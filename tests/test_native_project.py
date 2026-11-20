"""Native resume tests use real disk files and the desktop worker lifecycle."""
from copy import deepcopy
import shutil
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest
import tifffile
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication, QFileDialog, QMessageBox

from nuclear_segmentation import native_project
from nuclear_segmentation.core import file_sha256
from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.image_import import load_tiff_dataset
from nuclear_segmentation.lif_import import load_lif_dataset
from nuclear_segmentation.manual_review import ManualReview
from nuclear_segmentation.projection_crop import polygon_roi_mask
from nuclear_segmentation.scientific_input import prepare_scientific_input
from test_desktop_normalization import FakeQtViewer, _wait, _window_with_tiff
from test_lif_import import FakeBioImage


@pytest.fixture
def project(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window, source, original = _window_with_tiff(app, tmp_path)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _, title, text: warnings.append(text))
    yield window, app, source, original, warnings
    _wait(app, lambda: window._import_thread is None)
    window.close()


def save(window, app, folder=None, **options):
    assert window.save_project(folder, **options)
    _wait(app, lambda: window._import_thread is None)


def open_saved(window, app, folder, **options):
    assert window.open_project(folder, **options)
    _wait(app, lambda: window._import_thread is None)


def build_snapshot(window):
    state = window.project_state
    payload = native_project.snapshot_state(state)
    payload.update(
        z_previews={}, normalization_pixels=None,
        filter_controls=window.context_panel.mask_review_page.filter_settings(),
    )
    return payload


def test_imported_project_roundtrip_and_independent_save_as(project, tmp_path):
    window, app, source, original, warnings = project
    folder = tmp_path / "Analysis Ω.ncs-project"
    save(window, app, folder)
    assert window.project_directory == folder and not window.project_dirty
    assert {file.name for file in folder.iterdir()} == {"project.json", "checkpoint.ncs"}
    with ZipFile(folder / native_project.CHECKPOINT) as archive:
        assert not any(name.endswith(".npy") for name in archive.namelist())
    assert window.close_project()
    open_saved(window, app, folder)
    state = window.project_state
    assert state.original_dataset.source_path == source.resolve()
    assert state.original_dataset.source_sha256 == file_sha256(source)
    np.testing.assert_array_equal(state.original_dataset.data_czyx, original)
    assert state.segmentation_result is None and state.filtering_result is None
    assert len(window.embedding.viewer.layers) == original.shape[0]
    assert not window.project_dirty
    other = tmp_path / "Independent"
    save(window, app, other, save_as=True)
    assert native_project._manifest(folder)["id"] != native_project._manifest(other)["id"]
    before = (folder / native_project.CHECKPOINT).read_bytes()
    save(window, app, folder, save_as=True)
    assert warnings and (folder / native_project.CHECKPOINT).read_bytes() == before
    assert window.project_directory == other


def _segment(window):
    state = window.project_state
    config = window.context_panel.segmentation_page.scientific_config()
    prepared = prepare_scientific_input(state, config)
    masks = np.zeros(state.working_dataset.standardized_shape[1:], dtype=np.uint32)
    masks[:, 1:3, 1:3] = 90000
    manual = ManualReview(masks, [90000])
    manual.set(90000, "keep", "Reviewed record")
    state.set_segmentation_result({
        "masks": masks, "mask_shape": masks.shape, "object_count": 1, "maximum_label_id": 90000,
        "scientific_config": config, "input_provenance": prepared.provenance,
        "segmentation_channel": state.segmentation_channel, "model_name": config["MODEL_NAME"],
        "parameters": {}, "device": "cpu", "device_type": "cpu",
        "z_corrections": config["Z_CORRECTIONS"],
        "model_input_volume": prepared.namespace["model_input_volume"],
        "manual_review": manual.to_record(), "edited_masks": masks.copy(),
    })
    return masks


def _forbid_calculations(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Opening a native checkpoint reran scientific processing")
    for name in (
        "segmentation_execution.execute_cellpose_stage",
        "filtering_workspace.calculate_mask_properties",
        "filtering_workspace.calculate_perinuclear_marker_metrics",
        "filtering_workspace.apply_filter_settings",
        "z_correction.correct_z",
        "z_correction_workspace.correct_z",
        "normalization_workspace.calculate_normalization",
        "quantification_panel.calculate_quantification_result",
    ):
        monkeypatch.setattr("nuclear_segmentation." + name, unexpected)


def test_processed_checkpoint_reuses_results_with_exact_labels_roi_and_density(project, tmp_path, monkeypatch):
    window, app, _, _, warnings = project
    state = window.project_state
    vertices = [(1, 2), (1, 8), (7, 8), (7, 2)]
    bbox, crop_mask = polygon_roi_mask(vertices, state.original_dataset.standardized_shape[-2:])
    dataset = state.apply_crop(bbox, vertices, crop_mask, "polygon")
    window._display_working_dataset(dataset)
    panel = window.context_panel.preprocessing_page
    assert window.z_correction.calculate({0: panel.z_correction_panel.options_for(0)}, 0, True)
    _wait(app, lambda: not window.z_correction.busy)
    assert window.z_correction.apply([0])
    assert window.normalization.reuse_crop_roi()
    panel.normalization_mode_combo.setCurrentIndex(panel.normalization_mode_combo.findData("percentile"))
    assert window.normalization.calculate(panel.normalization_options())
    _wait(app, lambda: not window.normalization.busy)
    assert window.normalization.apply()
    masks = _segment(window)
    assert window.filtering.calculate()
    _wait(app, lambda: not window.filtering.busy)
    assert state.filtering_is_current
    review = window.context_panel.mask_review_page
    review.minimum_volume.editor.setValue(0)
    review.z_guard_mode.setCurrentText("none")
    review.intensity_master.setChecked(False)
    _, intensity_enabled, lower_intensity, _, _ = review.intensity_controls[0]
    intensity_enabled.setChecked(True)
    lower_intensity.setValue(lower_intensity.value() + 1)
    expected_intensity = review.intensity_control_settings()
    _wait(app, lambda: not review._filter_timer.isActive())
    window.filtering.accept_roi()
    assert state.scientific_roi_reviewed and state.quantification_is_current
    assert state.quantification_result["density_status"] == "valid"
    assert state.filtering_result["retained_ids"] == (90000,)
    initial_density = deepcopy(state.quantification_result)
    initial_measurements = state.filtering_result["properties"].copy()
    corrected = window.z_correction.preview_layers[0].data.copy()
    normalize = window.normalization.preview_layer.data.copy()
    panel.z_correction_panel.fields["percentile"].setValue(93)
    assert state.z_correction_applied == {0}
    folder = tmp_path / "Processed"
    save(window, app, folder)
    assert window.close_project()
    _forbid_calculations(monkeypatch)
    open_saved(window, app, folder)
    assert not warnings
    assert state.working_offset_yx == (1, 2)
    assert set(state.z_correction_applied) == {0}
    assert panel.z_correction_panel.options_for(0)["percentile"] == 93
    np.testing.assert_array_equal(window.z_correction.preview_layers[0].data, corrected)
    np.testing.assert_array_equal(window.normalization.preview_layer.data, normalize)
    assert state.normalization_is_current
    assert state.normalization_configuration["mode"] == "percentile"
    y0, y1, x0, x1 = bbox
    np.testing.assert_array_equal(state.normalization_roi_mask_yx[y0:y1, x0:x1], crop_mask)
    assert state.segmentation_is_current and state.filtering_is_current
    assert state.quantification_is_current and state.scientific_roi_reviewed
    assert state.quantification_result == initial_density
    np.testing.assert_array_equal(window.segmentation.result_layer.data, masks)
    assert window.segmentation.result_layer.data.dtype == np.uint32
    assert tuple(window.segmentation.result_layer.translate) == (
        0., state.voxel_spacing_zyx_um[1], 2 * state.voxel_spacing_zyx_um[2],
    )
    np.testing.assert_array_equal(state.segmentation_result["edited_masks"], masks)
    assert state.segmentation_result["manual_review"]["decisions"]["90000"]["decision"] == "keep"
    pd.testing.assert_frame_equal(state.filtering_result["properties"], initial_measurements)
    assert review.filter_settings() == state.filtering_result["settings"]
    assert review.intensity_control_settings() == expected_intensity
    assert window.context_panel.export_page.scientific_button.isEnabled()
    assert not window.project_dirty


def test_preprocessing_only_checkpoint_with_relocated_roi_source(project, tmp_path, monkeypatch):
    window, app, source, _, warnings = project
    state = window.project_state
    vertices = [(1, 2), (1, 8), (7, 8), (7, 2)]
    bbox, crop_mask = polygon_roi_mask(vertices, state.original_dataset.standardized_shape[-2:])
    window._display_working_dataset(state.apply_crop(bbox, vertices, crop_mask, "polygon"))
    assert window.normalization.reuse_crop_roi()
    panel = window.context_panel.preprocessing_page
    panel.normalization_mode_combo.setCurrentIndex(panel.normalization_mode_combo.findData("roi_fixed"))
    assert window.normalization.calculate(panel.normalization_options())
    _wait(app, lambda: not window.normalization.busy)
    assert window.normalization.apply()
    folder = tmp_path / "Preprocessed"
    save(window, app, folder)
    assert window.close_project()
    relocated = tmp_path / "Relocated.ome.tif"
    shutil.copyfile(source, relocated)
    source.unlink()
    _forbid_calculations(monkeypatch)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(relocated), ""))
    open_saved(window, app, folder)
    assert not warnings
    assert state.segmentation_result is None and state.filtering_result is None
    assert state.normalization_is_current
    assert state.normalization_configuration["mode"] == "roi_fixed"
    assert state.normalization_roi_provenance["source"] == str(relocated.resolve())
    assert state.normalization_configuration["provenance"]["source"]["path"] == str(relocated.resolve())
    np.testing.assert_array_equal(state.roi_mask_yx, crop_mask)


def test_stale_measurements_keep_cached_density_pending(project, tmp_path, monkeypatch):
    window, app, _, _, warnings = project
    state = window.project_state
    _segment(window)
    assert window.filtering.calculate()
    _wait(app, lambda: not window.filtering.busy)
    review = window.context_panel.mask_review_page
    review.z_guard_mode.setCurrentText("none")
    _wait(app, lambda: not review._filter_timer.isActive())
    window.filtering.accept_roi()
    cached = deepcopy(state.quantification_result)
    assert cached["density_status"] == "valid"
    state.invalidate_filtering()
    assert state.scientific_roi_reviewed and not state.quantification_is_current
    folder = tmp_path / "Outdated"
    save(window, app, folder)
    assert window.close_project()
    _forbid_calculations(monkeypatch)
    open_saved(window, app, folder)
    assert not warnings
    assert state.segmentation_is_current and state.scientific_roi_reviewed
    assert not state.filtering_is_current and not state.quantification_is_current
    assert state.quantification_result == cached
    assert not window.context_panel.export_page.scientific_button.isEnabled()


def test_stale_segmentation_and_pending_settings_remain_stale(project, tmp_path, monkeypatch):
    window, app, _, _, warnings = project
    masks = _segment(window)
    panel = window.context_panel.preprocessing_page
    assert window.z_correction.calculate({0: panel.z_correction_panel.options_for(0)}, 0, True)
    _wait(app, lambda: not window.z_correction.busy)
    assert not window.project_state.segmentation_is_current
    assert not window.project_state.z_correction_applied
    panel.z_correction_panel.fields["percentile"].setValue(92)
    panel.normalization_upper.setValue(97)
    window.context_panel.segmentation_page.cellprob.setValue(.7)
    folder = tmp_path / "Pending"
    save(window, app, folder)
    assert window.close_project()
    _forbid_calculations(monkeypatch)
    open_saved(window, app, folder)
    assert not warnings
    state = window.project_state
    assert not state.segmentation_is_current and not state.quantification_is_current
    assert not state.z_correction_applied and not state.normalization_is_current
    np.testing.assert_array_equal(state.segmentation_result["masks"], masks)
    assert panel.z_correction_panel.options_for(0)["percentile"] == 92
    assert window.z_correction.preview_layers[0].metadata["z_correction_preview_status"] == "outdated"
    assert panel.normalization_upper.value() == 97
    assert window.context_panel.segmentation_page.cellprob.value() == .7


def test_raw_masks_reopen_in_a_new_window_without_measurements(project, tmp_path, monkeypatch):
    window, app, _, _, warnings = project
    masks = _segment(window)
    folder = tmp_path / "Segmented"
    save(window, app, folder)
    assert window.close_project()
    _forbid_calculations(monkeypatch)
    fresh = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=QSettings(str(tmp_path / "fresh.ini"), QSettings.Format.IniFormat),
        restore_workspace=False,
    )
    try:
        open_saved(fresh, app, folder)
        assert not warnings
        assert fresh.project_state.segmentation_is_current
        assert fresh.project_state.filtering_result is None
        np.testing.assert_array_equal(fresh.segmentation.result_layer.data, masks)
        assert fresh.context_panel.mask_review_page.calculate_button.isEnabled()
    finally:
        fresh.close()


def test_lif_reopens_saved_series_and_timepoint(project, tmp_path, monkeypatch):
    window, app, _, _, warnings = project
    source = tmp_path / "Experiment Ω.lif"
    source.write_bytes(b"fixture for the injected BioIO reader")
    factory = lambda _: FakeBioImage()
    monkeypatch.setattr(window, "_bioimage_factory", factory)
    original = load_lif_dataset(source, 1, time_index=1, bioimage_factory=factory)
    assert window._apply_imported_dataset(original)
    folder = tmp_path / "Leica"
    save(window, app, folder)
    assert window.close_project()
    open_saved(window, app, folder)
    restored = window.project_state.original_dataset
    assert not warnings
    assert (restored.series_index, restored.time_index) == (1, 1)
    assert restored.series_identifier == original.series_identifier
    assert restored.reported_axes == original.reported_axes
    np.testing.assert_array_equal(restored.data_czyx, original.data_czyx)


@pytest.mark.parametrize(
    "shape,reported,override,time_index",
    [((3, 8, 9), "ZYX", None, 0), ((2, 3, 8, 9), "TZYX", "TZYX", 1)],
)
def test_tiff_restores_original_axes_and_explicit_overrides(project, tmp_path, shape, reported, override, time_index):
    window, app, _, _, warnings = project
    source = tmp_path / "Single-channel.ome.tif"
    data = np.arange(np.prod(shape), dtype=np.uint16).reshape(shape)
    tifffile.imwrite(source, data, ome=True, metadata={
        "axes": reported, "PhysicalSizeZ": 1., "PhysicalSizeY": .3, "PhysicalSizeX": .4,
    })
    imported = load_tiff_dataset(source, axes_override=override, time_index=time_index)
    assert window._apply_imported_dataset(imported)
    folder = tmp_path / "Native TIFF"
    save(window, app, folder)
    assert window.close_project()
    open_saved(window, app, folder)
    restored = window.project_state.original_dataset
    assert not warnings
    assert (restored.reported_axes, restored.axes_override, restored.time_index) == (reported, override, time_index)
    np.testing.assert_array_equal(restored.data_czyx, imported.data_czyx)


def test_relocation_requires_exact_source_bytes(project, tmp_path, monkeypatch):
    window, app, source, _, warnings = project
    folder = tmp_path / "Relocation"
    save(window, app, folder)
    old_hash = file_sha256(source)
    relocated = tmp_path / "Moved source.ome.tif"
    source.rename(relocated)
    assert window.close_project()
    different = tmp_path / "Different.ome.tif"
    different.write_bytes(relocated.read_bytes() + b"changed")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(different), ""))
    open_saved(window, app, folder)
    assert "SHA-256" in warnings[-1] and not window.project_state.dataset_ready
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(relocated), ""))
    open_saved(window, app, folder)
    assert window.project_state.original_dataset.source_path == relocated.resolve()
    assert window.project_state.original_dataset.source_sha256 == old_hash
    assert window.project_dirty
    save(window, app)
    assert native_project.open_project(folder)[1].source_path == relocated.resolve()


def test_corrupt_and_missing_checkpoints_leave_active_project_in_place(project, tmp_path):
    window, app, _, _, warnings = project
    masks = _segment(window)
    snapshot = build_snapshot(window)
    snapshot["state"]["segmentation_result"] = dict(
        snapshot["state"]["segmentation_result"], masks=masks.astype(np.float32)
    )
    bad = tmp_path / "Bad labels"
    native_project.save_project(bad, snapshot)
    active = window.project_state.segmentation_result
    open_saved(window, app, bad, confirm=False)
    assert "label mask" in warnings[-1]
    assert window.project_state.segmentation_result is active
    missing = tmp_path / "Missing"
    shutil.copytree(bad, missing)
    (missing / native_project.CHECKPOINT).unlink()
    open_saved(window, app, missing, confirm=False)
    assert "FileNotFoundError" in warnings[-1]
    assert window.project_state.segmentation_result is active
    (bad / native_project.CHECKPOINT).write_bytes(b"truncated")
    open_saved(window, app, bad, confirm=False)
    assert "BadZipFile" in warnings[-1]
    assert window.project_state.segmentation_result is active


def test_failed_save_leaves_previous_checkpoint_and_does_not_close(project, tmp_path, monkeypatch):
    window, app, _, _, warnings = project
    folder = tmp_path / "Recoverable"
    save(window, app, folder)
    before = (folder / native_project.CHECKPOINT).read_bytes()
    window.context_panel.segmentation_page.cellprob.setValue(.5)
    assert window.project_dirty
    monkeypatch.setattr(window, "_ask_project_changes", lambda: "save")

    def fail_replace(_temporary, target):
        assert target.name == native_project.CHECKPOINT
        raise OSError("simulated interrupted save")

    with monkeypatch.context() as change:
        change.setattr(native_project.os, "replace", fail_replace)
        assert not window.close_project()
        _wait(app, lambda: window._import_thread is None)
    assert "simulated interrupted save" in warnings[-1]
    assert window.project_dirty and window.project_state.dataset_ready
    assert (folder / native_project.CHECKPOINT).read_bytes() == before
    assert list(folder.glob(".save-*")) == []
    assert native_project.open_project(folder)[1].source_sha256
    assert not window.close_project()
    _wait(app, lambda: window._import_thread is None)
    assert not window.project_state.dataset_ready
    open_saved(window, app, folder)
    assert window.context_panel.segmentation_page.cellprob.value() == .5


@pytest.mark.parametrize("choice,closed", [("cancel", False), ("discard", True)])
def test_close_choices_preserve_or_discard_the_active_analysis(project, monkeypatch, choice, closed):
    window, _, _, _, _ = project
    with monkeypatch.context() as change:
        change.setattr(window, "_ask_project_changes", lambda: choice)
        assert window.close_project() == closed
    assert window.project_state.dataset_ready != closed


def test_source_paths_roundtrip_text_without_platform_specific_parsing(project, tmp_path):
    window, _, _, _, _ = project
    record = build_snapshot(window)
    for value in (r"C:\Users\Researcher\Series Ω.ome.tif", "/Volumes/Study A/Series Ω.ome.tif"):
        record["source"]["path"] = value
        with ZipFile(tmp_path / "paths.zip", "w") as archive:
            payload = native_project._pack(archive, record)
        with ZipFile(tmp_path / "paths.zip") as archive:
            assert native_project._unpack(archive, payload)["source"]["path"] == value
