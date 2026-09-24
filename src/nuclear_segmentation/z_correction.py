"""Multiplicative Z correction; independent of Cellpose normalization."""
import numpy as np
from scipy.ndimage import gaussian_filter1d

DEFAULTS = dict(percentile=99.0, sigma_planes=3.0, min_gain=0.5, max_gain=3.0,
                segmentation=True, measurement=False)


def validate_options(options):
    if set(options) - set(DEFAULTS):
        raise ValueError('Unknown Z correction option.')
    out = {**DEFAULTS, **options}
    for key in ('percentile', 'sigma_planes', 'min_gain', 'max_gain'):
        out[key] = float(out[key])
        if not np.isfinite(out[key]):
            raise ValueError('Z correction parameters must be finite.')
    if not 0 < out['percentile'] <= 100 or out['sigma_planes'] < 0:
        raise ValueError('Percentile must be in (0, 100]; sigma must be nonnegative.')
    if not 0 < out['min_gain'] <= out['max_gain']:
        raise ValueError('Gain bounds must satisfy 0 < minimum <= maximum.')
    for key in ('segmentation', 'measurement'):
        if type(out[key]) is not bool:
            raise ValueError(key + ' must be true or false.')
    return out


def correct_z(volume, options=None):
    """p99 of positive finite pixels, interpolate, smooth, median target, gain.

    Z must be axis 0. Empty planes do not define the reference profile. They
    remain zero after multiplication. Smoothing sigma is in planes, not um.
    """
    opts = validate_options(options or {})
    data = np.asarray(volume)
    if data.ndim != 3 or not all(data.shape):
        raise ValueError('Select a nonempty 3D ZYX image layer.')
    if not np.isfinite(data).all():
        raise ValueError('The source contains NaN or infinite intensities.')
    profile = np.full(len(data), np.nan)
    counts = []
    for z, plane in enumerate(data):
        positive = plane[plane > 0]
        counts.append(int(positive.size))
        if positive.size:
            profile[z] = np.percentile(positive, opts['percentile'])
    valid = np.isfinite(profile) & (profile > 0)
    if not valid.any():
        raise ValueError('No positive pixels are available for Z correction.')
    interpolated = np.interp(np.arange(len(data)), np.flatnonzero(valid), profile[valid])
    smooth = (gaussian_filter1d(interpolated, opts['sigma_planes'])
              if opts['sigma_planes'] else interpolated.copy())
    target = float(np.median(smooth))
    gain = np.clip(target / smooth, opts['min_gain'], opts['max_gain'])
    corrected = data.astype(np.float32, copy=True)
    corrected *= gain.astype(np.float32)[:, None, None]
    if not np.isfinite(corrected).all():
        raise ValueError('Corrected values exceed float32 range.')
    return corrected, dict(options=opts, positive_pixel_counts=counts,
                          observed_profile=[float(x) if np.isfinite(x) else None for x in profile],
                          smoothed_profile=smooth.tolist(), target=target, gain=gain.tolist())


def prepare_volumes(ns):
    """Route original/corrected images separately for inference and measurement."""
    original = ns['channel_volumes']
    ns['original_channel_volumes'] = dict(original)
    measured = dict(original)
    corrected, reports = {}, {}
    for name, options in ns.get('Z_CORRECTIONS', {}).items():
        if name not in original:
            raise ValueError('Z correction channel not found: ' + name)
        corrected[name], reports[name] = correct_z(original[name], options)
        if reports[name]['options']['measurement']:
            measured[name] = corrected[name]
    primary = ns['SEGMENTATION_CHANNEL']
    use_corrected = primary in reports and reports[primary]['options']['segmentation']
    ns['model_input_volume'] = corrected[primary] if use_corrected else original[primary]
    ns['channel_volumes'] = measured
    ns['z_corrected_volumes'] = corrected
    ns['z_correction_reports'] = reports
