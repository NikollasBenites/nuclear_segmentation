import numpy as np
import pandas as pd

from nuclear_segmentation.config import defaults
from nuclear_segmentation.filtering import apply_filter_settings
from nuclear_segmentation.filtering_workspace import calculate_filtering_prerequisites
from nuclear_segmentation.project_state import ChannelDescriptor, ImportedDataset, ProjectState


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
    config = defaults(); config.update(
        RUN_MODE="segment", CHANNELS=state.analysis_channel_records(),
        SEGMENTATION_CHANNEL=state.segmentation_channel,
        VOXEL_SPACING_OVERRIDE_UM=list(state.voxel_spacing_zyx_um),
        Z_CORRECTIONS={}, CELLPOSE_NORMALIZE=True, CELLPOSE_ROI_NORMALIZATION=None,
        INITIAL_MIN_VOLUME_UM3=0, PERINUCLEAR_MARKERS=[],
    )
    result = calculate_filtering_prerequisites(state, config)
    assert tuple(result["properties"].index) == (4, 9)
    assert result["raw_mask_shape"] == masks.shape
    assert result["measurement_provenance"]["voxel_spacing_zyx_um"] == (2., .5, .5)
    assert not np.shares_memory(result["filtered_masks"], masks)
    np.testing.assert_array_equal(state.segmentation_result["masks"], masks)
