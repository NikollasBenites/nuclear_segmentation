"""Reference-guided proposals and independent verification of saved sparse transfers."""
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .label_geometry import selected_bounds
from .watershed import partition

OPERATION = "nuclear_guided_watershed"
METHOD = "negative_euclidean_distance_3d_face_connectivity"


def qualifying(candidates, threshold):
    if not np.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("Minimum seed overlap fraction must be finite and in (0, 1].")
    return sorted(int(c["id"]) for c in candidates if c["fraction"] >= threshold)


def candidate_records(result, parent_id):
    stats = result.statistics
    classes = {row.nuclear_id: row.auto_class for row in result.nuclei}
    return [dict(id=int(label), classification=classes[label], intersection=int(count),
                 total=int(stats.nuclear_voxels[label]), fraction=int(count) / int(stats.nuclear_voxels[label]))
            for (label, parent), count in stats.intersections.items() if parent == parent_id and count > 0]


def parent_box(labels, parent_id, expected, bounds):
    if bounds is not None:
        box = tuple(slice(int(a), int(b)) for a, b in zip(*bounds))
        if all(0 <= s.start < s.stop <= n for s, n in zip(box, labels.shape)):
            support = labels[box] == parent_id
            if expected > 0 and np.count_nonzero(support) == expected:
                # Extra padding can change the EDT at the edge of the box.
                tight = []
                for axis, outer in enumerate(box):
                    other_axes = tuple(i for i in range(3) if i != axis)
                    occupied = np.flatnonzero(support.any(axis=other_axes))
                    tight.append(slice(outer.start + int(occupied[0]), outer.start + int(occupied[-1]) + 1))
                return tuple(tight)
    bounds = selected_bounds(labels, parent_id)
    if bounds is None:
        raise ValueError("Parent geometry is unavailable. Return to Matching and rerun.")
    return tuple(slice(int(a), int(b)) for a, b in zip(*bounds))


@dataclass(frozen=True)
class Proposal:
    box: tuple
    labels: np.ndarray
    reference: np.ndarray
    parameters: dict


def propose(target_labels, reference_labels, parent_id, candidates, threshold, spacing,
            next_id, dtype, parent_count, bounds=None):
    """Partition a retained parent using overlap counts and the target history's next unused ID."""
    if (target_labels.ndim != 3 or target_labels.shape != reference_labels.shape or parent_id <= 0
            or any(np.dtype(d).kind not in "iu" for d in (target_labels.dtype, reference_labels.dtype, dtype))
            or int(parent_id) > int(np.iinfo(dtype).max)):
        raise ValueError("Expected integer label volumes on one ZYX grid and a representable parent ID.")
    seed_ids = qualifying(candidates, threshold)
    if len(seed_ids) < 2:
        raise ValueError("At least two seeds must qualify. Lower the seed threshold or inspect another parent.")
    if next_id <= 0 or next_id + len(seed_ids) - 1 > int(np.iinfo(dtype).max):
        raise ValueError(f"Insufficient unused label IDs in the {np.dtype(dtype)} editing volume.")
    box = parent_box(target_labels, parent_id, parent_count, bounds)
    target = target_labels[box] == parent_id
    reference = reference_labels[box]
    if np.count_nonzero(target) != parent_count:
        raise ValueError("Parent support differs from matching statistics. Rerun matching.")
    child_ids = list(range(next_id, next_id + len(seed_ids)))
    markers = np.zeros(target.shape, np.int32)
    runs = {}
    for marker, label in enumerate(seed_ids, 1):
        points = np.flatnonzero(target & (reference == label))
        if not points.size:
            raise ValueError("A qualifying seed has disappeared. Rerun matching.")
        markers.flat[points] = marker
        starts = np.r_[0, np.flatnonzero(np.diff(points) != 1) + 1]
        runs[str(label)] = np.column_stack((points[starts], np.diff(np.r_[starts, points.size]))).tolist()
    regions, components = partition(target, markers, spacing)
    counts = np.bincount(regions[target], minlength=len(child_ids) + 1).tolist()
    voxel = float(np.prod(spacing))
    params = dict(method=METHOD, parent_id=int(parent_id), candidates=candidates, threshold=float(threshold),
                  seed_ids=seed_ids, child_ids=child_ids, seed_runs=runs, spacing=list(map(float, spacing)),
                  box=[[s.start, s.stop] for s in box], parent_voxels=int(parent_count),
                  parent_volume_um3=int(parent_count) * voxel, child_voxels=counts[1:],
                  child_volumes_um3=[v * voxel for v in counts[1:]], residual_components=int(components),
                  residual_voxels=counts[0], residual_volume_um3=counts[0] * voxel, manually_refined=False)
    labels = np.array([parent_id, *child_ids], dtype=dtype)[regions]
    labels[~target] = 0
    validate_partition(target, labels, params)
    seed_labels = np.asarray([0, *seed_ids], dtype=reference.dtype)[markers]
    labels.setflags(write=False)
    seed_labels.setflags(write=False)
    return Proposal(box, labels, seed_labels, params)


def validate_live_seeds(proposal, target_labels, reference_labels, candidates, spacing):
    """Check the saved evidence against the current complete retained pair at acceptance."""
    p = proposal.parameters
    if p["candidates"] != candidates or list(spacing) != p["spacing"]:
        raise ValueError("Seed statistics or calibration changed. Regenerate the watershed.")
    support = target_labels[proposal.box] == p["parent_id"]
    reference = reference_labels[proposal.box]
    ids, counts = np.unique(reference[support], return_counts=True)
    if {int(i): int(n) for i, n in zip(ids, counts) if i} != {c["id"]: c["intersection"] for c in candidates}:
        raise ValueError("Candidate intersections differ from the retained reference.")
    for seed, child in zip(p["seed_ids"], p["child_ids"], strict=True):
        actual = support & (reference == seed)
        if (not np.array_equal(proposal.reference == seed, actual)
                or not np.all(proposal.labels[actual] == child)):
            raise ValueError("Nuclear seed evidence differs from the retained source.")
        for start, length in p["seed_runs"][str(seed)]:
            if not actual.flat[start:start + length].all():
                raise ValueError("Saved seed locations differ from the retained source.")
    validate_partition(support, proposal.labels, p)


def validate_partition(target, labels, p):
    """Verify volume, residual connectivity and each recorded seed intersection on replay."""
    candidates = p["candidates"]
    ids = [c["id"] for c in candidates]
    seeds, children = p["seed_ids"], p["child_ids"]
    parent = p["parent_id"]
    spacing = np.asarray(p["spacing"])
    if (p["method"] != METHOD or p["manually_refined"] is not False or len(set(ids)) != len(ids)
            or any(type(i) is not int or i <= 0 for i in [parent, *ids, *children])
            or any(type(c["total"]) is not int or type(c["intersection"]) is not int
                   or not 0 < c["intersection"] <= c["total"] or c["fraction"] != c["intersection"] / c["total"]
                   for c in candidates)
            or seeds != qualifying(candidates, p["threshold"]) or len(seeds) < 2
            or set(p["seed_runs"]) != set(map(str, seeds))
            or len(children) != len(seeds) or len(set(children)) != len(children) or parent in children
            or spacing.shape != (3,) or not np.all(np.isfinite(spacing) & (spacing > 0))):
        raise ValueError("Invalid guided watershed provenance.")
    voxel = float(np.prod(spacing))
    residual = target & (labels == parent)
    components, count = ndimage.label(target)
    seeded = set()
    totals = {c["id"]: c["intersection"] for c in candidates}
    for seed, child in zip(seeds, children, strict=True):
        total = end = 0
        child_components = set()
        for start, length in p["seed_runs"][str(seed)]:
            if (type(start) is not int or type(length) is not int or start < end or length <= 0
                    or start + length > labels.size or not np.all(labels.flat[start:start + length] == child)):
                raise ValueError("Saved seed support disagrees with the proposed child.")
            end = start + length
            total += length
            child_components.update(np.unique(components.flat[start:end]))
        if set(np.unique(components[labels == child])) != child_components:
            raise ValueError("A child extends into a component without its seed.")
        seeded.update(child_components)
        if total != totals[seed]:
            raise ValueError("Seed evidence differs from the overlap count.")
    n = int(target.sum())
    child_counts = [int(np.count_nonzero(labels == child)) for child in children]
    r = int(residual.sum())
    if (not np.array_equal(labels != 0, target) or not np.array_equal(
            residual, target & ~np.isin(components, list(seeded)))
            or n != p["parent_voxels"] or n != sum(child_counts) + r or min(child_counts) < 1
            or p["parent_volume_um3"] != n * voxel or p["child_voxels"] != child_counts
            or p["child_volumes_um3"] != [v * voxel for v in child_counts]
            or p["residual_components"] != count - len(seeded) or p["residual_voxels"] != r
            or p["residual_volume_um3"] != r * voxel):
        raise ValueError("Guided watershed volume or seedless component accounting differs.")


def validate_step(working, step, counts):
    p = step["parameters"]
    parent = p["parent_id"]
    box = tuple(slice(a, b) for a, b in p["box"])
    if (len(box) != 3 or any(not 0 <= s.start < s.stop <= n for s, n in zip(box, working.shape))
            or counts.get(parent) != p["parent_voxels"] or any(counts.get(i) for i in p["child_ids"])
            or not np.all(step["before"] == parent)):
        raise ValueError("Guided watershed must only partition its complete parent into unused IDs.")
    support = working[box] == parent
    if int(support.sum()) != counts[parent]:
        raise ValueError("Saved bounds omit part of the parent.")
    local = np.where(support, working[box], 0)
    coords = np.unravel_index(step["indexes"], working.shape)
    local[tuple(axis - s.start for axis, s in zip(coords, box))] = step["after"]
    validate_partition(support, local, p)
