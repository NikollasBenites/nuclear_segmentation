import os
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication, QWidget

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.normalization import mask_digest
from nuclear_segmentation.normalization_workspace import (
    calculate_normalization,
    normalization_roi_on_original_grid,
)
from nuclear_segmentation.z_correction import DEFAULTS, correct_z
from nuclear_segmentation.z_correction_workspace import (
    calculate_z_corrections,
    diagnostic_profiles,
)


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


def _open_window(qapp, tmp_path):
    source = tmp_path / "phase_2b3.ome.tif"
    z = np.arange(5, dtype=np.float32)[:, None, None]
    base = np.ones((5, 9, 10), dtype=np.float32) * (z + 1)
    base[:, 2, 2] = 0
    data = np.stack((base * 10, base * 20, base * 5)).astype(np.uint16)
    tifffile.imwrite(
        source,
        data,
        ome=True,
        metadata={
            "axes": "CZYX",
            "Channel": {"Name": ["DAPI", "MAP2", "VGLUT"]},
            "PhysicalSizeZ": 2.0,
            "PhysicalSizeY": 0.5,
            "PhysicalSizeX": 0.25,
        },
    )
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait(qapp, lambda: window._import_thread is None)
    return window, source, data


def test_independent_normalization_roi_draw_edit_clear_and_crop_intersection(
    qapp, tmp_path
):
    window, _, original = _open_window(qapp, tmp_path)
    panel = window.context_panel.preprocessing_page
    normalization = window.normalization
    assert normalization.draw_roi()
    _wait(qapp, lambda: not normalization.busy)
    assert normalization.roi_projection_layer is not None
    shapes = normalization.roi_shapes_layer
    shapes.add(np.array([[-1, 1], [1, 8], [7, 1]], dtype=float), shape_type="polygon")
    qapp.processEvents()
    assert window.project_state.normalization_roi_mask_yx is None
    assert "inside" in panel.normalization_roi_status.text()
    shapes.data = []
    vertices = np.array([[1, 1], [1, 8], [7, 1]], dtype=float)
    shapes.add(vertices, shape_type="polygon")
    qapp.processEvents()
    mask = window.project_state.normalization_roi_mask_yx
    assert mask is not None and mask.dtype == bool and mask.any()
    assert window.project_state.crop_provenance is None
    assert window.project_state.working_dataset.standardized_shape == original.shape
    assert window.project_state.normalization_roi_provenance["mask_sha256"] == mask_digest(mask)
    item = window.layer_panel.model.item_for_layer(shapes)
    assert item.parent().text() == "Annotations & Overlays"
    panel.normalization_roi_visibility_button.setChecked(False)
    assert not shapes.visible
    panel.normalization_roi_visibility_button.setChecked(True)
    assert shapes.visible

    panel.normalization_mode_combo.setCurrentIndex(
        panel.normalization_mode_combo.findData("roi_fixed")
    )
    options = panel.normalization_options()
    report, _ = calculate_normalization(window.project_state, options)
    assert report["roi_voxels"] == int(mask.sum()) * original.shape[1]
    assert report["roi_zero_fraction"] > 0
    assert normalization.calculate(options)
    _wait(qapp, lambda: not normalization.busy)
    assert "Original full-volume limits" in panel.normalization_results.text.toPlainText()
    panel.normalization_results.copy_results()
    assert "Polygon ROI limits" in QApplication.clipboard().text()
    panel.normalization_results.set_expanded(False)
    assert not panel.normalization_results.expanded
    panel.normalization_results.set_expanded(True)
    previous_digest = report["roi_sha256"]
    shapes.data = [np.array([[2, 2], [2, 7], [6, 2]], dtype=float)]
    qapp.processEvents()
    assert mask_digest(window.project_state.normalization_roi_mask_yx) != previous_digest
    assert not window.project_state.normalization_is_current

    crop_vertices = np.array([[2, 2], [2, 8], [7, 2]], dtype=float)
    from nuclear_segmentation.projection_crop import polygon_roi_mask

    bbox, crop_mask = polygon_roi_mask(crop_vertices, original.shape[-2:])
    cropped = window.project_state.apply_crop(
        bbox, crop_vertices, crop_mask, "polygon"
    )
    window._display_working_dataset(cropped)
    effective = normalization_roi_on_original_grid(window.project_state)
    assert effective.sum() <= (
        window.project_state.normalization_roi_mask_yx.sum() * original.shape[1]
    )
    report, preview = calculate_normalization(window.project_state, options)
    assert report["roi_voxels"] == int(effective.sum())
    assert preview.shape == cropped.standardized_shape[1:]
    normalization.clear_roi()
    assert normalization.reuse_crop_roi()
    preserved = window.project_state.normalization_roi_mask_yx.copy()
    window.crop_workspace.reset_crop()
    np.testing.assert_array_equal(
        window.project_state.normalization_roi_mask_yx, preserved
    )
    normalization.clear_roi()
    assert window.project_state.normalization_roi_mask_yx is None
    assert normalization.roi_shapes_layer is None
    np.testing.assert_array_equal(window.project_state.original_dataset.data_czyx, original)
    window.close()


def test_z_correction_batch_parity_independence_profiles_and_failures():
    assert DEFAULTS == {
        "percentile": 99.0,
        "sigma_planes": 3.0,
        "min_gain": 0.5,
        "max_gain": 3.0,
        "segmentation": True,
        "measurement": False,
    }
    z = np.arange(6, dtype=np.float32)[:, None, None]
    first = np.ones((6, 4, 5), dtype=np.float32) * (z + 1)
    second = np.ones((6, 4, 5), dtype=np.float32) * (7 - z)
    first[2] = 0
    first[:, 0, 0] = 0
    second[:, 0, 0] = 0
    sources = {0: first.copy(), 1: second.copy()}
    options = {
        0: {**DEFAULTS, "percentile": 99, "sigma_planes": 0},
        1: {**DEFAULTS, "percentile": 98, "sigma_planes": 2, "measurement": True},
    }
    mask = np.ones((4, 5), dtype=bool)
    mask[:, -1] = False
    reports, preview = calculate_z_corrections(
        sources, options, 1, mask, include_zeros=True
    )
    repeated_reports, repeated_preview = calculate_z_corrections(
        sources, options, 1, mask, include_zeros=True
    )
    np.testing.assert_allclose(repeated_preview, preview)
    np.testing.assert_allclose(repeated_reports[1]["gain"], reports[1]["gain"])
    expected, expected_report = correct_z(second, options[1])
    np.testing.assert_allclose(preview, expected)
    np.testing.assert_allclose(reports[1]["gain"], expected_report["gain"])
    assert reports[0]["options"]["percentile"] == 99
    assert reports[1]["options"]["percentile"] == 98
    assert reports[1]["options"]["measurement"] is True
    assert reports[0]["output_dtype"] == "float32"
    assert reports[0]["observed_profile"][2] is None
    assert np.all(preview[second == 0] == 0)
    np.testing.assert_array_equal(sources[0], first)
    np.testing.assert_array_equal(sources[1], second)
    profiles = diagnostic_profiles(second, expected, mask, include_zeros=True)
    assert len(profiles["original"]["p99"]) == second.shape[0]
    positive = diagnostic_profiles(second, expected, mask, include_zeros=False)
    assert positive["original"]["mean"] != profiles["original"]["mean"]
    with pytest.raises(ValueError, match="NaN or infinite"):
        calculate_z_corrections(
            {0: np.full((2, 3, 3), np.nan)}, {0: DEFAULTS}, 0,
            np.ones((3, 3), bool), True,
        )
    with pytest.raises(ValueError, match="unavailable"):
        calculate_z_corrections(
            {}, {0: DEFAULTS}, 0, np.ones((3, 3), bool), True
        )


def test_multichannel_z_ui_apply_routing_profiles_reset_and_close(
    qapp, tmp_path, monkeypatch
):
    window, source, original = _open_window(qapp, tmp_path)
    panel = window.context_panel.preprocessing_page.z_correction_panel
    panel.channel_list.selectAll()
    panel.inspect_combo.setCurrentIndex(panel.inspect_combo.findData(1))
    panel.fields["percentile"].setValue(98)
    panel.fields["sigma_planes"].setValue(2)
    panel.measurement.setChecked(True)
    panel._request_calculate()
    _wait(qapp, lambda: not window.z_correction.busy)
    assert set(window.project_state.z_correction_reports) == {0, 1, 2}
    assert "Preview ready" in panel.channel_list.item(0).text()
    assert window.z_correction.preview_channel_index == 1
    preview = window.z_correction.preview_layer
    assert preview.data.dtype == np.float32
    assert tuple(preview.scale) == (2.0, 0.5, 0.25)
    assert preview.metadata["nucytoseg_z_correction_preview"]
    assert len(
        [
            layer for layer in window.embedding.viewer.layers
            if layer.metadata.get("nucytoseg_z_correction_preview")
        ]
    ) == 1
    assert "Reference percentile: 98" in panel.results.text.toPlainText()
    import nuclear_segmentation.z_correction_workspace as z_workspace

    original_correct_z = z_workspace.correct_z
    monkeypatch.setattr(
        z_workspace,
        "correct_z",
        lambda volume, options: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    panel._request_calculate()
    _wait(qapp, lambda: not window.z_correction.busy)
    assert set(window.project_state.z_correction_reports) == {0, 1, 2}
    assert "failed" in panel.status.text().lower()
    monkeypatch.setattr(z_workspace, "correct_z", original_correct_z)
    panel.results.copy_results()
    assert "Channel: MAP2" in QApplication.clipboard().text()
    panel.results.set_expanded(False)
    assert not panel.results.expanded
    panel.results.set_expanded(True)
    assert window.z_correction.show_profiles(1, True)
    assert window.z_correction._dialogs
    window.z_correction._dialogs[-1].close()
    qapp.processEvents()

    roi = np.ones(original.shape[-2:], dtype=bool)
    window.project_state.set_normalization_roi(
        roi,
        [[0, 0], [0, original.shape[-1] - 1], [original.shape[-2] - 1, 0]],
        {"mask_sha256": mask_digest(roi)},
    )
    window.project_state.apply_normalization(
        {"mode": "roi_fixed", "cellpose_normalize": {"lowhigh": [1, 99]}}
    )
    assert not window.z_correction.apply([0, 1, 2])
    assert "Reset normalization" in panel.status.text()
    window.normalization.reset()
    assert window.z_correction.apply([0, 1, 2])
    assert window.project_state.z_correction_applied == {0, 1, 2}
    assert all(
        "Applied" in panel.channel_list.item(row).text()
        for row in range(panel.channel_list.count())
    )
    assert window.project_state.segmentation_channel_index == 0
    legacy = window.project_state.legacy_z_corrections()
    assert legacy["DAPI"]["segmentation"] is True
    assert legacy["MAP2"]["measurement"] is True
    window.project_state.update_channel(1, name="MAP2 renamed")
    assert 1 in window.project_state.z_correction_options
    assert "MAP2 renamed" in window.project_state.legacy_z_corrections()
    panel.inspect_combo.setCurrentIndex(panel.inspect_combo.findData(1))
    panel.fields["percentile"].setValue(97)
    qapp.processEvents()
    assert "Outdated settings" in panel.channel_list.item(1).text()
    assert 1 not in window.project_state.z_correction_reports
    assert 0 in window.project_state.z_correction_reports

    panel.channel_list.clearSelection()
    panel.channel_list.item(1).setSelected(True)
    window.z_correction.reset_selected([1])
    assert 1 not in window.project_state.z_correction_reports
    assert 0 in window.project_state.z_correction_reports
    working = window.project_state.apply_crop(
        (1, 7, 1, 8),
        [[1, 1], [1, 8], [7, 8], [7, 1]],
        selection_type="rectangle",
    )
    window._display_working_dataset(working)
    window.z_correction.invalidate_all()
    assert not window.project_state.z_correction_reports
    assert 0 in window.project_state.z_correction_options
    window.z_correction.reset_all()
    assert not window.project_state.z_correction_reports
    assert window.z_correction.preview_layer is None
    assert source.exists()
    np.testing.assert_array_equal(window.project_state.original_dataset.data_czyx, original)
    window.z_correction._thread = object()
    assert not window.close_project(confirm=False)
    window.z_correction._thread = None
    assert window.close_project(confirm=False)
    assert not window.project_state.z_correction_options
    assert len(window.embedding.viewer.layers) == 0
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait(qapp, lambda: window._import_thread is None)
    assert window.project_state.normalization_roi_mask_yx is None
    assert not window.project_state.z_correction_reports
    assert all(
        "No correction" in panel.channel_list.item(row).text()
        for row in range(panel.channel_list.count())
    )
    assert window.close_project(confirm=False)
    window.close()
