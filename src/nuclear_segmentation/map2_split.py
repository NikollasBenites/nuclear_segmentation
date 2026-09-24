"""Previewable marker-controlled watershed matching the DAPI-seeded console workflow."""
import copy
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from scipy import ndimage as ndi
from .manual_review import mask_signature


def write_mask(path, data, spacing):
    import tifffile
    tifffile.imwrite(path, data, ome=True, photometric='minisblack', metadata={
        'axes':'ZYX', 'PhysicalSizeZ':spacing[0], 'PhysicalSizeY':spacing[1],
        'PhysicalSizeX':spacing[2], 'PhysicalSizeZUnit':'µm',
        'PhysicalSizeYUnit':'µm', 'PhysicalSizeXUnit':'µm'})


class SplitMixin:
    def split_partners(self, parent):
        return sorted(i for i,r in self.records.items()
                      if r['status']=='Matched' and r['map2_id']==parent)

    def preview_split(self, parent, seed_min_fraction=.5):
        parent=int(parent)
        if parent not in self.volumes[1]:raise ValueError('Select an existing MAP2 object.')
        if not np.isfinite(seed_min_fraction) or not 0<seed_min_fraction<=1:
            raise ValueError('Seed overlap fraction must be in (0,1].')
        ids,counts=np.unique(self.dapi[self.map2==parent],return_counts=True)
        voxel=float(np.prod(self.spacing));overlaps=[]
        for d,n in zip(ids,counts):
            if not d:continue
            d=int(d);fraction=int(n)/int(round(self.volumes[0][d]/voxel))
            overlaps.append(dict(original_map2_id=parent,dapi_id=d,dapi_volume_um3=self.volumes[0][d],
                overlap_voxels=int(n),overlap_volume_um3=int(n)*voxel,
                fraction_dapi_inside_map2=fraction,valid_split_seed=fraction>=seed_min_fraction))
        partners=[r['dapi_id'] for r in overlaps if r['valid_split_seed']]
        if len(partners)<2:raise ValueError('At least two DAPI nuclei must meet the split seed overlap fraction.')
        last=max(self.volumes[1])
        if last+len(partners)>2147483647:raise ValueError('Not enough available label IDs below 2^31.')
        target=self.map2==parent
        locations=np.where(target)
        box=tuple(slice(int(v.min()),int(v.max())+1) for v in locations)
        local=target[box];nuclei=self.dapi[box]
        children={d:last+i+1 for i,d in enumerate(partners)}
        seeds=np.zeros(local.shape,np.uint32)
        for d,child in children.items():
            overlap=local & (nuclei==d)
            if not overlap.any():raise ValueError(f'DAPI {d} does not overlap this MAP2. Correct the association before splitting.')
            seeds[overlap]=child
        components,n=ndi.label(local)
        for c in range(1,n+1):
            if not ((components==c)&(seeds>0)).any():
                raise ValueError('A disconnected MAP2 component has no valid DAPI seed. Review the mask before splitting.')
        from skimage.segmentation import watershed
        distance=ndi.distance_transform_edt(local,sampling=self.spacing)
        proposal=watershed(-distance,markers=seeds,mask=local)
        data=np.zeros(self.map2.shape,np.uint32);data[box]=proposal
        return dict(parent=parent,children=children,data=data,
                    proposal_signature=mask_signature(data),
                    source_signature=mask_signature(self.map2),
                    partners=partners,seed_min_fraction=float(seed_min_fraction),overlaps=overlaps,
                    records_snapshot=copy.deepcopy(self.records))

    def accept_split(self, preview, edited_data):
        parent=preview['parent']
        if mask_signature(self.map2)!=preview['source_signature'] or self.records!=preview['records_snapshot']:
            raise ValueError('Masks or associations changed. Generate a new split preview.')
        data=np.asarray(edited_data);target=self.map2==parent
        children=preview['children'];allowed=set(children.values())
        if data.shape!=self.map2.shape or data.dtype.kind not in 'ui' or (data<0).any():
            raise ValueError('Split preview must be an integer label volume on the original grid.')
        if not np.array_equal(data>0,target) or set(np.unique(data[target]))!=allowed:
            raise ValueError('Keep the full original MAP2 volume, no voxels outside it, and all proposed child IDs. Paint between child labels; do not erase.')
        for d,child in children.items():
            if not np.all(data[target & (self.dapi==d)]==child):
                raise ValueError(f'Keep DAPI {d} seed voxels assigned to child MAP2 {child}.')
        corrected=self.map2.astype(np.uint32,copy=True);corrected[target]=data[target]
        values,counts=np.unique(corrected,return_counts=True)
        volumes={int(i):int(n)*float(np.prod(self.spacing)) for i,n in zip(values,counts) if i}
        records=copy.deepcopy(self.records)
        for d,child in children.items():
            old=records.get(d)
            if old and old['status']=='Matched' and old['map2_id']==parent:
                records[d].update(map2_id=child,source='manual')
        for r in records.values():
            if r['status']=='Matched' and r['map2_id']==parent:
                r.update(status='Uncertain',map2_id=None,source='manual',
                         comment=r.get('comment','')+' [Review required: parent split; this DAPI did not qualify as a seed.]')
        auto_rows=[];settings=self.auto_settings
        if settings:
            from .automatic_matching import overlap_pairs,classify
            auto_rows,_=classify(self.volumes[0],volumes,float(np.prod(self.spacing)),
                                overlap_pairs(self.dapi,corrected),settings['match_min'],settings['second_match_max'])
            for row in auto_rows:
                d=row['dapi_id'];old=records.get(d)
                # Recalculate existing automatic decisions; preserve manual and unreviewed records.
                if old and old.get('source')=='automatic':
                    status={'MATCH':'Matched','AMBIGUOUS':'Uncertain','NO_MATCH':'Unmatched'}[row['auto_class']]
                    records[d].update(status=status,map2_id=row['best_map2_id'] if status=='Matched' else None)
        snapshot=(self.map2,self.volumes[1],copy.deepcopy(self.records),self.auto_rows,
                  copy.deepcopy(self.split_log),self.split_original,self.signatures[:])
        self.history.append(('__map2_split__',snapshot))
        if self.split_original is None:self.split_original=self.map2.copy()
        self.map2=corrected;self.volumes[1]=volumes;self.records=records;self.auto_rows=auto_rows
        self.signatures[1]=mask_signature(corrected);self._overlap_pairs=None
        self.split_log.append(dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),
            parent_map2_id=parent,dapi_to_child_map2={str(d):c for d,c in children.items()},
            method='marker_controlled_watershed_negative_MAP2_distance_local_bbox',
            seed_min_fraction=preview['seed_min_fraction'],seed_overlaps=preview['overlaps'],
            manually_edited=mask_signature(data)!=preview['proposal_signature'],
            previous_parent_links={str(d):r for d,r in preview['records_snapshot'].items() if r.get('map2_id')==parent},
            source_sha256=preview['source_signature'],result_sha256=self.signatures[1],
            volume_conserved=True))

    def undo_split(self, snapshot):
        (self.map2,self.volumes[1],self.records,self.auto_rows,self.split_log,
         self.split_original,self.signatures)=snapshot
        self._overlap_pairs=None

    def split_save_fields(self, folder):
        if not self.split_log:return {}
        folder=Path(folder).resolve()
        names=['dapi_masks.ome.tif','map2_corrected.ome.tif']
        write_mask(folder/names[0],self.dapi,self.spacing)
        write_mask(folder/names[1],self.map2,self.spacing)
        import pandas as pd
        pd.DataFrame([r for event in self.split_log for r in event['seed_overlaps']]).to_csv(folder/'split_overlaps.csv',index=False)
        pd.DataFrame([dict(original_map2_id=e['parent_map2_id'],output_map2_id=c,dapi_id=int(d))
                      for e in self.split_log for d,c in e['dapi_to_child_map2'].items()]).to_csv(folder/'map2_splits.csv',index=False)
        backup='map2_before_splits.ome.tif'
        write_mask(folder/backup,self.split_original,self.spacing)
        return dict(paths=[str(folder/n) for n in names],relative_mask_paths=names,
                    original_input_paths=self.source_paths,split_history=self.split_log,
                    map2_before_splits=str(folder/backup),relative_map2_before_splits=backup,
                    map2_before_splits_sha256=mask_signature(self.split_original))

    def restore_splits(self,record,base_dir=None):
        log=record.get('split_history',[]);original=None
        if log:
            from .matching import load_labels
            path=Path(record['map2_before_splits'])
            if base_dir is not None:path=Path(base_dir)/record['relative_map2_before_splits']
            original,spacing,_=load_labels(path,self.spacing)
            if (original.shape!=self.map2.shape or not np.allclose(spacing,self.spacing)
                    or mask_signature(original)!=record['map2_before_splits_sha256']):
                raise ValueError('Original MAP2 backup does not match this split history.')
        self.split_log=copy.deepcopy(log);self.split_original=original
        self.source_paths=record.get('original_input_paths',self.paths)
