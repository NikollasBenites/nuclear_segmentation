"""Independent multichannel preparation for visualization and later TIFF export."""
from __future__ import annotations

from copy import deepcopy
from time import perf_counter

import numpy as np

from .normalization import mask_digest
from .z_correction import correct_z, validate_options


ROUTE_LABELS = {
    "original": "Original working image",
    "z_corrected": "Z-corrected image",
    "normalized": "Globally normalized image",
    "z_corrected_normalized": "Z-corrected and globally normalized image",
}


def validate_preparation_options(options: dict) -> dict:
    lower = float(options.get("lower", 1.0))
    upper = float(options.get("upper", 99.0))
    if not np.isfinite([lower, upper]).all() or not 0 <= lower < upper <= 100:
        raise ValueError("Percentiles must satisfy 0 <= lower < upper <= 100.")
    scope = str(options.get("scope", "valid_working"))
    if scope not in {"valid_working", "working_bbox"}:
        raise ValueError("Unsupported normalization sampling scope.")
    return {
        "lower": lower,
        "upper": upper,
        "clip": bool(options.get("clip", True)),
        "scope": scope,
    }


def global_normalize_volume(
    volume,
    *,
    lower: float,
    upper: float,
    clip: bool,
    valid_mask_yx=None,
    scope: str = "valid_working",
) -> tuple[np.ndarray, dict]:
    """Apply one exact percentile mapping across a complete ZYX channel volume."""
    options = validate_preparation_options(
        {"lower": lower, "upper": upper, "clip": clip, "scope": scope}
    )
    source = np.asarray(volume)
    if source.ndim != 3 or any(size == 0 for size in source.shape):
        raise ValueError("Image preparation requires a non-empty ZYX channel volume.")
    if not np.isfinite(source).all():
        raise ValueError("Image preparation does not accept NaN or infinite intensities.")
    mask = None
    if valid_mask_yx is not None:
        mask = np.asarray(valid_mask_yx, dtype=bool)
        if mask.shape != source.shape[-2:] or not mask.any():
            raise ValueError("The valid working mask is empty or incompatible.")
    if options["scope"] == "valid_working" and mask is not None:
        samples = source[..., mask]
        sampling_scope = "valid working XY region extended through all Z planes"
    else:
        samples = source.reshape(-1)
        sampling_scope = (
            "complete working bounding box including polygon exterior fill"
            if mask is not None and not mask.all()
            else "complete working ZYX volume"
        )
    low, high = np.percentile(samples, (options["lower"], options["upper"]))
    low, high = float(low), float(high)
    output = np.zeros(source.shape, dtype=np.float32)
    constant = high <= low
    if not constant:
        np.subtract(source, low, out=output, casting="unsafe")
        output /= np.float32(high - low)
        if options["clip"]:
            np.clip(output, 0.0, 1.0, out=output)
    if mask is not None and not mask.all():
        output[:, ~mask] = 0.0
    report = {
        **options,
        "limits": [low, high],
        "constant_input": bool(constant),
        "sample_count": int(samples.size),
        "sampling_scope": sampling_scope,
        "mapping": "(input - lower_limit) / (upper_limit - lower_limit)",
        "clipping": "bounded to [0, 1]" if options["clip"] else "not clipped",
        "output_dtype": "float32",
        "whole_z_volume_statistics": True,
        "per_plane_normalization": False,
    }
    return output, report


def prepare_channel(
    volume,
    route: str,
    options: dict,
    *,
    valid_mask_yx=None,
    z_options: dict | None = None,
) -> tuple[np.ndarray, dict]:
    """Prepare one channel from unmodified working data in an explicit order."""
    route = str(route)
    if route not in ROUTE_LABELS:
        raise ValueError(f"Unsupported image-preparation route: {route}")
    source = np.asarray(volume)
    if source.ndim != 3 or not np.isfinite(source).all():
        raise ValueError("Image preparation requires finite ZYX input data.")
    started = perf_counter()
    z_report = None
    prepared = source
    if route in {"z_corrected", "z_corrected_normalized"}:
        if z_options is None:
            raise ValueError("Calculate a current Z correction for this channel first.")
        prepared, z_report = correct_z(source, validate_options(z_options))
    normalization_report = None
    if route in {"normalized", "z_corrected_normalized"}:
        prepared, normalization_report = global_normalize_volume(
            prepared,
            valid_mask_yx=valid_mask_yx,
            **validate_preparation_options(options),
        )
    report = {
        "route": route,
        "route_label": ROUTE_LABELS[route],
        "processing_order": (
            "original working image -> Z correction -> global normalization"
            if route == "z_corrected_normalized"
            else "original working image -> Z correction"
            if route == "z_corrected"
            else "original working image -> global normalization"
            if route == "normalized"
            else "original working image"
        ),
        "source_dtype": str(source.dtype),
        "output_dtype": str(np.asarray(prepared).dtype),
        "shape_zyx": list(source.shape),
        "source_range": [float(np.min(source)), float(np.max(source))],
        "output_range": [float(np.min(prepared)), float(np.max(prepared))],
        "z_correction": None if z_report is None else deepcopy(z_report),
        "global_normalization": normalization_report,
        "processing_seconds": float(perf_counter() - started),
        "display_settings_applied": False,
    }
    return prepared, report


def preparation_fingerprint(project_state, routes: dict[int, str], options: dict) -> dict:
    source = project_state.original_dataset
    working = project_state.working_dataset
    roi_digest = None
    if project_state.roi_mask_yx is not None:
        roi_digest = mask_digest(project_state.roi_mask_yx)
    return {
        "source_path": None if source is None else str(source.source_path),
        "series_index": None if source is None else int(source.series_index),
        "working_shape_czyx": None if working is None else list(working.standardized_shape),
        "working_offset_yx": list(project_state.working_offset_yx),
        "valid_roi_sha256": roi_digest,
        "routes": {int(index): str(route) for index, route in routes.items()},
        "z_correction_options": {
            int(index): deepcopy(project_state.z_correction_reports[index]["options"])
            for index, route in routes.items()
            if route in {"z_corrected", "z_corrected_normalized"}
            and index in project_state.z_correction_reports
        },
        "options": validate_preparation_options(options),
    }
