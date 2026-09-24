"""Manual DAPI-to-MAP2 associations and descriptive nuclear volume comparisons."""
import copy
import json
from pathlib import Path
from datetime import datetime
from collections import Counter
import numpy as np
import pandas as pd
import tifffile
from .manual_review import mask_signature
from .core import read_voxel_spacing_um


def load_labels(path, fallback=None):
    with tifffile.TiffFile(path) as tif:
        series=tif.series[0]
        if any(getattr(page, "keyframe", page).photometric == tifffile.PHOTOMETRIC.RGB for page in series.pages):
            raise ValueError('RGB images are not instance label masks.')
        if series.axes not in {'ZYX', 'QYX', 'IYX'}:
            raise ValueError('Expected a single-channel ZYX mask stack; found axes ' + series.axes)
        data=series.asarray()
    if data.ndim != 3 or data.dtype.kind not in 'ui' or (data < 0).any():
        raise ValueError('Masks must be a nonnegative integer ZYX label TIFF, not RGB or a multichannel image.')
    spacing, source=read_voxel_spacing_um(path, fallback)
    return data, spacing, source


from .map2_split import SplitMixin


class Matching(SplitMixin):
    def __init__(self, dapi_path, map2_path, fallback=None):
        self.paths=[str(Path(p).resolve()) for p in (dapi_path,map2_path)]
        self.dapi,self.spacing,self.calibration_source=load_labels(dapi_path,fallback)
        self.map2,other_spacing,_=load_labels(map2_path,fallback)
        if self.dapi.shape != self.map2.shape or not np.allclose(self.spacing,other_spacing):
            raise ValueError('DAPI and MAP2 masks must share shape and XYZ calibration. Register/crop them consistently first.')
        self.signatures=[mask_signature(a) for a in (self.dapi,self.map2)]
        self.volumes=[]
        for a in (self.dapi,self.map2):
            labels,counts=np.unique(a,return_counts=True)
            self.volumes.append({int(i):int(n)*float(np.prod(self.spacing)) for i,n in zip(labels,counts) if i})
        if not all(self.volumes):raise ValueError('Each segmentation must contain at least one nonzero label.')
        self.source_paths=self.paths[:]
        self.split_log=[];self.split_original=None
        self.auto_rows=[]; self.auto_settings=None; self._overlap_pairs=None
        self.records={};self.history=[];self.sample_id=Path(dapi_path).stem;self.animal_id=''

    def set(self, dapi_id, status, map2_id=None, comment=''):
        dapi_id=int(dapi_id)
        if dapi_id not in self.volumes[0]:raise ValueError('Select an existing DAPI object; 0 is background.')
        if status not in {'Matched','Unmatched','Uncertain','Not reviewed'}:raise ValueError('Invalid status.')
        if status=='Matched':
            if map2_id is None or int(map2_id) not in self.volumes[1]:raise ValueError('Select an existing MAP2 object.')
            map2_id=int(map2_id)
        else:map2_id=None
        self.history.append((dapi_id,copy.deepcopy(self.records.get(dapi_id))))
        if status=='Not reviewed':self.records.pop(dapi_id,None)
        else:self.records[dapi_id]=dict(status=status,map2_id=map2_id,comment=str(comment),source='manual')

    def undo(self):
        if self.history:
            label,record=self.history.pop()
            if label == '__map2_split__':
                self.undo_split(record)
            elif label == '__automatic_batch__':
                self.records,self.auto_rows,self.auto_settings=record
            elif record is None:self.records.pop(label,None)
            else:self.records[label]=record

    def table(self):
        counts=Counter(r['map2_id'] for r in self.records.values() if r['status']=='Matched')
        rows=[]
        for label,volume in self.volumes[0].items():
            r=self.records.get(label,dict(status='Not reviewed',map2_id=None,comment=''))
            partner=r['map2_id'];n=counts.get(partner,0)
            rows.append(dict(sample_id=self.sample_id,animal_id=self.animal_id,dapi_id=label,
                map2_id=partner,status=r['status'],dapi_volume_um3=volume,
                map2_volume_um3=self.volumes[1].get(partner),dapi_per_map2=n,
                multiple_association=n>1,comment=r['comment'],decision_source=r.get('source','manual' if label in self.records else 'none')))
        table = pd.DataFrame(rows)
        table['map2_id'] = table['map2_id'].astype('Int64')
        return table

    def run_automatic(self, match_min=.5, second_max=.2):
        from .automatic_matching import overlap_pairs, classify, thresholds
        thresholds(match_min,second_max)
        if self._overlap_pairs is None:
            self._overlap_pairs=overlap_pairs(self.dapi,self.map2)
        rows,settings=classify(self.volumes[0],self.volumes[1],float(np.prod(self.spacing)),
                               self._overlap_pairs,match_min,second_max)
        self.history.append(('__automatic_batch__',copy.deepcopy((self.records,self.auto_rows,self.auto_settings))))
        for row in rows:
            label=row['dapi_id'];old=self.records.get(label)
            if old and old.get('source','manual')=='manual':continue
            status={'MATCH':'Matched','AMBIGUOUS':'Uncertain','NO_MATCH':'Unmatched'}[row['auto_class']]
            self.records[label]=dict(status=status,map2_id=row['best_map2_id'] if status=='Matched' else None,
                                    comment='',source='automatic')
        self.auto_rows=rows;self.auto_settings=settings
        return rows

    def groups(self, one_to_one=False):
        table=self.table();matched=table.status.eq('Matched')
        if one_to_one:matched &= table.dapi_per_map2.eq(1)
        return {'Matched':table.loc[matched,'dapi_volume_um3'].to_numpy(),
                'Unmatched':table.loc[table.status.eq('Unmatched'),'dapi_volume_um3'].to_numpy()}

    def summary(self, one_to_one=False):
        rows=[]
        for status,values in self.groups(one_to_one).items():
            q=np.percentile(values,[25,50,75]) if len(values) else [np.nan]*3
            rows.append(dict(sample_id=self.sample_id,animal_id=self.animal_id,status=status,n=len(values),
                             q25_um3=q[0],median_um3=q[1],q75_um3=q[2],iqr_um3=q[2]-q[0],
                             one_to_one_only=one_to_one))
        return pd.DataFrame(rows)

    def figure(self, one_to_one=False, proportions=True):
        from matplotlib.figure import Figure
        fig=Figure(figsize=(10,4));axes=fig.subplots(1,2)
        groups=self.groups(one_to_one);nonempty=[v for v in groups.values() if len(v)]
        bins=np.histogram_bin_edges(np.concatenate(nonempty),bins='auto') if nonempty else np.array([0,1])
        for (label,values),color in zip(groups.items(),['#21918c','#440154']):
            if not len(values):continue
            weights=np.ones(len(values))/len(values) if proportions else None
            axes[0].hist(values,bins=bins,weights=weights,alpha=.55,label=f'{label} (n={len(values)})',color=color)
            x=np.sort(values);axes[1].step(x,np.arange(1,len(x)+1)/len(x),where='post',label=label,color=color)
        for ax in axes:
            ax.set_xlabel('DAPI mask volume (µm³)')
            if nonempty:ax.legend()
            else:ax.text(.5,.5,'No reviewed Matched / Unmatched objects',ha='center',transform=ax.transAxes)
        axes[0].set_ylabel('Fraction of group' if proportions else 'Object count')
        axes[1].set_ylabel('Cumulative fraction');axes[1].set_ylim(0,1.02)
        fig.suptitle(self.sample_id + (' — one-to-one matches only' if one_to_one else ''))
        fig.tight_layout();return fig

    def save(self, parent, one_to_one=False, proportions=True):
        folder=Path(parent)/('dapi_matching_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        folder.mkdir()
        record=dict(schema_version=3,automatic_results=self.auto_rows,automatic_settings=self.auto_settings,paths=self.paths,mask_sha256=self.signatures,
                    spacing_zyx_um=list(self.spacing),sample_id=self.sample_id,animal_id=self.animal_id,
                    records={str(k):v for k,v in self.records.items()},
                    alignment_confirmed=True,one_to_one_only=one_to_one,proportions=proportions)
        record.update(self.split_save_fields(folder))
        (folder/'dapi_matching.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
        self.table().to_csv(folder/'dapi_matching.csv',index=False)
        self.summary(one_to_one).to_csv(folder/'dapi_volume_summary.csv',index=False)
        fig=self.figure(one_to_one,proportions);fig.savefig(folder/'dapi_volume_distributions.png',dpi=180)
        fig.savefig(folder/'dapi_volume_distributions.pdf')
        if self.auto_rows:
            from .automatic_matching import plot_automatic
            auto=pd.DataFrame(self.auto_rows)
            for col in ['best_map2_id','second_map2_id']:auto[col]=auto[col].astype('Int64')
            auto.to_csv(folder/'automatic_matching.csv',index=False)
            auto.groupby('auto_class')['dapi_volume_um3'].agg(['count','median','min','max']).to_csv(folder/'automatic_volume_summary.csv')
            counts=auto.auto_class.value_counts().reindex(['MATCH','AMBIGUOUS','NO_MATCH'],fill_value=0)
            pd.DataFrame({'count':counts,'fraction':counts/len(auto)}).to_csv(folder/'automatic_group_proportions.csv',index_label='auto_class')
            fig=plot_automatic(self.auto_rows,self.sample_id)
            fig.savefig(folder/'automatic_matching.png',dpi=180);fig.savefig(folder/'automatic_matching.pdf')
        return folder

    def restore(self, record, base_dir=None):
        if self.signatures!=record['mask_sha256'] or not np.allclose(self.spacing,record['spacing_zyx_um']):
            raise ValueError('Saved matching does not match these masks or their calibration.')
        self.restore_splits(record,base_dir)
        old=self.records;self.records={}
        try:
            for label,r in record['records'].items():
                self.set(int(label),r['status'],r.get('map2_id'),r.get('comment',''))
                if int(label) in self.records:
                    self.records[int(label)]['source']=r.get('source','manual')
        except Exception:
            self.records=old;self.history=[];raise
        self.auto_rows=record.get('automatic_results',[])
        self.auto_settings=record.get('automatic_settings')
        self.history=[];self.sample_id=record.get('sample_id','');self.animal_id=record.get('animal_id','')
