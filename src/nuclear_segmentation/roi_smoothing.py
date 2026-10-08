"""Binary tissue ROI smoothing; never smooth integer cell IDs directly."""
import numpy as np
from scipy.ndimage import gaussian_filter


def smooth_roi(data, sigma_xy=2.0, sigma_z=0.0):
    data = np.asarray(data)
    if data.ndim not in (2, 3):
        raise ValueError('ROI must be a 2D image or Z,Y,X stack.')
    labels = np.unique(data[data != 0])
    if len(labels) != 1:
        raise ValueError('Select a nonempty tissue ROI with one foreground label, not cell masks.')
    if not np.isfinite([sigma_xy, sigma_z]).all() or min(sigma_xy, sigma_z) < 0:
        raise ValueError('Sigma must be finite and nonnegative.')
    sigma = (sigma_z, sigma_xy, sigma_xy) if data.ndim == 3 else (sigma_xy, sigma_xy)
    mask = gaussian_filter((data != 0).astype(np.float32), sigma, mode='nearest') >= 0.5
    if not mask.any():
        raise ValueError('Smoothing removed the entire ROI. Reduce sigma.')
    return np.where(mask, labels[0], 0).astype(data.dtype)
