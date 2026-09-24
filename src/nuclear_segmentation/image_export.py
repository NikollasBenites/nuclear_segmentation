"""Calibrated float32 OME-TIFF export with per-channel provenance."""
import json
from pathlib import Path
import numpy as np
import tifffile


def export_images(path, arrays, names, spacing, provenance=None, protected_paths=()):
    path = Path(path)
    if not str(path).lower().endswith(('.ome.tif', '.ome.tiff')):
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
    if len(shape) != 3 or any(a.shape != shape for a in arrays):
        raise ValueError('All selected images must have the same ZYX shape.')
    spacing = np.asarray(spacing, dtype=float)
    if spacing.shape != (3,) or not np.isfinite(spacing).all() or np.any(spacing <= 0):
        raise ValueError('Expected positive Z, Y, X calibration in micrometers.')
    if any(not np.isfinite(a).all() for a in arrays):
        raise ValueError('Images contain nonfinite intensities.')
    meta = dict(axes='CZYX', Channel={'Name': list(names)},
                PhysicalSizeZ=float(spacing[0]), PhysicalSizeY=float(spacing[1]),
                PhysicalSizeX=float(spacing[2]), PhysicalSizeZUnit='µm',
                PhysicalSizeYUnit='µm', PhysicalSizeXUnit='µm')
    record = dict(format='OME-TIFF', axes='CZYX', shape=[len(arrays), *shape],
                  dtype='float32', channel_names=list(names), voxel_spacing_zyx_um=spacing.tolist(),
                  channels=provenance or [], display_settings_applied=False)
    serialized = json.dumps(record, indent=2, allow_nan=False)
    # Write planes incrementally; avoid allocating a second multichannel volume.
    def planes():
        for a in arrays:
            for plane in a:
                out = np.asarray(plane, dtype=np.float32)
                if not np.isfinite(out).all():
                    raise ValueError('Intensity values exceed float32 range.')
                yield out
    created = False
    try:
        with path.open('xb') as handle:
            created = True
            tifffile.imwrite(handle, planes(), shape=(len(arrays), *shape), dtype=np.float32,
                             ome=True, metadata=meta, photometric='minisblack',
                             bigtiff=len(arrays)*np.prod(shape)*4 > 2**32 - 2**25)
        with sidecar.open('x', encoding='utf-8') as handle:
            handle.write(serialized)
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    return path


def export_layers(path, layers, spacing, provenance=None, protected_paths=()):
    from .runtime import validate_roi_geometry
    if not layers:
        raise ValueError('Select at least one image layer.')
    for layer in layers:
        if getattr(layer, '_type_string', '') != 'image' or layer.rgb:
            raise ValueError('Select grayscale image layers only; export masks separately.')
        validate_roi_geometry(layer, spacing, layers[0].data.shape)
    return export_images(path, [l.data for l in layers], [l.name for l in layers],
                         spacing, provenance, protected_paths)
