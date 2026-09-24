"""User presets are JSON data, never executable Python."""
from copy import deepcopy
from importlib.resources import files
import json
import math
from pathlib import Path


def defaults():
    data = json.loads(files('nuclear_segmentation').joinpath('defaults.json').read_text())
    data['PROCESSING_DEVICE'] = 'auto'
    return data


def validate_config(data):
    required = defaults()
    unknown = set(data) - set(required)
    if unknown:
        raise ValueError('Unknown configuration fields: ' + ', '.join(sorted(unknown)))
    result = {**required, **deepcopy(data)}
    if result['RUN_MODE'] not in {'segment', 'resume', 'density_only'}:
        raise ValueError('Select a valid run mode.')
    nominal = float(result['NOMINAL_SECTION_THICKNESS_UM'])
    if not math.isfinite(nominal) or nominal <= 0:
        raise ValueError('Nominal thickness must be positive.')
    spacing = result['VOXEL_SPACING_OVERRIDE_UM']
    if spacing is not None and (len(spacing) != 3 or any(not math.isfinite(float(v)) or float(v) <= 0 for v in spacing)):
        raise ValueError('Spacing must contain positive Z, Y and X values.')
    if result['PROCESSING_DEVICE'] not in {'auto', 'cpu'}:
        raise ValueError('Device must be auto or cpu.')
    if result['MNTB_ROI_MODE'] not in {'per_slice', 'constant_xy'}:
        raise ValueError('ROI mode must be per_slice or constant_xy.')
    if int(result['BATCH_SIZE']) < 1 or int(result['CELLPOSE_MIN_SIZE_VOXELS']) < 0:
        raise ValueError('Invalid batch size or minimum mask size.')
    if type(result['CELLPOSE_RESAMPLE']) is not bool:
        raise ValueError('CELLPOSE_RESAMPLE must be true or false.')
    rescale = result['CELLPOSE_RESCALE']
    if rescale is not None and (type(rescale) not in (int, float) or not math.isfinite(rescale) or rescale <= 0):
        raise ValueError('CELLPOSE_RESCALE must be null or a positive finite number.')
    if type(result['CELLPOSE_NORMALIZE']) not in (bool, dict):
        raise ValueError('CELLPOSE_NORMALIZE must be true, false, or a JSON object of Cellpose normalization options.')
    if result['CELLPOSE_ROI_NORMALIZATION'] is not None and not isinstance(result['CELLPOSE_ROI_NORMALIZATION'], dict):
        raise ValueError('CELLPOSE_ROI_NORMALIZATION must be null or an object.')
    from .z_correction import validate_options
    if not isinstance(result['Z_CORRECTIONS'], dict):
        raise ValueError('Z_CORRECTIONS must be a channel-to-options object.')
    names = {c['name'] for c in result['CHANNELS']}
    for name, options in result['Z_CORRECTIONS'].items():
        if name not in names:
            raise ValueError('Unknown correction channel: ' + name)
        result['Z_CORRECTIONS'][name] = validate_options(options)
    if result['Z_CORRECTIONS'] and result['CELLPOSE_ROI_NORMALIZATION'] is not None:
        raise ValueError('Reset the old ROI normalization limits before applying Z correction. They were calculated on the original image.')
    if type(result['EXPORT_CORRECTED_IMAGES']) is not bool:
        raise ValueError('EXPORT_CORRECTED_IMAGES must be true or false.')
    return result


def find_saved_roi(folder):
    """Most recently modified ROI in an export or its density addenda."""
    folder = Path(folder)
    candidates = [folder / 'mntb_roi.tif', *folder.glob('density_addendum_*/mntb_roi.tif')]
    candidates = [p for p in candidates if p.is_file()]
    return max(candidates, key=lambda p: (p.stat().st_mtime_ns, str(p))) if candidates else None
