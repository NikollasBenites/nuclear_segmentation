"""Voxel-wise DAPI/MAP2 matching using DAPI-normalized overlap fractions."""
from collections import Counter, defaultdict
import numpy as np
import pandas as pd

COLORS = {'MATCH': (0.,1.,0.,1.), 'AMBIGUOUS': (1.,1.,0.,1.), 'NO_MATCH': (1.,0.,0.,1.)}


def thresholds(match_min=.5, second_max=.2):
    values=np.asarray([match_min,second_max],float)
    if not np.isfinite(values).all() or not 0 < values[0] <= 1 or not 0 <= values[1] <= 1:
        raise ValueError('Match fraction must be in (0,1]; runner-up maximum must be in [0,1].')
    return dict(match_min=float(values[0]),second_match_max=float(values[1]),
                method='voxel_overlap_v1',denominator='entire DAPI label',
                tie_break='smallest MAP2 ID',centroid_or_proximity_used=False)


def overlap_pairs(dapi,map2):
    """Process planes independently; sparse IDs never allocate a max-ID matrix."""
    if dapi.shape!=map2.shape or dapi.ndim!=3:raise ValueError('Masks must share a ZYX grid.')
    counts=Counter()
    for a,b in zip(dapi,map2):
        inside=(a>0)&(b>0)
        if not inside.any():continue
        pairs,n=np.unique(np.column_stack((a[inside],b[inside])),axis=0,return_counts=True)
        counts.update({(int(i),int(j)):int(v) for (i,j),v in zip(pairs,n)})
    return counts


def classify(dapi_volumes,map2_volumes,voxel_volume,pairs,match_min=.5,second_max=.2):
    settings=thresholds(match_min,second_max)
    by_dapi=defaultdict(list)
    for (a,b),n in pairs.items():by_dapi[a].append((b,n))
    rows=[]
    for a,volume in dapi_volumes.items():
        n=int(round(volume/voxel_volume));ranked=sorted(by_dapi[a],key=lambda p:(-p[1],p[0]))
        b,intersection=ranked[0] if ranked else (None,0)
        second,second_n=ranked[1] if len(ranked)>1 else (None,0)
        first_fraction=intersection/n;second_fraction=second_n/n
        status=('NO_MATCH' if not intersection else 'MATCH' if first_fraction>=settings['match_min'] and second_fraction<=settings['second_match_max'] else 'AMBIGUOUS')
        nb=int(round(map2_volumes[b]/voxel_volume)) if b is not None else 0
        rows.append(dict(dapi_id=int(a),auto_class=status,best_map2_id=b,second_map2_id=second,
            dapi_volume_um3=float(volume),best_map2_volume_um3=map2_volumes.get(b),
            overlap_voxels=intersection,overlap_volume_um3=intersection*voxel_volume,
            best_dapi_fraction=first_fraction,second_dapi_fraction=second_fraction,
            total_dapi_fraction=sum(v for _,v in ranked)/n,overlapping_map2_count=len(ranked),
            iou=intersection/(n+nb-intersection) if intersection else 0.,
            dice=2*intersection/(n+nb) if intersection else 0.))
    return rows,settings


def plot_automatic(rows,sample_id):
    from matplotlib.figure import Figure
    table=pd.DataFrame(rows);fig=Figure(figsize=(13,4));axes=fig.subplots(1,3)
    groups={key:table.loc[table.auto_class.eq(key),'dapi_volume_um3'].to_numpy() for key in COLORS}
    bins=np.histogram_bin_edges(table.dapi_volume_um3.to_numpy(),bins='auto')
    n=len(table)
    for i,(name,values) in enumerate(groups.items()):
        color=COLORS[name];fraction=len(values)/n
        axes[0].bar(i,fraction,color=color,edgecolor='black');axes[0].text(i,fraction+.015,f'{len(values)} ({fraction:.1%})',ha='center',fontsize=8)
        if len(values):
            axes[1].hist(values,bins=bins,weights=np.ones(len(values))/len(values),color=color,alpha=.55,label=name,edgecolor='black')
            axes[2].step(np.sort(values),np.arange(1,len(values)+1)/len(values),where='post',color=color,label=name)
    axes[0].set_xticks(range(3),list(COLORS),rotation=15);axes[0].set_ylim(0,1.15);axes[0].set_ylabel('Fraction of all loaded DAPI objects')
    for ax in axes[1:]:ax.set_xlabel('DAPI volume (µm³)');ax.legend(fontsize=8)
    axes[1].set_ylabel('Fraction within group');axes[2].set_ylabel('Cumulative fraction');axes[2].set_ylim(0,1.02)
    fig.suptitle(sample_id+' — automatic overlap classification (before manual changes)');fig.tight_layout()
    return fig
