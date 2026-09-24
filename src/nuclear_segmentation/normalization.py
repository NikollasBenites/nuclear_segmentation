"""ROI percentile statistics on the original segmentation channel (no Cellpose import)."""
from pathlib import Path
import hashlib
import numpy as np


def mask_digest(roi):
    return hashlib.sha256(np.ascontiguousarray(roi, dtype=np.uint8).tobytes()).hexdigest()


def source_identity(path, config):
    path = Path(path).resolve()
    stat = path.stat()
    channel = next(c for c in config['CHANNELS'] if c['name'] == config['SEGMENTATION_CHANNEL'])
    return dict(path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                channel_name=channel['name'], channel_index=int(channel['index']),
                axes_override=config['AXES_OVERRIDE'], time_index=config['TIME_INDEX'])


def compare_percentiles(volume, roi, lower=1., upper=99.):
    volume = np.asarray(volume)
    roi = np.asarray(roi) > 0
    if volume.ndim != 3 or roi.shape != volume.shape:
        raise ValueError('Image and ROI must share the same ZYX grid.')
    if not np.isfinite([lower, upper]).all() or not 0 <= lower < upper <= 100:
        raise ValueError('Percentiles must satisfy 0 <= lower < upper <= 100.')
    if not roi.any():
        raise ValueError('The MNTB ROI is empty.')
    if not np.isfinite(volume).all():
        raise ValueError('The image contains NaN or infinite values. Correct these before segmentation.')
    # Include genuine zero-intensity tissue voxels. Do not select only bright nuclei.
    tissue = volume[roi]
    roi_limits = np.percentile(tissue, [lower, upper]).tolist()
    full_limits = np.percentile(volume, [lower, upper]).tolist()
    if roi_limits[1] - roi_limits[0] <= 1e-3:
        raise ValueError('ROI percentile limits are identical or too close. Review the ROI or percentile range.')
    planes = np.flatnonzero(roi.any(axis=(1, 2)))
    return dict(percentiles=[float(lower), float(upper)], roi_lowhigh=roi_limits,
                full_lowhigh=full_limits, roi_voxels=int(roi.sum()), total_voxels=int(roi.size),
                outside_fraction=float(1 - roi.mean()), roi_zero_fraction=float(np.mean(tissue == 0)),
                full_zero_fraction=float(np.mean(volume == 0)),
                roi_first_z=int(planes[0]), roi_last_z=int(planes[-1]),
                roi_sha256=mask_digest(roi), shape_zyx=list(volume.shape),
                method='exact_numpy_percentile_linear', scope='all voxels inside reviewed 3D ROI; no guards',
                full_comparison='exact full-stack percentiles, not a replay of Cellpose subsampling or custom preprocessing')


def normalize_preview(volume, limits):
    low, high = np.asarray(limits, dtype=np.float64)
    if not np.isfinite(limits).all() or high - low <= 1e-3:
        return np.zeros(np.shape(volume), dtype=np.float32)
    result = np.array(volume, dtype=np.float32, copy=True)
    result -= low
    result /= high - low
    return result  # No clipping, no outside-ROI masking; matches lowhigh's affine mapping.


def validate_normalization_source(config, path, roi):
    record = config.get('CELLPOSE_ROI_NORMALIZATION')
    if record is None:
        return
    if config['CELLPOSE_NORMALIZE'] != record['cellpose_normalize']:
        raise ValueError('ROI normalization settings changed. Recalculate the preview or reset normalization.')
    if record['source'] != source_identity(path, config) or record['roi_sha256'] != mask_digest(roi):
        raise ValueError('ROI normalization belongs to a different image/channel/ROI. Recalculate and apply the preview, or use Reset normalization.')
