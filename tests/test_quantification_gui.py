import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest
from qtpy.QtWidgets import QApplication

from nuclear_segmentation.core import calculate_density_summary
from nuclear_segmentation.filtering import apply_filter_settings
from nuclear_segmentation.project_state import (
    ChannelDescriptor, ImportedDataset, ProjectState,
)
from nuclear_segmentation.quantification_panel import (
    QuantificationPanel, calculate_quantification_result,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _project(tmp_path, *, mode="none", guard=0.0, reviewed=True):
    image = np.ones((1, 6, 4, 4), dtype=np.uint16)
    dataset = ImportedDataset(
        source_path=tmp_path / "quantification.ome.tif",
        source_format="OME-TIFF",
        series_index=0,
        series_identifier="0",
        series_name="Image",
        reported_axes="CZYX",
        reported_shape=image.shape,
        standardized_shape=image.shape,
        dtype=str(image.dtype),
        data_czyx=image,
        voxel_spacing_zyx_um=(2.0, 1.0, 1.0),
        calibration_source="OME",
        channels=(ChannelDescriptor(0, "DAPI", "DAPI", "segmentation"),),
    )
    state = ProjectState()
    state.set_dataset(dataset)
    masks = np.zeros(image.shape[1:], dtype=np.uint16)
    for label, z in enumerate((0, 1, 4, 5), start=1):
        masks[z, label - 1, label - 1] = label
    state.set_segmentation_result({
        "masks": masks,
        "mask_shape": masks.shape,
        "object_count": 4,
        "segmentation_channel": "DAPI",
        "scientific_config": {"MODEL_NAME": "cpsam", "CELLPOSE_DO_3D": True},
    })
    properties = pd.DataFrame({
        "min_z": [0, 1, 4, 5],
        "max_z": [0, 1, 4, 5],
        "centroid_z_px": [0.0, 1.0, 4.0, 5.0],
        "centroid_y_px": [0.0, 1.0, 2.0, 3.0],
        "centroid_x_px": [0.0, 1.0, 2.0, 3.0],
        "volume_um3": [10.0, 2.0, 10.0, 10.0],
        "sphericity": [.9, .9, .2, .9],
        "mean_intensity_DAPI": [50.0, 50.0, 50.0, 200.0],
        "marker_ring_positive_fraction": [.8, .8, .8, .1],
    }, index=[1, 2, 3, 4])
    settings = {
        "minimum_volume_um3": 5.0,
        "minimum_sphericity": .5,
        "first_tissue_z": 0,
        "last_tissue_z": 5,
        "z_guard_um": float(guard),
        "z_guard_mode": mode,
        "perinuclear_combination": "all",
        "perinuclear_filters": {
            "Marker": {
                "column": "marker_ring_positive_fraction",
                "minimum_positive_fraction": .5,
            }
        },
        "intensity_combination": "all",
        "intensity_filters": {
            "DAPI": {
                "column": "mean_intensity_DAPI",
                "minimum": 0.0,
                "maximum": 100.0,
            }
        },
    }
    roi = np.ones(masks.shape, dtype=bool)
    measured, keep = apply_filter_settings(
        properties, masks, roi, settings, 2.0
    )
    retained = tuple(int(value) for value in measured.index[keep])
    state.set_scientific_roi(
        roi, {"source": "synthetic", "mask_shape": masks.shape},
        reviewed=reviewed,
    )
    state.set_filtering_result({
        "properties": measured,
        "settings": settings,
        "retained_ids": retained,
        "retained_count": len(retained),
        "filtered_masks": np.where(np.isin(masks, retained), masks, 0),
        "raw_mask_shape": masks.shape,
        "scientific_roi": roi,
        "roi_reviewed": reviewed,
        "measurement_provenance": {"source_path": str(dataset.source_path)},
    })
    return state, roi, measured


@pytest.mark.parametrize("mode", ["none", "first", "last", "both"])
def test_quantification_matches_authoritative_density_for_all_guard_modes(
    tmp_path, mode
):
    state, roi, _ = _project(tmp_path, mode=mode, reviewed=True)
    result = calculate_quantification_result(state)
    expected = calculate_density_summary(
        roi.sum(axis=(1, 2)),
        state.filtering_result["settings"],
        state.voxel_spacing_zyx_um,
        len(state.filtering_result["retained_ids"]),
        True,
    )
    for key in (
        "roi_stack_volume_um3", "roi_tissue_volume_um3",
        "roi_effective_volume_um3", "roi_effective_volume_mm3",
        "retained_nuclei_per_mm3", "retained_nuclei_per_100um_cube",
        "measured_full_tissue_thickness_um",
        "thickness_density_correction_factor",
        "nominal_corrected_nuclei_per_100um_cube",
    ):
        assert result[key] == pytest.approx(expected[key])


def test_counts_and_overlapping_failure_reasons_are_preserved(tmp_path):
    state, _, properties = _project(tmp_path)
    result = calculate_quantification_result(state)
    assert result["total_cellpose_masks"] == 4
    assert result["retained_masks"] == len(state.filtering_result["retained_ids"])
    assert result["excluded_masks"] == 4 - result["retained_masks"]
    assert result["failed_morphology"] == 2
    assert result["failed_intensity"] == 1
    assert result["failed_perinuclear"] == 1
    assert result["excluded_by_z_guard"] == int(
        properties["excluded_by_z_guard"].sum()
    )
    assert result["excluded_by_sampling_roi"] == int(
        properties["excluded_by_sampling_roi"].sum()
    )
    assert result["retained_mask_ids"] == state.filtering_result["retained_ids"]


def test_zero_guard_keeps_authoritative_boundary_exclusion(tmp_path):
    first, _, first_properties = _project(tmp_path / "first", mode="first", guard=0)
    last, _, last_properties = _project(tmp_path / "last", mode="last", guard=0)
    both, _, both_properties = _project(tmp_path / "both", mode="both", guard=0)
    assert first_properties.loc[1, "excluded_by_z_guard"]
    assert last_properties.loc[4, "excluded_by_z_guard"]
    assert both_properties.loc[[1, 4], "excluded_by_z_guard"].all()
    assert calculate_quantification_result(first)["eligible_sampling_planes"] == 5
    assert calculate_quantification_result(last)["eligible_sampling_planes"] == 5
    assert calculate_quantification_result(both)["eligible_sampling_planes"] == 4


def test_roi_review_and_empty_sampling_volume_keep_density_unavailable(tmp_path):
    state, _, _ = _project(tmp_path / "review", reviewed=False)
    result = calculate_quantification_result(state)
    assert result["density_status"] == "review ROI"
    assert result["retained_nuclei_per_mm3"] is None
    assert result["retained_nuclei_per_100um_cube"] is None

    empty, _, _ = _project(tmp_path / "empty", mode="both", guard=100, reviewed=True)
    result = calculate_quantification_result(empty)
    assert result["density_status"] == "empty sampling volume"
    assert result["roi_effective_volume_um3"] == 0
    assert result["retained_nuclei_per_mm3"] is None


def test_panel_lifecycle_outdated_measurements_and_project_close(qapp, tmp_path):
    state, _, _ = _project(tmp_path)
    panel = QuantificationPanel(state)
    assert state.quantification_is_current
    assert "valid" in panel.status.text().lower()
    original_properties = state.filtering_result["properties"]

    state.invalidate_filtering()
    assert not state.quantification_is_current
    assert "recalculation" in panel.status.text().lower()
    assert state.filtering_result["properties"] is original_properties
    assert state.scientific_roi_reviewed
    reviewed_roi = state.scientific_roi.copy()

    state.set_filtering_result(state.filtering_result)
    assert state.quantification_is_current
    assert state.filtering_result["properties"] is original_properties
    np.testing.assert_array_equal(state.scientific_roi, reviewed_roi)
    state.clear_project()
    assert state.quantification_result is None
    assert "current segmentation" in panel.status.text().lower()
    panel.dispose()


def test_incompatible_roi_grid_is_rejected(tmp_path):
    state, _, _ = _project(tmp_path)
    state.scientific_roi = np.ones((2, 2, 2), dtype=bool)
    with pytest.raises(ValueError, match="segmentation grid"):
        calculate_quantification_result(state)
