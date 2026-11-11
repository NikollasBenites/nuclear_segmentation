"""Calibrated float32 or rescaled uint16 TIFF export with provenance."""
import json
from pathlib import Path
import numpy as np
import tifffile


def export_images(path, arrays, names, spacing, provenance=None, protected_paths=(),
                  *, file_format='ome', pixel_type='float32'):
    if file_format not in ('ome', 'imagej'):
        raise ValueError('Expected export format: ome or imagej.')
    if pixel_type not in ('float32', 'uint16'):
        raise ValueError('Expected pixel type: float32 or uint16.')
    dtype = np.dtype(pixel_type)
    path = Path(path)
    if file_format == 'imagej':
        # Do not leave an OME extension on a non-OME file, including when the
        # user changes the format filter after entering the filename.
        for suffix in ('.ome.tiff', '.ome.tif'):
            if str(path).lower().endswith(suffix):
                path = Path(str(path)[:-len(suffix)] + '.tif')
                break
        if not str(path).lower().endswith(('.tif', '.tiff')):
            path = Path(str(path) + '.tif')
    elif not str(path).lower().endswith(('.ome.tif', '.ome.tiff')):
        if path.suffix.lower() in ('.tif', '.tiff'):
            path = path.with_suffix('')
        path = Path(str(path) + '.ome.tif')
    sidecar = Path(str(path) + '.json')
    protected = {Path(p).resolve() for p in protected_paths if p}
    if path.resolve() in protected or sidecar.resolve() in protected:
        raise ValueError('The source image must not be overwritten.')
    if path.exists() or sidecar.exists():
        raise ValueError('Choose a new filename; an export or its JSON already exists.')
    arrays = [np.asarray(a) for a in arrays]
    if not arrays or len(arrays) != len(names) or len(set(names)) != len(names):
        raise ValueError('Select image channels with unique names.')
    shape = arrays[0].shape
    if len(shape) != 3 or any(n == 0 for n in shape) or any(a.shape != shape for a in arrays):
        raise ValueError('All selected images must have the same ZYX shape.')
    spacing = np.asarray(spacing, dtype=float)
    if spacing.shape != (3,) or not np.isfinite(spacing).all() or np.any(spacing <= 0):
        raise ValueError('Expected positive Z, Y, X calibration in micrometers.')
    if any(not np.isfinite(a).all() for a in arrays):
        raise ValueError('Images contain nonfinite intensities.')
    conversion = None
    maxima = []
    if pixel_type == 'uint16':
        # One zero-anchored scale per channel across the entire Z volume.
        # Negative inputs are rejected instead of silently clipping them.
        if any(np.min(a) < 0 for a in arrays):
            raise ValueError('Rescaled uint16 export requires nonnegative intensities. '
                             'Use float32 for images with negative values.')
        maxima = [float(np.max(a)) for a in arrays]
        conversion = dict(
            method='per_channel_zero_to_volume_max',
            formula='round((input / channel_volume_max) * 65535)',
            scope='One scale per channel for the entire Z volume; no per-plane scaling.',
            zero_max_policy='All-zero channels remain zero.',
            rounding='nearest, ties to even',
            intended_use='Visualization and mask proofreading; intensities are rescaled.',
            channel_ranges=[dict(channel=name, input_range=[0.0, maximum],
                                 output_range=[0, 65535 if maximum > 0 else 0])
                            for name, maximum in zip(names, maxima)])
    meta = dict(axes='CZYX', Channel={'Name': list(names)},
                PhysicalSizeZ=float(spacing[0]), PhysicalSizeY=float(spacing[1]),
                PhysicalSizeX=float(spacing[2]), PhysicalSizeZUnit='µm',
                PhysicalSizeYUnit='µm', PhysicalSizeXUnit='µm')
    large = len(arrays) * int(np.prod(shape)) * dtype.itemsize > 2**32 - 2**25
    if file_format == 'imagej' and large:
        raise ValueError('ImageJ TIFF export is limited to approximately 4 GB. '
                         'Select OME-TIFF (BigTIFF) or export fewer channels.')
    output_shape = (len(arrays), *shape)
    options = dict(ome=True, bigtiff=large)
    axes = 'CZYX'
    if file_format == 'imagej':
        axes = 'ZCYX'
        output_shape = (shape[0], len(arrays), *shape[1:])
        meta = dict(axes=axes, spacing=float(spacing[0]), unit='um',
                    mode='composite', Labels=[name for _ in range(shape[0]) for name in names])
        options = dict(ome=False, imagej=True, bigtiff=False,
                       resolution=(1.0 / spacing[2], 1.0 / spacing[1]),
                       resolutionunit='NONE')
    record = dict(format='ImageJ TIFF' if file_format == 'imagej' else 'OME-TIFF',
                  axes=axes, shape=list(output_shape),
                  dtype=pixel_type, intensity_conversion=conversion, channel_names=list(names), voxel_spacing_zyx_um=spacing.tolist(),
                  channels=provenance or [], display_settings_applied=False)
    serialized = json.dumps(record, indent=2, allow_nan=False)
    # Write planes incrementally; avoid allocating a second multichannel volume.
    def planes():
        indices = ((c, z) for c in range(len(arrays)) for z in range(shape[0]))
        if file_format == 'imagej':
            # ImageJ expects channel to vary fastest within each Z plane.
            indices = ((c, z) for z in range(shape[0]) for c in range(len(arrays)))
        for c, z in indices:
            if pixel_type == 'uint16':
                if maxima[c] == 0:
                    out = np.zeros(shape[1:], dtype=np.uint16)
                else:
                    scaled = np.asarray(arrays[c][z], dtype=np.float64) / maxima[c]
                    out = np.rint(np.clip(scaled, 0.0, 1.0) * 65535).astype(np.uint16)
            else:
                out = np.asarray(arrays[c][z], dtype=np.float32)
                if not np.isfinite(out).all():
                    raise ValueError('Intensity values exceed float32 range.')
            yield out
    created = False
    try:
        with path.open('xb') as handle:
            created = True
            tifffile.imwrite(handle, planes(), shape=output_shape, dtype=dtype,
                             metadata=meta, photometric='minisblack', **options)
        with sidecar.open('x', encoding='utf-8') as handle:
            handle.write(serialized)
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    return path


def export_layers(path, layers, spacing, provenance=None, protected_paths=(),
                  *, file_format='ome', pixel_type='float32'):
    from .runtime import validate_roi_geometry
    if not layers:
        raise ValueError('Select at least one image layer.')
    for layer in layers:
        if getattr(layer, '_type_string', '') != 'image' or layer.rgb:
            raise ValueError('Select grayscale image layers only; export masks separately.')
        validate_roi_geometry(layer, spacing, layers[0].data.shape)
    return export_images(path, [l.data for l in layers], [l.name for l in layers],
                         spacing, provenance, protected_paths, file_format=file_format, pixel_type=pixel_type)
