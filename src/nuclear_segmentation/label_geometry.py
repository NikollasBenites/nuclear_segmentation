"""Bounding boxes of objects on the scientific label grid."""
import numpy as np


def selected_bounds(data, selected: int):
    """Half-open bounds of a label, using at most one plane of coordinates."""
    if data.ndim == 2:
        coordinates = np.argwhere(data == selected)
        if coordinates.size == 0:
            return None
        return coordinates.min(axis=0), coordinates.max(axis=0) + 1
    minimum = np.full(data.ndim, np.iinfo(np.int64).max, dtype=int)
    maximum = np.full(data.ndim, -1, dtype=int)
    for index in range(data.shape[0]):
        coordinates = np.argwhere(data[index] == selected)
        if coordinates.size == 0:
            continue
        local_min = np.concatenate(([index], coordinates.min(axis=0)))
        local_max = np.concatenate(([index + 1], coordinates.max(axis=0) + 1))
        minimum = np.minimum(minimum, local_min)
        maximum = np.maximum(maximum, local_max)
    return None if maximum[0] < 0 else (minimum, maximum)


def measured_bounds(properties, label, shape):
    columns = [f"{edge}_{axis}" for edge in ("min", "max") for axis in "zyx"]
    if properties is None or label not in properties.index or not all(c in properties for c in columns):
        return None
    limits = properties.loc[label, columns].to_numpy(dtype=float)
    low, high = limits[:3], limits[3:] + 1
    if (np.all(np.isfinite(limits)) and np.all(limits == np.floor(limits))
            and np.all((0 <= low) & (low < high) & (high <= shape))):
        return low.astype(int), high.astype(int)
    return None
