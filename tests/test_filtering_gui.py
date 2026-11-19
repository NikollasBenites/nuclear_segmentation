import numpy as np
import pandas as pd
import pytest
from qtpy.QtWidgets import QApplication

from nuclear_segmentation.config import defaults
from nuclear_segmentation.filtering import apply_filter_settings
from nuclear_segmentation.filtering_workspace import calculate_filtering_prerequisites
from nuclear_segmentation.mask_review_panel import SyncedNumericControl
from nuclear_segmentation.project_state import ChannelDescriptor, ImportedDataset, ProjectState


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _properties():
    return pd.DataFrame(
        {
            "volume_um3": [10.0, 20.0, 30.0],
            "sphericity": [.9, .8, .7],
            "min_z": [0, 1, 2], "max_z": [0, 1, 2],
            "centroid_z_px": [0., 1., 2.],
            "centroid_y_px": [1., 1., 1.],
            "centroid_x_px": [1., 1., 1.],
            "mean_intensity_A": [1., 5., 9.],
            "mean_intensity_B": [9., 5., 1.],
            "map2_ring_positive_fraction": [.1, .5, .9],
        }, index=pd.Index([1, 2, 3], name="label")
    )


def _masks():
    masks = np.zeros((3, 3, 3), dtype=np.uint16)
    masks[0, 1, 1], masks[1, 1, 1], masks[2, 1, 1] = 1, 2, 3
    return masks


def _settings(**updates):
    settings = {
        "minimum_volume_um3": 0., "minimum_sphericity": 0.,
        "first_tissue_z": 0, "last_tissue_z": 2,
        "z_guard_um": 0., "z_guard_mode": "none",
        "perinuclear_combination": "all", "perinuclear_filters": {},
        "intensity_combination": "all", "intensity_filters": {},
    }
    settings.update(updates)
    return settings


def test_morphology_and_original_properties_are_preserved():
    source = _properties()
    result, keep = apply_filter_settings(
        source, _masks(), np.ones((3, 3, 3), bool),
        _settings(minimum_volume_um3=15, minimum_sphericity=.75), 1.0,
    )
    assert keep.to_dict() == {1: False, 2: True, 3: False}
    assert "automatic_keep" not in source
    assert result.loc[1, "automatic_failure_reasons"] == "volume"


def test_channel_intensity_any_and_all_use_existing_columns():
    filters = {
        "A": {"column": "mean_intensity_A", "minimum": 4., "maximum": 6.},
        "B": {"column": "mean_intensity_B", "minimum": 8., "maximum": 10.},
    }
    _, all_keep = apply_filter_settings(
        _properties(), _masks(), np.ones((3, 3, 3), bool),
        _settings(intensity_filters=filters, intensity_combination="all"), 1.0,
    )
    _, any_keep = apply_filter_settings(
        _properties(), _masks(), np.ones((3, 3, 3), bool),
        _settings(intensity_filters=filters, intensity_combination="any"), 1.0,
    )
    assert not all_keep.any()
    assert any_keep.to_dict() == {1: True, 2: True, 3: False}


def test_perinuclear_filter_and_preserved_label_ids():
    settings = _settings(perinuclear_filters={
        "MAP2": {"column": "map2_ring_positive_fraction", "minimum_positive_fraction": .5}
    })
    result, keep = apply_filter_settings(
        _properties(), _masks(), np.ones((3, 3, 3), bool), settings, 1.0,
    )
    retained = result.index[keep].to_numpy()
    filtered = np.where(np.isin(_masks(), retained), _masks(), 0)
    assert set(np.unique(filtered)) == {0, 2, 3}


def test_zero_guard_preserves_directional_first_boundary_rule():
    result, keep = apply_filter_settings(
        _properties(), _masks(), np.ones((3, 3, 3), bool),
        _settings(z_guard_mode="first", z_guard_um=0), 1.0,
    )
    assert bool(result.loc[1, "excluded_by_z_guard"])
    assert not bool(keep.loc[1])
    assert bool(keep.loc[2])


def test_roi_directional_membership_is_not_centroid_z_second_gate():
    roi = np.zeros((3, 3, 3), bool); roi[1:, 1, 1] = True
    result, keep = apply_filter_settings(
        _properties(), _masks(), roi,
        _settings(z_guard_mode="last", first_tissue_z=0, last_tissue_z=2), 1.0,
    )
    assert not bool(keep.loc[1])
    assert bool(result.loc[2, "counting_point_in_roi"])


def test_measurement_adapter_uses_working_data_calibration_and_raw_ids(tmp_path):
    image = np.zeros((2, 3, 6, 6), dtype=np.uint16)
    image[0] = np.arange(108, dtype=np.uint16).reshape(3, 6, 6) + 1
    image[1] = image[0] * 2
    channels = (
        ChannelDescriptor(0, "DNA", "DNA", "segmentation"),
        ChannelDescriptor(1, "Marker", "Marker", "measurement"),
    )
    dataset = ImportedDataset(
        source_path=tmp_path / "source.tif", source_format="TIFF", series_index=0,
        series_identifier="0", series_name="Image", reported_axes="CZYX",
        reported_shape=image.shape, standardized_shape=image.shape,
        dtype=str(image.dtype), data_czyx=image,
        voxel_spacing_zyx_um=(2., .5, .5), calibration_source="test",
        channels=channels,
    )
    state = ProjectState(); state.set_dataset(dataset)
    masks = np.zeros((3, 6, 6), dtype=np.uint16)
    masks[:, 1:3, 1:3] = 4; masks[:, 3:5, 3:5] = 9
    state.set_segmentation_result({"masks": masks, "object_count": 2, "input_provenance": {"source_path": str(dataset.source_path)}})
    state.set_perinuclear_markers([{
        "channel_index": 1, "enabled": True, "ring_inner_um": 0.0,
        "ring_outer_um": 1.0, "pixel_threshold": None,
        "initial_min_positive_fraction": .25,
    }])
    config = defaults(); config.update(
        RUN_MODE="segment", CHANNELS=state.analysis_channel_records(),
        SEGMENTATION_CHANNEL=state.segmentation_channel,
        VOXEL_SPACING_OVERRIDE_UM=list(state.voxel_spacing_zyx_um),
        Z_CORRECTIONS={}, CELLPOSE_NORMALIZE=True, CELLPOSE_ROI_NORMALIZATION=None,
        INITIAL_MIN_VOLUME_UM3=0,
        PERINUCLEAR_MARKERS=state.legacy_perinuclear_markers(),
    )
    result = calculate_filtering_prerequisites(state, config)
    assert tuple(result["properties"].index) == (4, 9)
    assert result["raw_mask_shape"] == masks.shape
    assert result["measurement_provenance"]["voxel_spacing_zyx_um"] == (2., .5, .5)
    assert not np.shares_memory(result["filtered_masks"], masks)
    assert result["perinuclear_definitions"][0]["channel_index"] == 1
    assert result["perinuclear_definitions"][0]["name"] == "Marker"
    np.testing.assert_array_equal(state.segmentation_result["masks"], masks)


def test_slider_and_exact_numeric_editor_remain_synchronized(qapp):
    control = SyncedNumericControl(-37.5, 9123.75, 6)
    control.setValue(1234.567891)
    assert control.value() == pytest.approx(1234.567891)
    expected_position = round(
        (1234.567891 + 37.5) / (9123.75 + 37.5) * control._STEPS
    )
    assert control.slider.value() == expected_position
    control.slider.setValue(750)
    assert control.editor.value() == pytest.approx(
        -37.5 + (9123.75 + 37.5) * .75,
        abs=1e-6,
    )


def test_fraction_control_is_bounded(qapp):
    control = SyncedNumericControl(0, 1, 4)
    control.editor.setValue(2)
    assert control.value() == 1
    control.editor.setValue(-1)
    assert control.value() == 0


def test_perinuclear_configuration_uses_stable_index_and_current_name(tmp_path):
    image = np.ones((3, 2, 4, 4), dtype=np.uint16)
    channels = tuple(
        ChannelDescriptor(index, f"Channel {index + 1}", f"Channel {index + 1}",
                          "segmentation" if index == 0 else "measurement")
        for index in range(3)
    )
    dataset = ImportedDataset(
        source_path=tmp_path / "generic.tif", source_format="TIFF", series_index=0,
        series_identifier="0", series_name="Image", reported_axes="CZYX",
        reported_shape=image.shape, standardized_shape=image.shape,
        dtype=str(image.dtype), data_czyx=image,
        voxel_spacing_zyx_um=(1., .5, .5), calibration_source="test",
        channels=channels,
    )
    state = ProjectState(); state.set_dataset(dataset)
    records = [
        {"channel_index": 1, "enabled": True, "ring_inner_um": .25,
         "ring_outer_um": 1.5, "pixel_threshold": None,
         "initial_min_positive_fraction": .2},
        {"channel_index": 2, "enabled": True, "ring_inner_um": .5,
         "ring_outer_um": 2., "pixel_threshold": 42.,
         "initial_min_positive_fraction": .4},
    ]
    assert state.set_perinuclear_markers(records)
    assert [item["name"] for item in state.legacy_perinuclear_markers()] == [
        "Channel 2", "Channel 3"
    ]
    state.update_channel(1, name="MAP2-like marker")
    legacy = state.legacy_perinuclear_markers()
    assert legacy[0]["name"] == "MAP2-like marker"
    assert state.perinuclear_markers[0]["channel_index"] == 1
    assert legacy[0]["pixel_threshold"] is None
    assert legacy[1]["pixel_threshold"] == 42.


def test_marker_validation_and_filter_only_change_do_not_invalidate_measurements():
    state = ProjectState()
    state.channels = [ChannelDescriptor(0, "Channel 1", "Channel 1")]
    base = {"channel_index": 0, "enabled": True, "ring_inner_um": .5,
            "ring_outer_um": 2., "pixel_threshold": None,
            "initial_min_positive_fraction": .1}
    assert state.set_perinuclear_markers([base])
    state.filtering_result = {"filtered_masks": np.zeros((1, 1, 1), dtype=np.uint8)}
    state.filtering_is_current = True
    threshold_only = dict(base, initial_min_positive_fraction=.75)
    assert not state.set_perinuclear_markers([threshold_only])
    assert state.filtering_is_current
    with pytest.raises(ValueError, match="inner < outer"):
        state.set_perinuclear_markers([dict(base, ring_inner_um=2.)])
    with pytest.raises(ValueError, match="only once"):
        state.set_perinuclear_markers([base, base])
