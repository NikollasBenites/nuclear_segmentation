"""Native resume tests use real disk files and the desktop worker lifecycle."""
from copy import deepcopy
import json
import shutil
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest
import tifffile
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QLineEdit, QMessageBox

from nuclear_segmentation import native_project
from nuclear_segmentation.core import file_sha256
from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.image_import import load_tiff_dataset
from nuclear_segmentation.lif_import import load_lif_dataset
from nuclear_segmentation.manual_review import ManualReview
from nuclear_segmentation.projection_crop import polygon_roi_mask
from nuclear_segmentation.scientific_input import prepare_scientific_input
from test_cellpose_execution import _fake_result
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


def _segment(window, *, x_start=1):
    state = window.project_state
    config = window.context_panel.segmentation_page.scientific_config()
    prepared = prepare_scientific_input(state, config)
    masks = np.zeros(state.working_dataset.standardized_shape[1:], dtype=np.uint32)
    masks[:, 1:3, x_start:x_start + 2] = 90000
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
    owner = state.session_revision()
    session_id = state.nuclear_session.session_id
    window._update_channel_configuration(0, "colormap", "magenta")
    window._update_channel_configuration(0, "visible", False)
    assert state.session_revision() == owner
    assert state.normalization_is_current and state.quantification_is_current
    corrected = window.z_correction.preview_layers[0].data.copy()
    normalize = window.normalization.preview_layer.data.copy()
    panel.z_correction_panel.fields["percentile"].setValue(93)
    assert state.z_correction_applied == {0}
    folder = tmp_path / "Processed"
    save(window, app, folder)
    assert native_project._manifest(folder)["version"] == 2
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
    session = state.nuclear_session
    assert session.session_id == session_id == "nuclear"
    assert session.source_channel_index == 0
    assert state.session_revision().project_id != owner.project_id
    assert session.filtering_result is state.filtering_result
    assert session.scientific_roi is state.scientific_roi
    assert session.quantification_result is state.quantification_result
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
    good = tmp_path / "Good labels"
    save(window, app, good)
    bad = tmp_path / "Bad labels"
    snapshot = _read_snapshot(good)
    session = snapshot["sessions"]["nuclear"]
    session["segmentation_result"] = dict(
        session["segmentation_result"], masks=masks.astype(np.float32)
    )
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


def _read_snapshot(folder):
    with ZipFile(folder / native_project.CHECKPOINT) as archive:
        source = native_project._unpack(archive, json.loads(archive.read("source.json")))
        payload = native_project._unpack(archive, json.loads(archive.read("state.json")))
    return dict(payload, source=source)


def _write_v1_checkpoint(folder, snapshot):
    """Write the Phase 2D.1 archive structure from a populated scientific fixture."""
    folder.mkdir()
    session = snapshot["sessions"]["nuclear"]
    (folder / native_project.MANIFEST).write_text(
        json.dumps({"format": "NuCytoSeg", "version": 1, "id": "v1-fixture"}), encoding="utf-8"
    )
    keys = ("segmentation_settings", "filter_controls", "intensity_controls", "roi_grid_compatible")
    payload = {
        key: value for key, value in snapshot.items()
        if key not in {"source", "state", "sessions", "active_session_id"}
    }
    payload["state"] = dict(snapshot["state"], **{
        key: session[key] for key in native_project.V1_SESSION_FIELDS
    })
    payload.update({key: session[key] for key in keys})
    with ZipFile(folder / native_project.CHECKPOINT, "w") as archive:
        archive.writestr("source.json", json.dumps(native_project._pack(archive, snapshot["source"])))
        archive.writestr("state.json", json.dumps(native_project._pack(archive, payload)))
        archive.writestr("revision.txt", "first")
        archive.writestr("project-id.txt", "v1-fixture")


def _measure_and_review(window, app):
    assert window.filtering.calculate()
    _wait(app, lambda: not window.filtering.busy)
    review = window.context_panel.mask_review_page
    _wait(app, lambda: not review._filter_timer.isActive())
    window.filtering.accept_roi()
    assert window.project_state.quantification_is_current


def test_completed_sessions_roundtrip_independently(project, tmp_path, monkeypatch):
    window, app, _, _, warnings = project
    state = window.project_state
    first_masks = _segment(window)
    _measure_and_review(window, app)
    first = state.active_session
    first_identity = state.mask_identity(90000)
    first_quantification = first.quantification_result
    shared_grid = state.working_dataset
    old_layer = window.segmentation.result_layer
    panel = window.context_panel.project_page
    segmentation = window.context_panel.segmentation_page
    segmentation.cellprob.setValue(.83)
    second = state.create_session("Replicate", channel_index=1)
    assert second.session_id != first.session_id
    with pytest.raises(AttributeError):
        second.session_id = first.session_id
    panel.session_combo.setCurrentIndex(panel.session_combo.findData(second.session_id))
    assert state.active_session_id == second.session_id
    assert state.working_dataset is shared_grid
    assert not state.segmentation_is_current
    assert window.segmentation.result_layer is None
    assert "MAP2" in panel.session_details.text()
    assert segmentation.cellprob.value() != .83
    second_masks = _segment(window, x_start=4)
    _measure_and_review(window, app)
    second_quantification = second.quantification_result
    second_identity = state.mask_identity(90000)
    assert first_identity.geometry_revision == second_identity.geometry_revision
    assert first_identity != second_identity
    assert window.segmentation.result_layer.metadata["session_id"] == second.session_id
    review = window.context_panel.mask_review_page
    review.set_selection(old_layer, 90000)
    assert not review.show_selected_button.isEnabled()
    assert "not available" in review.measurements.text()

    session_revisions = {
        session.session_id: (session.input_revision, session.geometry_revision, session.measurement_revision)
        for session in state.sessions.values()
    }
    window._update_channel_configuration(0, "colormap", "magenta")
    window._update_channel_configuration(1, "visible", False)
    assert all(session.quantification_is_current for session in state.sessions.values())
    panel.session_combo.setCurrentIndex(panel.session_combo.findData(first.session_id))
    assert state.quantification_result is first_quantification
    assert segmentation.cellprob.value() == pytest.approx(.83)
    np.testing.assert_array_equal(window.segmentation.result_layer.data, first_masks)
    panel.session_combo.setCurrentIndex(panel.session_combo.findData(second.session_id))
    assert state.quantification_result is second_quantification
    assert state.session_revision().session_id == second.session_id

    state.rename_session(second.session_id, "MAP2 replicate")
    folder = tmp_path / "Two analyses"
    save(window, app, folder)
    assert window.close_project()
    _forbid_calculations(monkeypatch)
    open_saved(window, app, folder)
    assert not warnings
    assert list(state.sessions) == ["nuclear", second.session_id]
    assert state.active_session_id == second.session_id
    assert state.active_session.display_name == "MAP2 replicate"
    assert "MAP2 replicate" in window.active_session_title.text()
    for session_id, masks, quant in (
        ("nuclear", first_masks, first_quantification),
        (second.session_id, second_masks, second_quantification),
    ):
        session = state.sessions[session_id]
        assert (session.input_revision, session.geometry_revision,
                session.measurement_revision) == session_revisions[session_id]
        assert session.filtering_is_current and session.scientific_roi_reviewed
        assert session.quantification_is_current and session.quantification_result == quant
        np.testing.assert_array_equal(session.segmentation_result["masks"], masks)
        np.testing.assert_array_equal(session.segmentation_result["edited_masks"], masks)
    np.testing.assert_array_equal(window.segmentation.result_layer.data, second_masks)
    assert window.context_panel.export_page.scientific_button.isEnabled()


def test_v1_migration_keeps_original_and_scientific_results(project, tmp_path):
    window, app, _, _, warnings = project
    masks = _segment(window)
    _measure_and_review(window, app)
    original_density = deepcopy(window.project_state.quantification_result)
    intermediate = tmp_path / "Fixture data"
    save(window, app, intermediate)
    old = tmp_path / "Phase 2D.1"
    _write_v1_checkpoint(old, _read_snapshot(intermediate))
    before = {path.name: path.read_bytes() for path in old.iterdir()}
    open_saved(window, app, old)
    state = window.project_state
    assert state.active_session_id == "nuclear"
    assert list(state.sessions) == ["nuclear"]
    assert state.quantification_is_current and state.scientific_roi_reviewed
    assert state.quantification_result == original_density
    np.testing.assert_array_equal(state.segmentation_result["masks"], masks)
    converted = tmp_path / "Version 2"
    save(window, app, converted, save_as=True)
    assert not warnings
    assert {path.name: path.read_bytes() for path in old.iterdir()} == before
    assert native_project._manifest(converted)["version"] == 2
    open_saved(window, app, converted)
    assert state.active_session_id == "nuclear"
    assert state.quantification_is_current and state.quantification_result == original_density


@pytest.mark.parametrize("phase", ["2D.2", "2D.3"])
def test_original_v2_sessions_restore_shared_normalization(project, tmp_path, monkeypatch, phase):
    window, app, _, _, warnings = project
    masks = _segment(window)
    folder = tmp_path / "Checkpoint"
    save(window, app, folder)
    snapshot = _read_snapshot(folder)
    for record in snapshot["sessions"].values():
        del record["display_state"]
        if phase == "2D.2":
            del record["normalization_settings"], record["normalization_configuration"], record["normalization_is_current"]
    older = tmp_path / phase
    native_project.save_project(older, snapshot)
    _forbid_calculations(monkeypatch)
    open_saved(window, app, older)
    state = window.project_state
    assert not warnings and state.segmentation_is_current
    assert state.normalization_configuration == snapshot["state"]["normalization_configuration"]
    np.testing.assert_array_equal(state.segmentation_result["masks"], masks)


def test_invalid_inactive_session_leaves_open_analysis_unchanged(project, tmp_path):
    window, app, _, _, warnings = project
    state = window.project_state
    _segment(window)
    original_session = state.active_session
    second = state.create_session("Empty")
    state.select_session(second.session_id)
    folder = tmp_path / "Valid"
    save(window, app, folder)
    snapshot = _read_snapshot(folder)
    snapshot["sessions"]["nuclear"]["geometry_revision"] = -1
    bad = tmp_path / "Invalid inactive record"
    native_project.save_project(bad, snapshot)
    open_saved(window, app, bad, confirm=False)
    assert "incompatible session metadata" in warnings[-1]
    assert state.active_session is second
    assert state.sessions["nuclear"] is original_session
    assert window.project_directory == folder


def test_project_actions_are_accessible_at_narrow_width_before_and_after_resume(project, tmp_path):
    window, app, _, _, _ = project
    window.show()
    window.set_workflow("project")
    panel = window.context_panel.project_page
    scroll = window.context_panel.stack.currentWidget()
    buttons = [panel.project_actions.itemAt(i).widget() for i in range(panel.project_actions.count())]
    buttons.extend([panel.open_button, panel.load_button, panel.close_button])

    def assert_narrow_actions():
        window.resize(1024, 680)
        window.splitter.setSizes([210, 490, 320])
        app.processEvents()
        viewport = scroll.viewport()
        assert 0 < viewport.width() < 310
        assert scroll.verticalScrollBar().maximum() > 0
        for button in buttons:
            scroll.ensureWidgetVisible(button, 0, 0)
            app.processEvents()
            rect = button.rect().translated(button.mapTo(viewport, button.rect().topLeft()))
            assert viewport.rect().contains(rect), button.text()

    assert_narrow_actions()
    _segment(window)
    folder = tmp_path / "Narrow project"
    save(window, app, folder)
    open_saved(window, app, folder)
    assert window.project_state.segmentation_is_current
    assert_narrow_actions()


def test_independent_cytoplasmic_execution_measurements_and_resume(project, tmp_path, monkeypatch):
    window, app, _, image, warnings = project
    state = window.project_state
    panel = window.context_panel.segmentation_page
    review = window.context_panel.mask_review_page
    inputs = []

    def runner(scientific, progress):
        inputs.append(scientific.namespace["model_input_volume"])
        return _fake_result(scientific)

    window.segmentation.runner = runner
    prep = window.context_panel.preprocessing_page
    prep.normalization_mode_combo.setCurrentIndex(prep.normalization_mode_combo.findData("percentile"))
    assert window.normalization.calculate(prep.normalization_options())
    _wait(app, lambda: not window.normalization.busy)
    assert window.normalization.apply()
    assert window.segmentation.run()
    _wait(app, lambda: not window.segmentation.busy)
    nuclear = state.nuclear_session
    original = nuclear.segmentation_result
    nuclear_id = state.mask_identity(1)
    assert window.filtering.calculate()
    _wait(app, lambda: not window.filtering.busy)
    nuclear_properties = nuclear.filtering_result["properties"].copy()
    prep.normalization_upper.setValue(97)
    assert not nuclear.normalization_is_current and nuclear.segmentation_is_current

    cyto = state.create_session("MAP2 objects", scientific_role="cytoplasmic", channel_index=1)
    state.select_session(cyto.session_id)
    assert not panel.model_name.text()
    assert prep.normalization_channel_combo.currentData() == 1
    prep.normalization_mode_combo.setCurrentIndex(prep.normalization_mode_combo.findData("disabled"))
    assert window.normalization.calculate(prep.normalization_options())
    _wait(app, lambda: not window.normalization.busy)
    assert window.normalization.apply()
    assert nuclear.segmentation_is_current and not nuclear.normalization_is_current
    panel.model_name.setText("/models/MAP2")
    panel.cellprob.setValue(-1.25)
    panel.mode.setCurrentIndex(panel.mode.findData(False))
    panel.stitch.setValue(0.35)
    _wait(app, panel.execute_button.isEnabled)
    assert window.segmentation.run()
    _wait(app, lambda: not window.segmentation.busy)
    completed = cyto.segmentation_result
    assert cyto.segmentation_is_current and nuclear.segmentation_result is original
    assert completed["cellpose_normalize"] is False
    assert original["normalization_provenance"]["channel_index"] == 0
    assert completed["scientific_config"]["MODEL_NAME"] == "/models/MAP2"
    assert completed["scientific_config"]["CELLPROB_THRESHOLD"] == -1.25
    assert completed["scientific_config"]["CELLPOSE_DO_3D"] is False
    assert (completed["source_channel_index"], completed["segmentation_channel"]) == (1, "MAP2")
    assert completed["session_id"] == cyto.session_id
    assert completed["geometry_revision"] == cyto.geometry_revision
    assert original["segmentation_channel"] == "DAPI"
    for index, pixels in enumerate(inputs):
        np.testing.assert_array_equal(pixels, image[index])
    assert state.mask_identity(1) != nuclear_id
    assert window.segmentation.result_layer.data is completed["masks"]
    assert window.segmentation.result_layer.metadata["mask_type"] == "cytoplasmic"

    def forbidden(*args, **kwargs):
        raise AssertionError("Cytoplasmic filtering invoked nuclear counting or rings")

    with monkeypatch.context() as patch:
        for target in ("filtering_workspace.calculate_perinuclear_marker_metrics",
                       "filtering_workspace.apply_filter_settings", "native_project.density_z_planes"):
            patch.setattr("nuclear_segmentation." + target, forbidden)
        assert window.filtering.calculate()
        _wait(app, lambda: not window.filtering.busy)
        measured = cyto.filtering_result
        assert measured["retained_ids"] == (1,)
        assert not measured["perinuclear_definitions"] and window.filtering.roi_layer is None
        assert "excluded_by_z_guard" not in measured["properties"]
        assert "counting_z_px" not in measured["properties"]
        assert "z_counting_rule" not in measured["settings"]
        from nuclear_segmentation.core import calculate_mask_properties
        config = completed["scientific_config"]
        reference, _ = calculate_mask_properties(
            completed["masks"], {c.name: image[c.index] for c in state.channels},
            config["CHANNELS"], state.voxel_spacing_zyx_um, config["GLOBAL_BACKGROUND_PERCENTILE"],
        )
        pd.testing.assert_frame_equal(measured["properties"][reference.columns], reference)
        pd.testing.assert_frame_equal(nuclear_properties[reference.columns], reference)
        # Use a shape-independent intensity cutoff to exclude this synthetic object.
        review.intensity_master.setChecked(True)
        _, enabled, low, high, _ = review.intensity_controls[1]
        enabled.setChecked(True)
        low.setValue(high.value())
        window.filtering.apply_filters()
        _wait(app, lambda: not review._filter_timer.isActive())
        assert not state.filtering_result["retained_ids"]
        assert not state.quantification_is_current
        assert "nuclear sessions" in window.context_panel.quantification_page.status.text()
        assert not window.context_panel.export_page.scientific_button.isEnabled()

    panel.cellprob.setValue(-2.5)
    def failing(scientific, progress):
        raise RuntimeError("failed MAP2 rerun")
    window.segmentation.runner = failing
    assert window.segmentation.run()
    _wait(app, lambda: not window.segmentation.busy)
    assert "failed MAP2 rerun" in panel.execution_log.toPlainText()
    assert cyto.segmentation_result is completed and cyto.segmentation_is_current
    assert nuclear.segmentation_result is original and nuclear.segmentation_is_current
    cytoplasmic_properties = cyto.filtering_result["properties"].copy()
    folder = tmp_path / "Both sessions"
    save(window, app, folder)
    assert window.close_project()
    _forbid_calculations(monkeypatch)
    open_saved(window, app, folder)
    assert not warnings
    assert state.active_session_id == cyto.session_id
    assert state.segmentation_is_current and state.filtering_is_current
    assert panel.cellprob.value() == -2.5
    assert state.segmentation_result["scientific_config"] == completed["scientific_config"]
    np.testing.assert_array_equal(state.segmentation_result["masks"], completed["masks"])
    pd.testing.assert_frame_equal(state.filtering_result["properties"], cytoplasmic_properties)
    assert state.normalization_configuration["mode"] == "disabled"
    state.select_session("nuclear")
    assert state.segmentation_is_current and state.filtering_is_current
    assert window.segmentation.result_layer.data is state.segmentation_result["masks"]
    assert not state.normalization_is_current and state.normalization_configuration["mode"] == "percentile"
    assert prep.normalization_upper.value() == 97
    pd.testing.assert_frame_equal(state.filtering_result["properties"], nuclear_properties)
    np.testing.assert_array_equal(state.segmentation_result["masks"], original["masks"])


def test_nuclear_session_worker_uses_its_source_channel(project, monkeypatch):
    window, app, _, image, _ = project
    state = window.project_state
    panel = window.context_panel.project_page
    inputs = []

    def runner(scientific, progress):
        pixels = scientific.namespace["model_input_volume"]
        inputs.append(pixels.copy())
        result = _fake_result(scientific)
        result["masks"].fill(0)
        result["masks"][:, 1, 1 if pixels[0, 0, 0] == image[0, 0, 0, 0] else 4] = 1
        return result

    window.segmentation.runner = runner
    assert window.segmentation.run()
    _wait(app, lambda: not window.segmentation.busy)
    first_result = state.segmentation_result
    def choose_second_channel(dialog):
        assert dialog.windowTitle() == "New Segmentation Session"
        dialog.findChild(QLineEdit).setText("nuclear2")
        source = next(combo for combo in dialog.findChildren(QComboBox) if combo.findData(1) >= 0)
        source.setCurrentIndex(source.findData(1))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", choose_second_channel)
    panel.new_session_button.click()
    second = state.active_session
    assert second.display_name == "nuclear2"
    assert window.segmentation.result_layer is None
    assert window.segmentation.run()
    _wait(app, lambda: not window.segmentation.busy)

    np.testing.assert_array_equal(inputs[0], image[0])
    np.testing.assert_array_equal(inputs[1], image[1])
    second_result = state.segmentation_result
    assert first_result["segmentation_channel"] == "DAPI"
    assert second_result["segmentation_channel"] == "MAP2"
    assert not np.array_equal(first_result["masks"], second_result["masks"])

    panel.session_combo.setCurrentIndex(panel.session_combo.findData("nuclear"))
    np.testing.assert_array_equal(window.segmentation.result_layer.data, first_result["masks"])
    panel.session_combo.setCurrentIndex(panel.session_combo.findData(second.session_id))
    np.testing.assert_array_equal(window.segmentation.result_layer.data, second_result["masks"])
    assert window.segmentation.result_layer.name == "nuclear2 · Raw Cellpose masks"
    summary = window.context_panel.segmentation_page.result_summary
    assert "Session: nuclear2" in summary.text()
    assert "Input channel: MAP2" in summary.text()
    layer = window.segmentation.result_layer
    state.rename_session(second.session_id, "Replicate")
    assert window.segmentation.result_layer is layer
    assert layer.name == "Replicate · Raw Cellpose masks"
    assert "Session: Replicate" in summary.text()


def test_session_display_survives_switches_filter_refresh_and_resume(project, tmp_path, monkeypatch):
    window, app, _, _, warnings = project
    state = window.project_state
    viewer = window.embedding.viewer
    panel = window.context_panel.mask_review_page
    raw_masks = _segment(window)
    _measure_and_review(window, app)
    nuclear = state.active_session
    density = deepcopy(state.quantification_result)
    properties = state.filtering_result["properties"].copy()
    retained = state.filtering_result["retained_ids"]
    original_revision = state.session_revision()
    channel_layers = [window._layer_for_channel(i) for i in (0, 1)]
    channel_layers[1].visible = False
    raw = window.segmentation.result_layer
    raw.opacity = .22
    window.filtering.filtered_layer.opacity = .43
    window.filtering.roi_layer.visible = True
    panel.show_raw_button.click()
    assert raw.visible and not window.filtering.filtered_layer.visible
    window.viewer_toolbar.activate_tool("review")
    window.mask_selection.select_mask(raw, 90000)
    panel.show_selected_button.click()
    assert raw.show_selected_label and raw.selected_label == 90000

    cyto = state.create_session("MAP2", scientific_role="cytoplasmic", channel_index=1)
    state.select_session(cyto.session_id)
    channel_layers[0].visible = False
    channel_layers[1].visible = True
    window.context_panel.segmentation_page.model_name.setText("test-model")
    cyto_masks = _segment(window, x_start=4)
    assert window.filtering.calculate()
    _wait(app, lambda: not window.filtering.busy)
    filtered = window.filtering.filtered_layer
    filtered.opacity = .77
    window.segmentation.result_layer.opacity = .19
    panel.show_all_button.click()
    assert filtered.visible and not window.segmentation.result_layer.visible
    window.mask_selection.select_mask(filtered, 90000)
    panel.show_selected_button.click()
    assert filtered.show_selected_label and filtered.selected_label == 90000
    # Recalculate measurements through the normal worker, replacing the layer.
    assert window.filtering.calculate()
    _wait(app, lambda: not window.filtering.busy)
    assert filtered not in viewer.layers
    assert window.filtering.filtered_layer.opacity == .77
    assert window.filtering.filtered_layer.show_selected_label
    assert window.mask_selection.selected_layer is window.filtering.filtered_layer
    assert window.mask_selection.selected_mask_id == 90000
    panel.show_all_button.click()

    def check_nuclear():
        r, f = window.segmentation.result_layer, window.filtering.filtered_layer
        assert [window._layer_for_channel(i).visible for i in (0, 1)] == [True, False]
        assert r.opacity == .22 and f.opacity == .43 and window.filtering.roi_layer.visible
        assert r.visible and not f.visible and r.show_selected_label and r.selected_label == 90000
        assert r.data is state.segmentation_result["masks"]
        assert tuple(r.scale) == state.voxel_spacing_zyx_um
        assert state.owns_mask_layer(r) and state.mask_identity(90000).session_id == nuclear.session_id
        np.testing.assert_array_equal(r.data, raw_masks)
        pd.testing.assert_frame_equal(state.filtering_result["properties"], properties)
        assert state.filtering_result["retained_ids"] == retained
        assert state.quantification_result == density and state.quantification_is_current
        assert state.segmentation_is_current and state.filtering_is_current

    def check_cyto():
        r, f = window.segmentation.result_layer, window.filtering.filtered_layer
        assert [window._layer_for_channel(i).visible for i in (0, 1)] == [False, True]
        assert r.opacity == .19 and f.opacity == .77
        assert not r.visible and f.visible and not f.show_selected_label
        assert state.owns_mask_layer(f)
        assert f.data is state.filtering_result["filtered_masks"]
        np.testing.assert_array_equal(r.data, cyto_masks)

    for _ in range(3):
        state.select_session(nuclear.session_id)
        check_nuclear()
        assert state.session_revision() == original_revision
        assert window._layer_for_channel(0) is channel_layers[0]
        state.select_session(cyto.session_id)
        check_cyto()
        assert not any(layer.metadata.get("nucytoseg_selection_highlight") for layer in viewer.layers)

    folder = tmp_path / "Session display"
    state.select_session(nuclear.session_id)
    save(window, app, folder)
    assert window.close_project()
    _forbid_calculations(monkeypatch)
    open_saved(window, app, folder)
    assert not warnings and state.active_session_id == nuclear.session_id
    check_nuclear()
    state.select_session(cyto.session_id)
    check_cyto()
    # Manual layer visibility is also a session preference.
    window.segmentation.result_layer.visible = True
    assert window.filtering.filtered_layer.visible
    state.select_session(nuclear.session_id)
    check_nuclear()
    state.select_session(cyto.session_id)
    assert window.segmentation.result_layer.visible and window.filtering.filtered_layer.visible
    assert "raw and filtered" in panel.visualization_status.text()
