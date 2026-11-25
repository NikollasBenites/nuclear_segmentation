"""Sparse voxel-overlap matching shared by the legacy and integrated workspaces."""
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType

import numpy as np
import pandas as pd

COLORS = {'MATCH': (0.,1.,0.,1.), 'AMBIGUOUS': (1.,1.,0.,1.), 'NO_MATCH': (1.,0.,0.,1.)}


DEFAULT_MINIMUM_TOTAL = .05
NO_MATCH_REASONS = ("ZERO_OVERLAP", "BELOW_MINIMUM_OVERLAP")
# Before the total-overlap criterion each class had exactly one possible reason.
LEGACY_REASONS = {"NO_MATCH": "ZERO_OVERLAP", "MATCH": "OVERLAP_CRITERIA_MET",
                  "AMBIGUOUS": "BEST_OR_SECOND_OUTSIDE_LIMITS"}


@dataclass(frozen=True)
class MatchingSettings:
    match_min: float = .50
    second_max: float = .20
    minimum_total_overlap_fraction: float = DEFAULT_MINIMUM_TOTAL
    method: str = field(default="voxel_overlap_v1", init=False)
    denominator: str = field(default="entire retained nuclear label", init=False)
    ranking: str = field(default="descending intersection voxels, then ascending cytoplasmic ID", init=False)
    centroid_or_proximity_used: bool = field(default=False, init=False)

    def __post_init__(self):
        if not all(isfinite(v) for v in (self.match_min, self.second_max, self.minimum_total_overlap_fraction)):
            raise ValueError("Matching fractions must be finite.")
        if not (0 < self.match_min <= 1 and 0 <= self.second_max <= 1):
            raise ValueError("Match fraction must be in (0,1]; runner-up maximum must be in [0,1].")
        if not 0 <= self.minimum_total_overlap_fraction <= self.match_min:
            raise ValueError("Minimum total overlap must be between zero and the minimum best overlap.")

    def legacy_record(self):
        return dict(match_min=self.match_min, second_match_max=self.second_max,
                    minimum_total_overlap_fraction=self.minimum_total_overlap_fraction,
                    method=self.method, denominator='entire DAPI label',
                    tie_break='smallest MAP2 ID', centroid_or_proximity_used=False)


def thresholds(match_min=.5, second_max=.2, minimum_total_overlap_fraction=DEFAULT_MINIMUM_TOTAL):
    return MatchingSettings(float(match_min), float(second_max), float(minimum_total_overlap_fraction)).legacy_record()


def _count_intersections(counts, nuclear, cytoplasmic):
    inside = (nuclear > 0) & (cytoplasmic > 0)
    if inside.any():
        # Preserve exact IDs even when the two integer types promote to float64.
        pairs = np.rec.fromarrays((nuclear[inside], cytoplasmic[inside]), names="n,c")
        unique, sizes = np.unique(pairs, return_counts=True)
        counts.update({(int(n), int(c)): int(size) for (n, c), size in zip(unique, sizes)})


def overlap_pairs(dapi, map2):
    if dapi.shape != map2.shape or dapi.ndim != 3:
        raise ValueError("Masks must share a ZYX grid.")
    counts = Counter()
    for nuclear, cytoplasmic in zip(dapi, map2):
        _count_intersections(counts, nuclear, cytoplasmic)
    return counts


@dataclass(frozen=True)
class OverlapStatistics:
    population_identity: tuple
    voxel_volume_um3: float
    nuclear_voxels: Mapping[int, int]
    cytoplasmic_voxels: Mapping[int, int]
    intersections: Mapping[tuple[int, int], int]


def scan_overlaps(pair, progress):
    nuclear, cytoplasmic, intersections = Counter(), Counter(), Counter()
    depth = pair.grid.shape_zyx[0]
    last_percent = -1
    for z, (a, b) in enumerate(zip(pair.nuclear.labels, pair.cytoplasmic.labels, strict=True)):
        for plane, counts in ((a, nuclear), (b, cytoplasmic)):
            ids, sizes = np.unique(plane, return_counts=True)
            counts.update({int(i): int(size) for i, size in zip(ids, sizes) if i})
        _count_intersections(intersections, a, b)
        percent = 100 * (z + 1) // depth
        if percent != last_percent:
            progress(percent)
            last_percent = percent
    return OverlapStatistics(pair.population_identity, float(np.prod(pair.grid.spacing_zyx_um)),
                             MappingProxyType(nuclear), MappingProxyType(cytoplasmic),
                             MappingProxyType(intersections))


@dataclass(frozen=True, slots=True)
class NuclearOverlap:
    nuclear_id: int
    auto_class: str
    classification_reason: str
    best_cytoplasmic_id: int | None
    second_cytoplasmic_id: int | None
    nuclear_volume_um3: float
    best_cytoplasmic_volume_um3: float | None
    overlap_voxels: int
    overlap_volume_um3: float
    best_nuclear_fraction: float
    second_nuclear_fraction: float
    total_nuclear_fraction: float
    cytoplasmic_candidate_count: int
    iou: float
    dice: float

    @property
    def accepted_cytoplasmic_id(self):
        return self.best_cytoplasmic_id if self.auto_class == "MATCH" else None


def _classify_counts(nuclear, cytoplasmic, voxel_volume, intersections, settings):
    by_nucleus = defaultdict(list)
    for (nuclear_id, cytoplasmic_id), count in intersections.items():
        by_nucleus[nuclear_id].append((cytoplasmic_id, count))

    for nuclear_id, nuclear_size in nuclear.items():
        ranked = sorted(by_nucleus.get(nuclear_id, ()), key=lambda item: (-item[1], item[0]))
        best, overlap = ranked[0] if ranked else (None, 0)
        second, runner_up = ranked[1] if len(ranked) > 1 else (None, 0)
        first_fraction = overlap / nuclear_size
        second_fraction = runner_up / nuclear_size
        total_fraction = sum(count for _, count in ranked) / nuclear_size
        if total_fraction == 0:
            status, reason = "NO_MATCH", "ZERO_OVERLAP"
        elif total_fraction < settings.minimum_total_overlap_fraction:
            status, reason = "NO_MATCH", "BELOW_MINIMUM_OVERLAP"
        elif first_fraction >= settings.match_min and second_fraction <= settings.second_max:
            status, reason = "MATCH", "OVERLAP_CRITERIA_MET"
        else:
            status, reason = "AMBIGUOUS", "BEST_OR_SECOND_OUTSIDE_LIMITS"
        cytoplasmic_size = cytoplasmic[best] if best is not None else 0
        combined_size = nuclear_size + cytoplasmic_size
        yield NuclearOverlap(
            nuclear_id=int(nuclear_id), auto_class=status, classification_reason=reason,
            best_cytoplasmic_id=best, second_cytoplasmic_id=second,
            nuclear_volume_um3=nuclear_size * voxel_volume,
            best_cytoplasmic_volume_um3=cytoplasmic_size * voxel_volume if best is not None else None,
            overlap_voxels=overlap, overlap_volume_um3=overlap * voxel_volume,
            best_nuclear_fraction=first_fraction, second_nuclear_fraction=second_fraction,
            total_nuclear_fraction=total_fraction,
            cytoplasmic_candidate_count=len(ranked),
            iou=overlap / (combined_size - overlap), dice=2 * overlap / combined_size,
        )


def classify(dapi_volumes, map2_volumes, voxel_volume, pairs, match_min=.5, second_max=.2,
             minimum_total_overlap_fraction=DEFAULT_MINIMUM_TOTAL):
    """Legacy physical-volume API and DAPI/MAP2 output schema."""
    settings = MatchingSettings(float(match_min), float(second_max), float(minimum_total_overlap_fraction))
    nuclear, cyto = ({i: int(round(v / voxel_volume)) for i, v in volumes.items()}
                     for volumes in (dapi_volumes, map2_volumes))
    rows = []
    for r in _classify_counts(nuclear, cyto, voxel_volume, pairs, settings):
        rows.append(dict(dapi_id=r.nuclear_id, auto_class=r.auto_class, classification_reason=r.classification_reason,
                         best_map2_id=r.best_cytoplasmic_id,
                         second_map2_id=r.second_cytoplasmic_id, dapi_volume_um3=float(dapi_volumes[r.nuclear_id]),
                         best_map2_volume_um3=map2_volumes.get(r.best_cytoplasmic_id),
                         overlap_voxels=r.overlap_voxels, overlap_volume_um3=r.overlap_volume_um3,
                         best_dapi_fraction=r.best_nuclear_fraction, second_dapi_fraction=r.second_nuclear_fraction,
                         total_dapi_fraction=r.total_nuclear_fraction, overlapping_map2_count=r.cytoplasmic_candidate_count,
                         iou=r.iou, dice=r.dice))
    return rows, settings.legacy_record()


@dataclass(frozen=True, slots=True)
class CytoplasmicAssociations:
    matched_nuclei: int

    @property
    def multiple(self):
        return self.matched_nuclei > 1


@dataclass(frozen=True, eq=False)
class AutomaticMatchingResult:
    statistics: OverlapStatistics
    measurement_revisions: tuple[int, int]
    settings: MatchingSettings
    nuclei: tuple[NuclearOverlap, ...]
    associations: Mapping[int, CytoplasmicAssociations]
    class_counts: Mapping[str, int]
    no_match_reason_counts: Mapping[str, int]
    multiple_objects: int
    nuclei_in_multiple: int


def classify_population(stats, settings, revisions):
    rows = tuple(_classify_counts(stats.nuclear_voxels, stats.cytoplasmic_voxels,
                                  stats.voxel_volume_um3, stats.intersections, settings))
    links = Counter(r.best_cytoplasmic_id for r in rows if r.auto_class == "MATCH")
    associations = {i: CytoplasmicAssociations(links[i]) for i in stats.cytoplasmic_voxels}
    counts = Counter(r.auto_class for r in rows)
    reasons = Counter(r.classification_reason for r in rows if r.auto_class == "NO_MATCH")
    multiple = [n for n in links.values() if n > 1]
    return AutomaticMatchingResult(stats, revisions, settings, rows, MappingProxyType(associations),
                                   MappingProxyType({k: counts[k] for k in COLORS}),
                                   MappingProxyType({k: reasons[k] for k in NO_MATCH_REASONS}),
                                   len(multiple), sum(multiple))


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
