"""Bridge desktop project data to the validated legacy scientific contracts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import validate_config
from .core import reconstruct_mntb_roi, validate_channel_configuration
from .project_state import SessionRevision
from .z_correction import prepare_volumes, validate_options


@dataclass(frozen=True)
class ScientificInput:
    """Validated namespace ready for the existing inference boundary."""

    namespace: dict[str, Any]
    provenance: dict[str, Any]


@dataclass(frozen=True, eq=False)
class ScientificInputSnapshot:
    """Capture the job on the GUI thread, sharing only the existing pixel arrays."""

    owner: SessionRevision
    image_czyx: np.ndarray
    crop_mask_yx: np.ndarray | None
    _config: dict[str, Any]
    _provenance: dict[str, Any]

    def config(self) -> dict[str, Any]:
        return deepcopy(self._config)

    def provenance(self) -> dict[str, Any]:
        return deepcopy(self._provenance)


def validate_scientific_input(project_state, config: dict[str, Any]) -> dict[str, Any]:
    """Validate desktop state without loading Cellpose or transforming pixels."""
    validated = validate_config(config)
    dataset = project_state.working_dataset
    original = project_state.original_dataset
    if dataset is None or original is None:
        raise ValueError("A current working dataset is required.")

    image = np.asarray(dataset.data_czyx)
    if image.ndim != 4 or any(int(size) <= 0 for size in image.shape):
        raise ValueError("The working dataset must be a nonempty CZYX array.")
    if tuple(image.shape) != tuple(dataset.standardized_shape):
        raise ValueError("Working data dimensions do not match project metadata.")
    if Path(dataset.source_path) != Path(original.source_path):
        raise ValueError("The working dataset does not belong to the current project.")

    channels = validated["CHANNELS"]
    validate_channel_configuration(channels, validated["SEGMENTATION_CHANNEL"])
    indexes = [int(item["index"]) for item in channels]
    if len(set(indexes)) != len(indexes) or any(
        index < 0 or index >= image.shape[0] for index in indexes
    ):
        raise ValueError(
            "Channel indexes must be unique and available in the working CZYX data."
        )
    state_records = {int(item.index): item.name for item in project_state.channels}
    if state_records != {int(item["index"]): item["name"] for item in channels}:
        raise ValueError("The scientific channel configuration is outdated for this project.")

    spacing = tuple(float(value) for value in project_state.voxel_spacing_zyx_um or ())
    if len(spacing) != 3 or not all(np.isfinite(value) and value > 0 for value in spacing):
        raise ValueError("Voxel spacing must contain positive finite Z, Y and X values.")
    configured_spacing = validated["VOXEL_SPACING_OVERRIDE_UM"]
    if configured_spacing is None or not np.allclose(configured_spacing, spacing):
        raise ValueError("Scientific calibration is outdated for the working dataset.")

    crop = project_state.crop_provenance
    crop_mask = project_state.roi_mask_yx
    if crop is None:
        if crop_mask is not None or tuple(project_state.working_offset_yx) != (0, 0):
            raise ValueError("Uncropped project state contains stale crop geometry.")
    else:
        if tuple(crop.cropped_shape_czyx) != tuple(image.shape):
            raise ValueError("Crop provenance does not match the working dataset.")
        if crop_mask is None or np.asarray(crop_mask).shape != image.shape[-2:]:
            raise ValueError("Crop inclusion mask does not match the working XY grid.")
        if not np.asarray(crop_mask, dtype=bool).any():
            raise ValueError("Crop inclusion mask is empty.")

    applied = set(project_state.z_correction_applied)
    if not applied.issubset(project_state.z_correction_options):
        raise ValueError("Applied Z-correction settings are incomplete.")
    if not applied.issubset(project_state.z_correction_reports):
        raise ValueError("Applied Z-correction reports are missing or outdated.")
    for index in applied:
        report = project_state.z_correction_reports[index]
        channel = next(item for item in project_state.channels if item.index == index)
        expected_report = {
            "source_path": str(original.source_path),
            "series_index": int(original.series_index),
            "channel_name_at_calculation": channel.name,
            "working_shape_czyx": list(dataset.standardized_shape),
            "voxel_spacing_zyx_um": list(spacing),
            "working_offset_yx": list(project_state.working_offset_yx),
        }
        if any(report.get(key) != value for key, value in expected_report.items()):
            raise ValueError("Applied Z-correction provenance is missing or outdated.")
        if validate_options(report.get("options", {})) != validate_options(
            project_state.z_correction_options[index]
        ):
            raise ValueError("Applied Z-correction settings do not match their report.")
    expected_z = project_state.legacy_z_corrections()
    if validated["Z_CORRECTIONS"] != {
        name: validate_options(options) for name, options in expected_z.items()
    }:
        raise ValueError("Scientific Z-correction configuration is outdated.")

    normalization = project_state.normalization_configuration or {}
    mode = normalization.get("mode", "cellpose_default")
    if mode not in {"cellpose_default", "disabled"} and not project_state.normalization_is_current:
        raise ValueError("Cellpose normalization configuration is outdated.")
    expected_normalize = deepcopy(normalization.get("cellpose_normalize", True))
    if validated["CELLPOSE_NORMALIZE"] != expected_normalize:
        raise ValueError("Scientific Cellpose normalization configuration is outdated.")
    return validated


def capture_scientific_input(project_state, config: dict[str, Any]) -> ScientificInputSnapshot:
    validated = validate_scientific_input(project_state, config)
    crop_mask = project_state.roi_mask_yx
    return ScientificInputSnapshot(
        owner=project_state.session_revision(),
        image_czyx=np.asarray(project_state.working_dataset.data_czyx),
        crop_mask_yx=None if crop_mask is None else np.asarray(crop_mask, dtype=bool),
        _config=validated,
        _provenance=scientific_input_provenance(project_state, validated),
    )


def prepare_scientific_input(project_state, config: dict[str, Any]) -> ScientificInput:
    """Prepare legacy inputs from the current CZYX data; never execute Cellpose."""
    return prepare_captured_scientific_input(capture_scientific_input(project_state, config))


def prepare_captured_scientific_input(snapshot: ScientificInputSnapshot) -> ScientificInput:
    """Perform pixel work using the captured inputs, without reading project state."""
    validated = snapshot.config()
    provenance = snapshot.provenance()
    image = snapshot.image_czyx
    spacing = provenance["voxel_spacing_zyx_um"]
    channels = validated["CHANNELS"]
    volumes = {item["name"]: image[int(item["index"])] for item in channels}
    segmentation = volumes[validated["SEGMENTATION_CHANNEL"]]
    roi = reconstruct_mntb_roi(image, validated["MNTB_ROI_MODE"])
    if roi.shape != segmentation.shape or not roi.any():
        raise ValueError(
            "The provisional scientific ROI is empty or incompatible with the working grid."
        )

    xy_spacing = float(np.mean(spacing[1:]))
    namespace = dict(validated)
    namespace.update(
        image_czyx=image,
        channel_volumes=volumes,
        segmentation_volume=segmentation,
        VOXEL_SPACING_UM=spacing,
        Z_SPACING_UM=spacing[0],
        Y_SPACING_UM=spacing[1],
        X_SPACING_UM=spacing[2],
        XY_SPACING_UM=xy_spacing,
        ANISOTROPY=float(spacing[0] / xy_spacing),
        mntb_roi=roi,
        roi_source="inferred_" + validated["MNTB_ROI_MODE"],
        crop_inclusion_mask_yx=snapshot.crop_mask_yx,
    )
    prepare_volumes(namespace)
    return ScientificInput(namespace=namespace, provenance=provenance)


def scientific_input_provenance(project_state, validated):
    """Describe the existing grid and executed settings without preparing pixels."""
    dataset = project_state.working_dataset
    crop = project_state.crop_provenance
    return {
        "source_path": str(dataset.source_path),
        "source_series": int(dataset.series_index),
        "working_shape_czyx": tuple(dataset.standardized_shape),
        "voxel_spacing_zyx_um": tuple(float(value) for value in project_state.voxel_spacing_zyx_um),
        "working_offset_yx": tuple(int(value) for value in project_state.working_offset_yx),
        "crop_type": None if crop is None else crop.selection_type,
        "segmentation_channel": validated["SEGMENTATION_CHANNEL"],
        "z_corrections": deepcopy(validated["Z_CORRECTIONS"]),
        "cellpose_normalize": deepcopy(validated["CELLPOSE_NORMALIZE"]),
    }
