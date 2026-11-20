import os
from pathlib import Path
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication, QWidget

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.normalization import (
    compare_percentiles,
    mask_digest,
    normalize_preview,
)
from nuclear_segmentation.normalization_workspace import (
    calculate_normalization,
    cellpose_configuration,
    normalization_fingerprint,
    polygon_roi_on_original_grid,
    validate_percentiles,
)
from nuclear_segmentation.project_state import (
    ChannelDescriptor,
    ImportedDataset,
    ProjectState,
)
from nuclear_segmentation.projection_crop import polygon_roi_mask
from nuclear_segmentation.z_correction import DEFAULTS as Z_DEFAULTS, correct_z


class FakeQtViewer(QWidget):
    def __init__(self, viewer, show_welcome_screen=False):
        super().__init__()
        self.viewer = viewer


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(qapp, predicate, timeout=5):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        sleep(0.01)
    raise AssertionError("Timed out waiting for Qt worker")


def _dataset(path: Path, dtype=np.uint16):
    values = np.arange(2 * 3 * 8 * 9, dtype=np.float64).reshape(2, 3, 8, 9)
    values[0, :, 2, 2] = 0
    if np.issubdtype(dtype, np.integer):
        values = values.astype(dtype)
    else:
        values = (values / 10).astype(dtype)
    return ImportedDataset(
        source_path=path,
        source_format="TIFF",
        series_index=0,
        series_identifier="0",
        series_name="Image 0",
        reported_axes="CZYX",
        reported_shape=values.shape,
        standardized_shape=values.shape,
        dtype=str(values.dtype),
        data_czyx=values,
        voxel_spacing_zyx_um=(2.5, 0.6, 0.3),
        calibration_source="test metadata",
        channels=(
            ChannelDescriptor(0, "DAPI", "DAPI", "segmentation", "cyan", True),
            ChannelDescriptor(1, "MAP2", "MAP2", "measurement", "magenta", True),
        ),
    )


@pytest.mark.parametrize("dtype", [np.uint16, np.float32])
def test_polygon_normalization_reuses_legacy_math_and_excludes_fill_zeros(
    tmp_path, dtype
):
    source = tmp_path / "source.tif"
    source.write_bytes(b"identity")
    state = ProjectState()
    dataset = _dataset(source, dtype)
    original = dataset.data_czyx.copy()
    state.set_dataset(dataset)
    vertices = np.array([[1, 2], [1, 7], [6, 2]], dtype=float)
    bbox, mask = polygon_roi_mask(vertices, dataset.standardized_shape[-2:])
    state.apply_crop(bbox, vertices, mask, "polygon")
    full_roi = np.zeros(dataset.standardized_shape[-2:], dtype=bool)
    full_roi[bbox[0] : bbox[1], bbox[2] : bbox[3]] = mask
    state.set_normalization_roi(full_roi, vertices, {"source": "test"})
    options = {
        "channel_index": 0,
        "mode": "roi_fixed",
        "lower": 1.0,
        "upper": 99.0,
        "norm3D": True,
    }
    report, preview = calculate_normalization(state, options)
    roi = polygon_roi_on_original_grid(state)
    expected = compare_percentiles(original[0], roi, 1.0, 99.0)
    assert report["roi_lowhigh"] == expected["roi_lowhigh"]
    assert report["full_lowhigh"] == expected["full_lowhigh"]
    assert report["roi_zero_fraction"] == expected["roi_zero_fraction"]
    assert report["roi_voxels"] == int(mask.sum()) * original.shape[1]
    expected_preview = normalize_preview(
        state.working_dataset.data_czyx[0], expected["roi_lowhigh"]
    )
    expected_preview[:, ~mask] = 0
    np.testing.assert_allclose(preview, expected_preview)
    assert np.all(preview[:, ~mask] == 0)
    np.testing.assert_array_equal(state.original_dataset.data_czyx, original)
    assert report["relative_gain"] == pytest.approx(
        np.ptp(expected["full_lowhigh"]) / np.ptp(expected["roi_lowhigh"])
    )
    configuration = cellpose_configuration(report)
    assert configuration["cellpose_normalize"]["lowhigh"] == expected["roi_lowhigh"]
    assert configuration["cellpose_normalize"]["norm3D"] is True
    assert report["working_offset_yx"] == [bbox[0], bbox[2]]
    assert report["voxel_spacing_zyx_um"] == [2.5, 0.6, 0.3]


def test_normalization_validation_identity_and_channel_rename(tmp_path):
    with pytest.raises(ValueError, match="0 <= lower"):
        validate_percentiles(99, 1)
    with pytest.raises(ValueError, match="0 <= lower"):
        validate_percentiles(np.nan, 99)
    source = tmp_path / "source.tif"
    source.write_bytes(b"identity")
    state = ProjectState()
    state.set_dataset(_dataset(source))
    options = {
        "channel_index": 0,
        "mode": "percentile",
        "lower": 1,
        "upper": 99,
        "norm3D": False,
    }
    before = normalization_fingerprint(state, options)
    state.update_channel(0, name="Nuclear stain")
    after = normalization_fingerprint(state, options)
    assert before["channel_index"] == after["channel_index"] == 0
    assert after["channel_name"] == "Nuclear stain"
    report, _ = calculate_normalization(state, options)
    config = cellpose_configuration(report)
    assert config["cellpose_normalize"] == {
        "normalize": True,
        "percentile": [1.0, 99.0],
        "norm3D": False,
    }


def test_z_corrected_normalization_crop_roi_domain_and_zeros(tmp_path):
    source = tmp_path / "corrected-source.tif"
    source.write_bytes(b"identity")
    state = ProjectState()
    state.set_dataset(_dataset(source))
    vertices = np.array([[1, 1], [1, 7], [6, 1]], dtype=float)
    bbox, crop_mask = polygon_roi_mask(vertices, (8, 9))
    state.apply_crop(bbox, vertices, crop_mask, "polygon")
    full_roi = np.zeros((8, 9), dtype=bool)
    full_roi[bbox[0]:bbox[1], bbox[2]:bbox[3]] = crop_mask
    state.set_normalization_roi(full_roi, vertices, {"source": "test"})
    state.set_z_correction_preview(
        0,
        Z_DEFAULTS,
        {
            "options": dict(Z_DEFAULTS),
            "source_path": str(source),
            "series_index": 0,
            "series_identifier": "0",
            "valid_xy_sha256": mask_digest(crop_mask),
        },
    )
    state.apply_z_corrections([0])
    options = {
        "channel_index": 0,
        "mode": "cellpose_default",
        "lower": 1.0,
        "upper": 99.0,
        "norm3D": True,
        "input_source": "z_corrected",
    }
    report, preview = calculate_normalization(state, options)
    corrected, _ = correct_z(state.working_dataset.data_czyx[0], Z_DEFAULTS)
    expected_limits = np.percentile(corrected[:, crop_mask], [1, 99])
    np.testing.assert_allclose(report["full_lowhigh"], expected_limits)
    assert report["input_source"] == "z_corrected"
    assert report["z_correction_provenance"]["options"] == Z_DEFAULTS
    assert report["roi_voxels"] == int(crop_mask.sum()) * corrected.shape[0]
    assert report["roi_zero_fraction"] > 0
    assert report["outside_fraction"] == pytest.approx(0.0)
    assert np.all(preview[:, ~crop_mask] == 0)
    assert tuple(preview.shape) == tuple(state.working_dataset.data_czyx[0].shape)


def _write_ome(path):
    data = np.arange(2 * 3 * 8 * 9, dtype=np.uint16).reshape(2, 3, 8, 9)
    data[0, :, 2, 2] = 0
    tifffile.imwrite(
        path,
        data,
        ome=True,
        metadata={
            "axes": "CZYX",
            "Channel": {"Name": ["DAPI", "MAP2"]},
            "PhysicalSizeZ": 2.5,
            "PhysicalSizeY": 0.6,
            "PhysicalSizeX": 0.3,
        },
    )
    return data


def _window_with_tiff(qapp, tmp_path):
    source = tmp_path / "normalization.ome.tif"
    data = _write_ome(source)
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait(qapp, lambda: window._import_thread is None)
    return window, source, data


def test_desktop_preview_apply_invalidate_repeat_reset_and_cleanup(qapp, tmp_path):
    window, source, original = _window_with_tiff(qapp, tmp_path)
    panel = window.context_panel.preprocessing_page
    assert panel.normalization_channel_combo.currentData() == 0
    panel.normalization_mode_combo.setCurrentIndex(
        panel.normalization_mode_combo.findData("percentile")
    )
    source_copy = window.project_state.original_dataset.data_czyx.copy()
    assert window.normalization.calculate(panel.normalization_options())
    _wait(qapp, lambda: not window.normalization.busy)
    preview = window.normalization.preview_layer
    assert preview is not None
    assert preview.metadata["nucytoseg_normalization_preview"]
    assert tuple(preview.scale) == (2.5, 0.6, 0.3)
    assert tuple(preview.translate) == (0.0, 0.0, 0.0)
    item = window.layer_panel.model.item_for_layer(preview)
    assert item.parent().text() == "Annotations & Overlays"
    assert window.normalization.apply()
    assert window.project_state.normalization_is_current
    assert window.project_state.normalization_configuration["mode"] == "percentile"
    panel.normalization_lower.setValue(2.0)
    qapp.processEvents()
    assert window.normalization.preview_layer is None
    assert not window.project_state.normalization_is_current
    assert "Recalculate" in panel.normalization_status.text()
    assert window.normalization.calculate(panel.normalization_options())
    _wait(qapp, lambda: not window.normalization.busy)
    assert len(
        [
            layer for layer in window.embedding.viewer.layers
            if layer.metadata.get("nucytoseg_normalization_preview")
        ]
    ) == 1
    window.project_state.update_channel(0, name="Nuclear stain")
    qapp.processEvents()
    assert panel.normalization_channel_combo.currentData() == 0
    assert panel.normalization_channel_combo.currentText() == "Nuclear stain"
    assert window.normalization.preview_layer is None
    assert window.normalization.calculate(panel.normalization_options())
    _wait(qapp, lambda: not window.normalization.busy)
    assert window.crop_workspace.generate_projection(0, "maximum")
    _wait(qapp, lambda: not window.crop_workspace.busy)
    assert window.crop_workspace.start_drawing("rectangle")
    window.crop_workspace.selection_layer.add(
        np.array([[1, 2], [1, 7], [6, 7], [6, 2]], dtype=float),
        shape_type="rectangle",
    )
    assert window.crop_workspace.preview_crop()
    assert window.crop_workspace.confirm_crop()
    assert window.normalization.preview_layer is None
    crop_provenance = window.project_state.crop_provenance
    window.normalization.reset()
    assert window.normalization.preview_layer is None
    assert window.project_state.normalization_configuration["cellpose_normalize"] is True
    assert window.project_state.crop_provenance is crop_provenance
    np.testing.assert_array_equal(window.project_state.original_dataset.data_czyx, source_copy)
    assert source.exists()
    window.close()


def test_applied_z_preview_source_cellpose_default_and_invalidation(
    qapp, tmp_path, monkeypatch
):
    window, _, _ = _window_with_tiff(qapp, tmp_path)
    preprocessing = window.context_panel.preprocessing_page
    window.set_workflow("preprocessing")
    assert window.navigation.current_subpage == "crop_roi"
    preprocessing.generate_button.click()
    _wait(qapp, lambda: not window.crop_workspace.busy)
    projection = window.crop_workspace.projection_layer
    assert projection is not None
    window.navigation.set_subpage("z_correction", emit=True)
    z_panel = preprocessing.z_correction_panel
    z_panel.channel_list.item(0).setSelected(True)
    z_panel.calculate_button.click()
    _wait(qapp, lambda: not window.z_correction.busy)
    temporary = window.z_correction.preview_layers[0]
    assert window.z_correction.apply([0])
    assert temporary in window.embedding.viewer.layers
    assert window.z_correction.preview_layers[0] is temporary
    assert temporary.metadata["z_correction_applied"] is True
    assert "applied" in temporary.name.lower()
    assert 0 in window.project_state.z_correction_reports
    assert 0 in window.project_state.z_correction_options
    assert 0 in window.project_state.z_correction_applied
    window.navigation.set_subpage("normalization", emit=True)
    assert preprocessing.normalization_input_combo.currentData() == "z_corrected"
    original_position = preprocessing.normalization_input_combo.findData(
        "working_original"
    )
    assert original_position >= 0
    preprocessing.normalization_input_combo.setCurrentIndex(original_position)
    assert preprocessing.normalization_options()["input_source"] == "working_original"
    preprocessing.normalization_input_combo.setCurrentIndex(
        preprocessing.normalization_input_combo.findData("z_corrected")
    )

    import nuclear_segmentation.normalization_workspace as workspace
    calls = []
    original_correct_z = workspace.correct_z
    def tracked(volume, options):
        calls.append((volume, options))
        return original_correct_z(volume, options)
    monkeypatch.setattr(workspace, "correct_z", tracked)
    options = preprocessing.normalization_options()
    assert options["mode"] == "cellpose_default"
    preprocessing.normalization_calculate_button.click()
    assert window.normalization.busy
    _wait(qapp, lambda: not window.normalization.busy)
    assert len(calls) == 1
    corrected, _ = correct_z(
        window.project_state.working_dataset.data_czyx[0],
        window.project_state.z_correction_options[0],
    )
    limits = np.percentile(corrected, [1, 99])
    expected = normalize_preview(corrected, limits)
    np.testing.assert_allclose(window.normalization.preview_layer.data, expected)
    assert window.normalization.preview_layer.metadata["normalization_input_source"] == "z_corrected"
    assert window.normalization.apply()
    assert window.project_state.normalization_configuration["cellpose_normalize"] is True
    assert window.project_state.normalization_configuration["provenance"]["input_source"] == "z_corrected"

    preview = window.normalization.preview_layer
    report = window.project_state.z_correction_reports[0]
    window.navigation.set_subpage("crop_roi", emit=True)
    assert window.crop_workspace.projection_layer is projection
    window.navigation.set_subpage("z_correction", emit=True)
    assert window.z_correction.preview_layers[0] is temporary
    window.set_workflow("export")
    window.navigation.set_subpage("multichannel_images", emit=True)
    window.set_workflow("preprocessing")
    assert window.navigation.current_subpage == "z_correction"
    window.navigation.set_subpage("normalization", emit=True)
    assert window.normalization.preview_layer is preview
    assert window.project_state.z_correction_reports[0] is report
    assert window.project_state.normalization_is_current
    assert len(calls) == 1
    z_panel.fields["percentile"].setValue(98.0)
    qapp.processEvents()
    assert window.normalization.preview_layer is not None
    assert window.project_state.normalization_is_current
    assert preprocessing.normalization_input_combo.currentData() == "z_corrected"
    assert temporary.metadata["z_correction_preview_status"] == "outdated"
    assert 0 in window.project_state.z_correction_applied
    window.close()


def test_close_project_cancel_clear_reopen_and_preserve_layout(qapp, tmp_path, monkeypatch):
    window, source, _ = _window_with_tiff(qapp, tmp_path)
    geometry = bytes(window.saveGeometry())
    sizes = window.splitter.sizes()
    vertices = np.array([[1, 2], [1, 7], [6, 2]], dtype=float)
    bbox, roi_mask = polygon_roi_mask(vertices, (8, 9))
    cropped = window.project_state.apply_crop(bbox, vertices, roi_mask, "polygon")
    window._display_working_dataset(cropped)
    labels = window.embedding.viewer.add_labels(
        np.ones(cropped.standardized_shape[1:], dtype=np.uint8), name="Review masks"
    )
    window.embedding.viewer.layers.selection.active = labels
    window.mask_selection._selected_by_layer[labels] = 1
    window.mask_selection._emit_selection()
    panel = window.context_panel.preprocessing_page
    assert window.normalization.calculate(panel.normalization_options())
    _wait(qapp, lambda: not window.normalization.busy)
    monkeypatch.setattr(window, "_ask_project_changes", lambda: "cancel")
    assert not window.close_project()
    assert window.project_state.dataset_ready
    monkeypatch.setattr(window, "_ask_project_changes", lambda: "discard")
    assert window.close_project()
    assert not window.project_state.dataset_ready
    assert len(window.embedding.viewer.layers) == 0
    assert window.selected_mask_panel.mask_id_label.text() == "Mask ID: —"
    assert window.project_state.roi_mask_yx is None
    assert window.project_state.crop_provenance is None
    assert window.project_state.normalization_configuration is None
    assert window.context_panel.project_page.status_label.text() == "No image loaded"
    assert not window.close_project_action.isEnabled()
    assert window.splitter.sizes() == sizes
    assert bytes(window.saveGeometry()) == geometry
    assert source.exists()
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait(qapp, lambda: window._import_thread is None)
    assert window.project_state.dataset_ready
    assert len(window.embedding.viewer.layers) == 2
    assert window.close_project(confirm=False)
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait(qapp, lambda: window._import_thread is None)
    window.normalization._thread = object()
    assert not window.close_project(confirm=False)
    assert window.project_state.dataset_ready
    window.normalization._thread = None
    assert window.close_project(confirm=False)
    window.close()
