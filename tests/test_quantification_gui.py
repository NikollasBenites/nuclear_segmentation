import json
import os
from shutil import which
from subprocess import check_output

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest
import tifffile
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication, QFileDialog

from nuclear_segmentation.core import calculate_density_summary
from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.filtering import apply_filter_settings
from nuclear_segmentation.project_state import (
    ChannelDescriptor, ImportedDataset, ProjectState,
)
from nuclear_segmentation.quantification_panel import (
    QuantificationPanel, calculate_quantification_result,
)
from nuclear_segmentation.z_correction import correct_z
from test_phase_2b4_export import FakeQtViewer, _wait


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


@pytest.fixture
def scientific_export(qapp, tmp_path):
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=QSettings(str(tmp_path / "export.ini"), QSettings.Format.IniFormat),
        restore_workspace=False,
    )

    def measure(*, crop=False, marker=False, correction=False, nominal=60, max_id=37):
        state = window.project_state
        image = np.arange(1, 2 * 6 * 8 * 9 + 1, dtype=np.uint16).reshape(2, 6, 8, 9)
        source = ImportedDataset(
            source_path=tmp_path / ("microscopy_" + "sample" * 30) / "source.lif",
            source_format="LIF", series_index=2,
            series_identifier="series-2", series_name="Selected series",
            reported_axes="TZCYX", reported_shape=(3, 6, 2, 8, 9),
            standardized_shape=image.shape, dtype=str(image.dtype), data_czyx=image,
            voxel_spacing_zyx_um=(2.0, .5, .75), calibration_source="LIF",
            channels=(
                ChannelDescriptor(0, "DNA", "DNA", "segmentation"),
                ChannelDescriptor(1, "Marker", "Marker"),
            ),
            time_index=1,
        )
        state.set_dataset(source)
        if crop:
            include = np.ones((6, 6), dtype=bool)
            include[-1, -1] = False
            state.apply_crop(
                (1, 7, 2, 8), ((1, 2), (1, 7), (6, 7), (6, 2)), include, "polygon",
            )
        if marker:
            state.set_perinuclear_markers([{
                "channel_index": 1, "ring_inner_um": 0.0, "ring_outer_um": 1.0,
                "pixel_threshold": 0, "initial_min_positive_fraction": .2,
            }])
        working = state.working_dataset
        if correction:
            _, report = correct_z(working.data_czyx[0])
            report.update(
                source_path=str(source.source_path), series_index=2,
                channel_name_at_calculation="DNA", working_shape_czyx=list(working.standardized_shape),
                voxel_spacing_zyx_um=list(state.voxel_spacing_zyx_um),
                working_offset_yx=list(state.working_offset_yx),
            )
            state.set_z_correction_preview(0, report["options"], report)
            state.apply_z_corrections([0])
        config = window.context_panel.segmentation_page.scientific_config()
        config.update(INITIAL_MIN_VOLUME_UM3=0, INITIAL_MIN_SPHERICITY=0,
                      NOMINAL_SECTION_THICKNESS_UM=nominal)
        masks = np.zeros(working.standardized_shape[1:], dtype=np.uint32)
        masks[:2, 1:3, 1:3] = 13
        masks[2:4, 3:5, 3:5] = max_id
        state.set_segmentation_result({
            "masks": masks, "mask_shape": masks.shape, "object_count": 2, "scientific_config": config,
            "segmentation_channel": "DNA", "model_name": config["MODEL_NAME"],
            "device": "mps", "device_type": "mps",
            "parameters": {"cellprob_threshold": config["CELLPROB_THRESHOLD"], "normalize": True},
        })
        assert window.filtering.calculate()
        _wait(qapp, lambda: not window.filtering.busy)
        assert state.filtering_is_current, window.context_panel.mask_review_page.measurement_status.text()
        assert not window.context_panel.export_page.scientific_button.isEnabled()
        window.filtering.accept_roi()
        assert window.context_panel.export_page.scientific_button.isEnabled()
        return state, masks

    yield window, measure
    _wait(qapp, lambda: not window.image_export.busy and not window.filtering.busy)
    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_scientific_package_preserves_schema_results_and_executed_provenance(
    scientific_export, qapp, tmp_path, monkeypatch,
):
    window, measure = scientific_export
    state, masks = measure(crop=True, marker=True, correction=True, nominal=72)
    segmentation_panel = window.context_panel.segmentation_page
    segmentation_panel.cellprob.setValue(123)
    window.context_panel.preprocessing_page.z_correction_panel.fields["percentile"].setValue(90)
    panel = window.context_panel.export_page
    assert panel.scientific_button.isEnabled()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: str(tmp_path))
    panel.scientific_button.click()
    assert window.image_export.busy
    assert not panel.scientific_button.isEnabled()
    assert not panel.scientific_progress.isHidden()
    assert not window.close_project(confirm=False)
    _wait(qapp, lambda: not window.image_export.busy)
    assert "completed" in panel.scientific_status.text(), panel.scientific_status.text()
    assert panel.scientific_progress.isHidden()
    path, = tmp_path.glob("test_export_*")
    results = panel.results.text.toPlainText()
    assert "Scientific Results\nStatus: Completed" in results
    assert f"Destination: {path}" in results
    assert "Scientific PDF report" in results
    assert not panel.open_export_folder.isHidden()
    opened = []
    monkeypatch.setattr("nuclear_segmentation.image_export_panel.QDesktopServices.openUrl", opened.append)
    panel.open_export_folder.click()
    assert opened[0].toLocalFile() == str(path)
    panel.results.copy_results()
    assert qapp.clipboard().text() == results
    assert {item.name for item in path.iterdir()} == {
        "cellpose_raw_masks.tif", "filtered_masks_syglass.tif", "mntb_roi.tif",
        "perinuclear_marker_associated_dapi_nuclei.tif", "Marker_associated_dapi_nuclei.tif",
        "all_mask_properties.csv", "retained_mask_properties.csv", "excluded_mask_properties.csv",
        "z_guard_excluded_mask_properties.csv", "perinuclear_marker_metrics.csv",
        "channel_background_estimates.csv", "filter_settings.csv", "manual_mask_review.csv",
        "analysis_summary.csv", "analysis_summary.txt", "analysis_config.json",
        "mntb_density_summary.csv", "mntb_density_summary.txt", "mntb_roi_by_z.csv",
        "qc_volume_distribution.png", "qc_volume_sphericity.png", "qc_perinuclear_markers.png",
        "qc_exclusion_reasons.png", "analysis_report.pdf", "z_correction_profiles.csv",
    }
    tables = [
        pd.read_csv(path / f"{prefix}_mask_properties.csv")
        for prefix in ("all", "retained", "excluded", "z_guard_excluded")
    ]
    assert all(table.columns.equals(tables[0].columns) for table in tables)
    assert tables[0]["label"].tolist() == [13, 37]
    assert tables[1]["label"].tolist() == [37]
    assert tables[2]["label"].tolist() == tables[3]["label"].tolist() == [13]
    assert {"mean_intensity_DNA", "marker_ring_positive_fraction", "exclusion_reasons",
            "counting_point_in_sampling_z", "automatic_failure_reasons"}.issubset(tables[0].columns)
    assert pd.read_csv(path / "manual_mask_review.csv")["mask_id"].tolist() == [13, 37]
    assert pd.read_csv(path / "filter_settings.csv").columns.tolist() == [
        "criterion", "channel", "minimum", "maximum", "enabled", "combination",
    ]
    with tifffile.TiffFile(path / "cellpose_raw_masks.tif") as raw:
        assert raw.series[0].axes == "ZYX"
        assert raw.series[0].dtype == np.uint16
        assert raw.imagej_metadata["spacing"] == 2.0
        np.testing.assert_array_equal(raw.asarray(), masks)
    np.testing.assert_array_equal(tifffile.imread(path / "mntb_roi.tif"), state.scientific_roi)
    np.testing.assert_array_equal(
        tifffile.imread(path / "filtered_masks_syglass.tif"), state.filtering_result["filtered_masks"],
    )
    np.testing.assert_array_equal(
        tifffile.imread(path / "Marker_associated_dapi_nuclei.tif"), masks,
    )
    record = json.loads((path / "analysis_config.json").read_text())
    assert record["schema_version"] == 8
    assert record["input"]["series_index"] == 2
    assert record["input"]["time_index"] == 1
    assert record["input"]["reported_axes"] == "TZCYX"
    assert record["mask_grid"]["offset_zyx_um"] == [0, .5, 1.5]
    assert record["mask_grid"]["crop"]["bounding_box_y0_y1_x0_x1"] == [1, 7, 2, 8]
    assert record["mntb_roi"]["sha256"] == state.quantification_result["scientific_roi_sha256"]
    assert record["cellpose"]["cellprob_threshold"] == -1.0
    assert record["cellpose"]["executed_parameters"]["cellprob_threshold"] == -1.0
    assert record["cellpose"]["device"] == "mps"
    assert record["z_corrections"]["DNA"]["percentile"] == 99.0
    assert record["perinuclear_markers"][0]["resolved_pixel_threshold"] == 0
    assert record["nominal_section_thickness_um"] == 72
    assert record["nominal_section_thickness_source"] == "unknown"
    assert "minimum object Z" in record["counting_rule"]
    density = pd.read_csv(path / "mntb_density_summary.csv").iloc[0]
    summary = pd.read_csv(path / "analysis_summary.csv").iloc[0]
    assert density["counting_rule"] == summary["counting_rule"] == record["counting_rule"]
    assert density["nominal_section_thickness_um"] == summary["nominal_section_thickness_um"] == 72
    assert density["retained_nuclei_in_roi"] == summary["retained_masks"] == 1
    assert density["retained_nuclei_per_mm3"] == pytest.approx(state.quantification_result["retained_nuclei_per_mm3"])
    assert density["nominal_corrected_nuclei_per_100um_cube"] != density["retained_nuclei_per_100um_cube"]
    assert pd.read_csv(path / "mntb_roi_by_z.csv")["included_in_density"].tolist() == [
        False, True, True, True, True, True,
    ]
    assert (path / "analysis_report.pdf").read_bytes().startswith(b"%PDF")
    if which("pdftotext"):
        first_page = check_output(
            ["pdftotext", "-f", "1", "-l", "1", str(path / "analysis_report.pdf"), "-"],
            text=True,
        )
        for heading in (
            "Sample and Image Information", "Segmentation Parameters",
            "Scientific Filtering Configuration", "Scientific ROI and Tissue Thickness",
            "Quantification Results", "Exclusion Summary", "Scientific Interpretation",
        ):
            assert heading in first_page
        assert "Measured full tissue thickness: 12 µm" in first_page
        assert "Physical Z guard: 1" in first_page
        assert "ROI / counting Z: 1" in first_page
        assert f"Measured nuclear density: {density['retained_nuclei_per_mm3']:,.6g}" in first_page
        assert f"Nominal-thickness-corrected density: {density['nominal_corrected_nuclei_per_100um_cube']:,.6g}" in first_page


def test_scientific_export_zero_retained_and_no_markers(scientific_export, qapp, tmp_path):
    window, measure = scientific_export
    state, _ = measure()
    review = window.context_panel.mask_review_page
    review.intensity_master.setChecked(False)
    review.last_tissue_z.setValue(1)
    _wait(qapp, lambda: window.context_panel.export_page.scientific_button.isEnabled())
    assert state.filtering_result["retained_ids"] == ()
    destination = tmp_path / "zero"
    assert window.image_export.export_scientific(destination)
    _wait(qapp, lambda: not window.image_export.busy)
    assert window.context_panel.export_page.scientific_status.text().startswith("Scientific export completed")
    density = pd.read_csv(destination / "mntb_density_summary.csv").iloc[0]
    assert density["density_status"] == "valid"
    assert density["retained_nuclei_per_mm3"] == 0
    assert density["nominal_corrected_nuclei_per_100um_cube"] == 0
    assert not tifffile.imread(destination / "filtered_masks_syglass.tif").any()
    assert pd.read_csv(destination / "retained_mask_properties.csv").empty
    assert pd.read_csv(destination / "perinuclear_marker_metrics.csv").columns.tolist() == ["label"]
    record = json.loads((destination / "analysis_config.json").read_text())
    assert record["perinuclear_markers"] == []
    assert record["nominal_section_thickness_source"] == "unknown"
    assert not record["optional_filter_status"]["combined_marker_associated_nuclei_applicable"]
    assert pd.read_csv(destination / "analysis_summary.csv")["combined_marker_associated_nuclei"].iloc[0] == 2
    assert not (destination / "perinuclear_marker_associated_dapi_nuclei.tif").exists()
    assert not (destination / "qc_perinuclear_markers.png").exists()
    report = (destination / "analysis_summary.txt").read_text()
    for line in ("Perinuclear markers: Not configured", "Perinuclear filtering: Not applied",
                 "Combined marker-associated nuclei: N/A", "Intensity filtering: Not applied",
                 "Intensity measurement mode: raw"):
        assert line in report


def test_floating_qc_resumes_and_reuses_the_z_profile(scientific_export, qapp):
    window, measure = scientific_export
    state, _ = measure()
    window.show()
    plots = window.context_panel.quality_control_page
    window.set_workflow("quality_control")
    plots.kind.setCurrentText("Z profile")
    plots.redraw()
    # Compute the first plane, then hide the window before the timer finishes.
    plots.compute_z_plane()
    profile = plots.z_profile
    assert len(profile) == 1
    window.set_workflow("quantification")
    assert not plots.z_timer.isActive()
    window.set_workflow("quality_control")
    _wait(qapp, lambda: len(profile) == state.segmentation_result["masks"].shape[0])
    window.set_workflow("project")
    window.set_workflow("quality_control")
    assert plots.z_profile is profile
    assert window.close_project(confirm=False)
    assert window.graph_window.isHidden() and not plots.z_profile


@pytest.mark.parametrize("configured,origin,expected", [
    (None, None, "default"), (60, None, "unknown"), (72, None, "unknown"),
    (60, "user-defined", "user-defined"), (60, "default", "default"),
])
def test_nominal_thickness_origin_uses_only_explicit_provenance(tmp_path, configured, origin, expected):
    state, _, _ = _project(tmp_path)
    segmentation = state.segmentation_result
    if configured is not None:
        segmentation["scientific_config"]["NOMINAL_SECTION_THICKNESS_UM"] = configured
    if origin is not None:
        segmentation["nominal_section_thickness_source"] = origin
    result = calculate_quantification_result(state)
    assert result["nominal_section_thickness_source"] == expected
    assert result["nominal_section_thickness_um"] == (configured or 60)


def test_selective_scientific_export_and_bulk_options(scientific_export, qapp, tmp_path):
    window, measure = scientific_export
    measure(max_id=65536)
    panel = window.context_panel.export_page
    buttons = {button.text(): button for button in
               panel.scientific_options_section.findChildren(type(panel.scientific_button))}
    buttons["Clear Optional"].click()
    assert not panel.scientific_button.isEnabled()
    assert "Select at least one" in panel.scientific_button.toolTip()
    assert not window.image_export.export_scientific(tmp_path / "empty")
    assert not (tmp_path / "empty").exists()
    buttons["Select All"].click()
    assert panel.selected_scientific_outputs() == ("csv", "tiff", "png", "pdf")

    for key, expected in (
        ("pdf", {"analysis_report.pdf"}),
        ("png", {"qc_volume_distribution.png", "qc_volume_sphericity.png", "qc_exclusion_reasons.png"}),
        ("csv", {
            "analysis_summary.csv", "analysis_summary.txt", "mntb_density_summary.txt",
            "mntb_density_summary.csv", "mntb_roi_by_z.csv", "filter_settings.csv",
            "all_mask_properties.csv", "retained_mask_properties.csv", "excluded_mask_properties.csv",
            "z_guard_excluded_mask_properties.csv", "manual_mask_review.csv",
            "channel_background_estimates.csv", "perinuclear_marker_metrics.csv",
        }),
    ):
        buttons["Clear Optional"].click()
        panel.scientific_options[key].setChecked(True)
        destination = tmp_path / key
        assert window.image_export.export_scientific(destination)
        _wait(qapp, lambda: not window.image_export.busy)
        assert {file.name for file in destination.iterdir()} == expected | {"analysis_config.json"}
        record = json.loads((destination / "analysis_config.json").read_text())
        assert record["export_components"] == [key]
        assert record["mntb_roi"]["mask_file"] is None


def test_selective_mask_export(scientific_export, qapp, tmp_path):
    window, measure = scientific_export
    state, masks = measure()
    panel = window.context_panel.export_page
    for key, field in panel.scientific_options.items():
        field.setChecked(key == "tiff")
    destination = tmp_path / "masks"
    assert window.image_export.export_scientific(destination)
    _wait(qapp, lambda: not window.image_export.busy)
    assert {file.name for file in destination.iterdir()} == {
        "cellpose_raw_masks.tif", "filtered_masks_syglass.tif", "mntb_roi.tif", "analysis_config.json",
    }
    np.testing.assert_array_equal(tifffile.imread(destination / "cellpose_raw_masks.tif"), masks)
    np.testing.assert_array_equal(tifffile.imread(destination / "mntb_roi.tif"), state.scientific_roi)


@pytest.mark.parametrize("mode,expected", [
    ("first", "minimum object Z"), ("last", "maximum object Z"),
    ("both", "Centroid-based"), ("none", "Centroid-based"),
])
def test_quantification_reports_executed_counting_rule(tmp_path, mode, expected):
    state, _, _ = _project(tmp_path, mode=mode)
    result = calculate_quantification_result(state)
    assert expected in result["counting_rule"]
    assert "Descriptive retained-nucleus density" in result["interpretation"]


def test_scientific_export_rechecks_roi_and_filter_validity(
    scientific_export, qapp, tmp_path, monkeypatch,
):
    window, measure = scientific_export
    state, _ = measure(crop=True)
    panel = window.context_panel.export_page
    review = window.context_panel.mask_review_page
    review.first_tissue_z.setValue(5)
    review.last_tissue_z.setValue(1)
    assert not panel.scientific_button.isEnabled()
    assert not window.image_export.export_scientific(tmp_path / "pending-filters")
    _wait(qapp, lambda: "Invalid filter settings" in review.filter_summary.text())
    assert not panel.scientific_button.isEnabled()
    review.first_tissue_z.setValue(0)
    review.last_tissue_z.setValue(5)
    _wait(qapp, panel.scientific_button.isEnabled)
    review.z_guard_mode.setCurrentText("both")
    review.z_guard_um.editor.setValue(10)
    _wait(qapp, lambda: state.quantification_result["density_status"] == "empty sampling volume")
    assert not panel.scientific_button.isEnabled()
    review.z_guard_um.editor.setValue(0)
    _wait(qapp, panel.scientific_button.isEnabled)
    roi = window.filtering.roi_layer
    translation = tuple(roi.translate)
    roi.translate = (0, 0, 0)
    assert not state.scientific_roi_reviewed
    window.filtering.accept_roi()
    assert not panel.scientific_button.isEnabled()
    assert not window.image_export.export_scientific(tmp_path / "transformed")
    roi.translate = translation
    window.filtering.accept_roi()
    assert panel.scientific_button.isEnabled()
    destination = tmp_path / "invalidated-in-dialog"

    def invalidate_while_choosing(*_):
        state.invalidate_filtering()
        return destination

    monkeypatch.setattr(
        "nuclear_segmentation.image_export_workspace._choose_export_destination",
        invalidate_while_choosing,
    )
    assert not window.image_export.choose_scientific_directory()
    assert "changed while choosing" in panel.scientific_status.text()
    assert not panel.scientific_button.isEnabled()
    assert not window.image_export.export_scientific(tmp_path / "outdated")
    assert not destination.exists()
    assert not (tmp_path / "pending-filters").exists()
    assert not (tmp_path / "transformed").exists()
    assert not (tmp_path / "outdated").exists()


def test_scientific_export_rejects_unsupported_labels(scientific_export, qapp, tmp_path):
    window, measure = scientific_export
    measure(max_id=65536)
    destination = tmp_path / "unsupported"
    assert window.image_export.export_scientific(destination)
    _wait(qapp, lambda: not window.image_export.busy)
    assert "0 to 65,535" in window.context_panel.export_page.scientific_status.text()
    assert not destination.exists()


def test_scientific_export_recovers_from_write_failure_and_preserves_existing_results(
    scientific_export, qapp, tmp_path, monkeypatch,
):
    window, measure = scientific_export
    measure()
    existing = tmp_path / "existing"
    existing.mkdir()
    previous = existing / "analysis_summary.csv"
    previous.write_text("previous results\n")
    assert window.image_export.export_scientific(existing)
    _wait(qapp, lambda: not window.image_export.busy)
    assert "already exists" in window.context_panel.export_page.scientific_status.text()

    from nuclear_segmentation import image_export_workspace
    write = image_export_workspace.calibrated_label_tiff

    def fail_after_raw(path, *args, **kwargs):
        if path.name != "cellpose_raw_masks.tif":
            assert (path.parent / "cellpose_raw_masks.tif").is_file()
            raise OSError("simulated TIFF write failure")
        return write(path, *args, **kwargs)

    destination = tmp_path / "failed"
    monkeypatch.setattr(image_export_workspace, "calibrated_label_tiff", fail_after_raw)
    assert window.image_export.export_scientific(destination)
    _wait(qapp, lambda: not window.image_export.busy)
    assert "simulated TIFF write failure" in window.context_panel.export_page.scientific_status.text()
    assert not destination.exists()
    assert previous.read_text() == "previous results\n"
    assert list(existing.iterdir()) == [previous]
