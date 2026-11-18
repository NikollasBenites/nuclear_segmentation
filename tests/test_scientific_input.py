import numpy as np
import pytest

from nuclear_segmentation.config import defaults
from nuclear_segmentation.project_state import ChannelDescriptor, ImportedDataset, ProjectState
from nuclear_segmentation.scientific_input import (
    prepare_scientific_input,
    validate_scientific_input,
)
from nuclear_segmentation.z_correction import DEFAULTS as Z_DEFAULTS, correct_z


def _state(tmp_path):
    data = np.arange(3 * 4 * 6 * 8, dtype=np.uint16).reshape(3, 4, 6, 8) + 1
    channels = (
        ChannelDescriptor(0, "DNA", "DNA", "segmentation"),
        ChannelDescriptor(1, "Marker", "Marker"),
        ChannelDescriptor(2, "Other", "Other"),
    )
    dataset = ImportedDataset(
        source_path=tmp_path / "source.ome.tif", source_format="OME-TIFF",
        series_index=0, series_identifier="0", series_name="Image",
        reported_axes="CZYX", reported_shape=data.shape,
        standardized_shape=data.shape, dtype=str(data.dtype), data_czyx=data,
        voxel_spacing_zyx_um=(2.0, 0.5, 0.25), calibration_source="OME",
        channels=channels,
    )
    state = ProjectState()
    state.set_dataset(dataset)
    return state, data


def _config(state):
    config = defaults()
    config.update(
        RUN_MODE="segment", CHANNELS=state.analysis_channel_records(),
        SEGMENTATION_CHANNEL=state.segmentation_channel,
        VOXEL_SPACING_OVERRIDE_UM=list(state.voxel_spacing_zyx_um),
        Z_CORRECTIONS=state.legacy_z_corrections(), CELLPOSE_ROI_NORMALIZATION=None,
        CELLPOSE_NORMALIZE=(state.normalization_configuration or {}).get(
            "cellpose_normalize", True
        ),
    )
    return config


def _z_report(state, index, options):
    source = state.original_dataset
    working = state.working_dataset
    return {
        "options": options,
        "source_path": str(source.source_path),
        "series_index": source.series_index,
        "channel_name_at_calculation": next(
            item.name for item in state.channels if item.index == index
        ),
        "working_shape_czyx": list(working.standardized_shape),
        "voxel_spacing_zyx_um": list(state.voxel_spacing_zyx_um),
        "working_offset_yx": list(state.working_offset_yx),
    }


def test_uncropped_input_uses_views_calibration_and_default_normalization(tmp_path):
    state, source = _state(tmp_path)
    result = prepare_scientific_input(state, _config(state))
    ns = result.namespace
    assert ns["image_czyx"] is source
    assert np.shares_memory(ns["channel_volumes"]["DNA"], source)
    assert ns["segmentation_volume"].shape == (4, 6, 8)
    assert ns["VOXEL_SPACING_UM"] == (2.0, 0.5, 0.25)
    assert ns["ANISOTROPY"] == pytest.approx(2.0 / 0.375)
    assert ns["CELLPOSE_NORMALIZE"] is True
    assert ns["model_input_volume"] is ns["segmentation_volume"]
    assert ns["mntb_roi"].shape == ns["segmentation_volume"].shape
    np.testing.assert_array_equal(source, state.original_dataset.data_czyx)


def test_rectangle_and_polygon_crop_geometry_remain_distinct_from_scientific_roi(tmp_path):
    state, source = _state(tmp_path)
    rectangle = np.ones((4, 5), dtype=bool)
    state.apply_crop((1, 5, 2, 7), ((1, 2), (1, 7), (5, 7), (5, 2)), rectangle, "rectangle")
    rectangular = prepare_scientific_input(state, _config(state))
    assert rectangular.namespace["image_czyx"].shape == (3, 4, 4, 5)
    assert rectangular.provenance["working_offset_yx"] == (1, 2)

    state.reset_crop()
    polygon = np.zeros((4, 5), dtype=bool)
    polygon[:, :3] = True
    state.apply_crop((1, 5, 2, 7), ((1, 2), (1, 5), (5, 5)), polygon, "polygon")
    result = prepare_scientific_input(state, _config(state))
    np.testing.assert_array_equal(result.namespace["crop_inclusion_mask_yx"], polygon)
    assert result.namespace["mntb_roi"].ndim == 3
    assert result.namespace["crop_inclusion_mask_yx"].ndim == 2
    assert np.count_nonzero(result.namespace["image_czyx"][..., ~polygon]) == 0
    np.testing.assert_array_equal(source, state.original_dataset.data_czyx)


def test_channel_rename_and_selection_use_stable_index(tmp_path):
    state, source = _state(tmp_path)
    state.update_channel(0, name="Nuclear stain")
    state.update_channel(1, role="segmentation")
    result = prepare_scientific_input(state, _config(state))
    assert result.namespace["SEGMENTATION_CHANNEL"] == "Marker"
    np.testing.assert_array_equal(result.namespace["model_input_volume"], source[1])
    stale = _config(state)
    stale["CHANNELS"][1]["name"] = "Old name"
    with pytest.raises(ValueError, match="not in CHANNELS|outdated"):
        validate_scientific_input(state, stale)


def test_existing_z_routing_corrects_once_and_preserves_measurement_contract(tmp_path, monkeypatch):
    state, source = _state(tmp_path)
    options = {**Z_DEFAULTS, "segmentation": True, "measurement": False}
    state.set_z_correction_preview(0, options, _z_report(state, 0, options))
    state.apply_z_corrections([0])
    expected, _ = correct_z(source[0], options)
    result = prepare_scientific_input(state, _config(state))
    np.testing.assert_allclose(result.namespace["model_input_volume"], expected)
    assert np.shares_memory(result.namespace["channel_volumes"]["DNA"], source)
    np.testing.assert_array_equal(source, state.original_dataset.data_czyx)

    state.reset_z_corrections([0])
    measurement = {**Z_DEFAULTS, "segmentation": False, "measurement": True}
    state.set_z_correction_preview(0, measurement, _z_report(state, 0, measurement))
    state.apply_z_corrections([0])
    result = prepare_scientific_input(state, _config(state))
    assert result.namespace["model_input_volume"] is result.namespace["segmentation_volume"]
    assert not np.shares_memory(result.namespace["channel_volumes"]["DNA"], source)


def test_rejects_stale_preprocessing_and_project_closure(tmp_path):
    state, _ = _state(tmp_path)
    config = _config(state)
    state.normalization_configuration = {
        "mode": "percentile",
        "cellpose_normalize": {"percentile": [1, 99]},
    }
    state.normalization_is_current = False
    with pytest.raises(ValueError, match="normalization.*outdated"):
        validate_scientific_input(state, config)
    state.clear_project()
    with pytest.raises(ValueError, match="working dataset"):
        validate_scientific_input(state, config)


def test_rejects_incompatible_crop_and_missing_applied_report(tmp_path):
    state, _ = _state(tmp_path)
    state.roi_mask_yx = np.ones((2, 2), dtype=bool)
    with pytest.raises(ValueError, match="stale crop geometry"):
        validate_scientific_input(state, _config(state))
    state.roi_mask_yx = None
    state.z_correction_options[0] = dict(Z_DEFAULTS)
    state.z_correction_applied.add(0)
    with pytest.raises(ValueError, match="reports"):
        validate_scientific_input(state, _config(state))
