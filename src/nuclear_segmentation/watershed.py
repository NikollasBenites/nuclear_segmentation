"""Calibrated marker-controlled watershed on a local three-dimensional support."""
import numpy as np
from scipy import ndimage


def partition(target, markers, spacing):
    """Positive compact markers retain their IDs. Unseeded support returns zero."""
    from skimage.segmentation import watershed

    spacing = np.asarray(spacing, dtype=float)
    if target.ndim != 3 or target.dtype != bool or markers.shape != target.shape:
        raise ValueError("Expected a 3D target support and markers on the same grid.")
    if spacing.shape != (3,) or not np.all(np.isfinite(spacing) & (spacing > 0)):
        raise ValueError("Expected positive finite physical ZYX spacing.")
    if markers.dtype != np.int32 or np.any(markers < 0) or np.any(markers[~target]):
        raise ValueError("Markers must be nonnegative int32 values inside the target.")
    distance = ndimage.distance_transform_edt(target, sampling=spacing)
    regions = watershed(-distance, markers=markers, mask=target, connectivity=1)
    residual = target & (regions == 0)
    _, components = ndimage.label(residual)
    return regions, components
