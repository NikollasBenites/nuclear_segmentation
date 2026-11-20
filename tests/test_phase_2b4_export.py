import json
import os
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import tifffile
from qtpy.QtCore import QModelIndex, QPoint, QSettings, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QWidget

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.image_export_panel import ImageExportPanel


class FakeQtViewer(QWidget):
    def __init__(self, viewer, show_welcome_screen=False):
        super().__init__()
        self.viewer = viewer


def _wait(qapp, predicate, timeout=8):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        sleep(0.01)
    raise AssertionError("Timed out waiting for TIFF export")


def _window(qapp, tmp_path):
    source = tmp_path / "source.ome.tif"
    data = np.stack(
        (
            np.arange(3 * 5 * 6, dtype=np.float32).reshape(3, 5, 6),
            np.linspace(0, 5, 3 * 5 * 6, dtype=np.float32).reshape(3, 5, 6),
            np.zeros((3, 5, 6), dtype=np.float32),
        )
    )
    tifffile.imwrite(
        source,
        data,
        ome=True,
        metadata={
            "axes": "CZYX",
            "Channel": {"Name": ["DNA", "Marker A", "Empty"]},
            "PhysicalSizeZ": 1.5,
            "PhysicalSizeY": 0.4,
            "PhysicalSizeX": 0.2,
        },
    )
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat),
        restore_workspace=False,
    )
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait(qapp, lambda: window._import_thread is None)
    window.set_workflow("export")
    window.navigation.set_subpage("multichannel_images", emit=True)
    prep_panel = window.context_panel.export_page.image_preparation_panel
    prep_panel.channel_list.selectAll()
    prep_panel._routes = {0: "original", 1: "normalized", 2: "original"}
    prep_panel.preview_button.click()
    assert window.image_preparation.busy
    _wait(qapp, lambda: not window.image_preparation.busy)
    return window, source, data


def _uint16_expected(array):
    maximum = float(np.max(array))
    if maximum == 0:
        return np.zeros(array.shape, dtype=np.uint16)
    return np.rint(np.clip(np.asarray(array, dtype=np.float64) / maximum, 0, 1) * 65535).astype(np.uint16)


def test_desktop_ome_uint16_default_order_pixels_metadata_and_provenance(tmp_path):
    qapp = QApplication.instance() or QApplication([])
    window, source, original = _window(qapp, tmp_path)
    panel = window.context_panel.export_page
    assert panel.format_combo.currentData() == "ome"
    assert panel.pixel_type_combo.currentData() == "uint16"
    assert panel.selected_channel_order() == [0, 1, 2]
    panel.channels.setCurrentRow(2)
    panel._move_current(-1)
    panel._move_current(-1)
    assert panel.selected_channel_order() == [2, 0, 1]
    order = panel.selected_channel_order()
    prepared = {
        index: np.array(window.image_preparation.prepared_channels[index], copy=True)
        for index in order
    }
    output = tmp_path / "prepared.ome.tif"
    assert window.image_export.export(output, order, "ome", "uint16")
    assert not window.close_project(confirm=False)
    _wait(qapp, lambda: not window.image_export.busy)
    results = panel.results.text.toPlainText()
    assert "Multichannel TIFF\nStatus: Completed" in results
    assert f"Destination: {output}" in results
    assert "multichannel image TIFF and provenance JSON" in results
    with tifffile.TiffFile(output) as tif:
        assert tif.series[0].axes == "CZYX"
        saved = tif.series[0].asarray()
        expected = np.stack([_uint16_expected(prepared[index]) for index in order])
        np.testing.assert_array_equal(saved, expected)
        root = ET.fromstring(tif.ome_metadata)
        pixels = root.find(".//{*}Pixels")
        assert float(pixels.attrib["PhysicalSizeZ"]) == 1.5
        assert float(pixels.attrib["PhysicalSizeY"]) == 0.4
        assert float(pixels.attrib["PhysicalSizeX"]) == 0.2
        assert [channel.attrib["Name"] for channel in pixels.findall("{*}Channel")] == [
            "Empty",
            "DNA",
            "Marker A",
        ]
    record = json.loads((tmp_path / "prepared.ome.tif.json").read_text())
    assert record["axes"] == "CZYX"
    assert record["dtype"] == "uint16"
    assert record["channel_names"] == ["Empty", "DNA", "Marker A"]
    assert record["display_settings_applied"] is False
    assert record["intensity_conversion"]["method"] == "per_channel_zero_to_volume_max"
    assert [channel["channel_index"] for channel in record["channels"]] == order
    assert [channel["export_channel_position"] for channel in record["channels"]] == [0, 1, 2]
    assert record["channels"][2]["global_normalization"]["per_plane_normalization"] is False
    np.testing.assert_array_equal(window.project_state.original_dataset.data_czyx, original)
    assert source.exists()
    window.close()


def test_float32_display_isolation_imagej_roundtrip_and_outdated_rejection(tmp_path):
    qapp = QApplication.instance() or QApplication([])
    window, source, _ = _window(qapp, tmp_path)
    prepared = {
        index: np.array(array, copy=True)
        for index, array in window.image_preparation.prepared_channels.items()
    }
    for index, layer in window.image_preparation.preview_layers.items():
        layer.contrast_limits = (0.2, 0.8)
        layer.gamma = 1.7
        layer.opacity = 0.35
    float_path = tmp_path / "prepared_float.ome.tif"
    assert window.image_export.export(float_path, [1, 0], "ome", "float32")
    _wait(qapp, lambda: not window.image_export.busy)
    with tifffile.TiffFile(float_path) as tif:
        assert tif.series[0].axes == "CZYX"
        np.testing.assert_array_equal(tif.series[0].asarray(), np.stack([prepared[1], prepared[0]]).astype(np.float32))

    imagej_path = tmp_path / "prepared_imagej.tif"
    assert window.image_export.export(imagej_path, [0, 1], "imagej", "uint16")
    _wait(qapp, lambda: not window.image_export.busy)
    with tifffile.TiffFile(imagej_path) as tif:
        assert tif.series[0].axes == "ZCYX"
        saved = tif.series[0].asarray()
        expected = np.stack([_uint16_expected(prepared[0]), _uint16_expected(prepared[1])], axis=1)
        np.testing.assert_array_equal(saved, expected)
        assert float(tif.imagej_metadata["spacing"]) == 1.5
        assert tif.imagej_metadata["unit"] == "um"
    source_bytes = source.read_bytes()
    assert window.image_export.export(source, [0], "ome", "uint16")
    _wait(qapp, lambda: not window.image_export.busy)
    assert "failed" in window.context_panel.export_page.status.text().lower()
    assert source.read_bytes() == source_bytes
    window.project_state.set_calibration((2.0, 0.4, 0.2))
    assert not window.image_preparation.prepared_channels
    rejected = tmp_path / "outdated.ome.tif"
    assert not window.image_export.export(rejected, [0], "ome", "uint16")
    assert not rejected.exists()
    window.close()


def test_export_panel_channel_selection_and_order_controls(tmp_path):
    qapp = QApplication.instance() or QApplication([])
    window, _, _ = _window(qapp, tmp_path)
    panel = window.context_panel.export_page
    assert panel.selection_counter.text() == "3 of 3 channels selected"
    assert panel.export_button.isEnabled()
    initial_states = [panel.channels.item(row).checkState() for row in range(3)]
    panel.channels.setCurrentRow(2)
    assert [panel.channels.item(row).checkState() for row in range(3)] == initial_states
    panel._set_all_checked(False)
    assert panel.selected_channel_order() == []
    assert panel.selection_counter.text() == "0 of 3 channels selected"
    assert not panel.export_button.isEnabled()
    assert panel.channels.item(1).icon().isNull()
    assert panel.channels.item(1).toolTip() == "Excluded from TIFF export"
    panel.channels.item(1).setCheckState(Qt.CheckState.Checked)
    assert panel.selected_channel_order() == [1]
    assert panel.selection_counter.text() == "1 of 3 channels selected"
    assert panel.export_button.isEnabled()
    assert panel.channels.item(1).toolTip() == "Included in TIFF export"
    panel._set_all_checked(True)
    assert panel.selection_counter.text() == "3 of 3 channels selected"
    panel.channels.setCurrentRow(0)
    panel._move_current(1)
    assert panel.selected_channel_order() == [1, 0, 2]
    assert panel.selection_counter.text() == "3 of 3 channels selected"
    panel.set_busy(True)
    assert not panel.export_button.isEnabled()
    panel.set_busy(False)
    assert panel.export_button.isEnabled()
    window.close()


def test_export_panel_single_checkbox_mouse_keyboard_and_live_counter():
    qapp = QApplication.instance() or QApplication([])
    panel = ImageExportPanel()
    channels = [SimpleNamespace(index=index, name=f"Channel {index + 1}") for index in range(4)]
    provenance = {
        index: {"channel_name": channel.name, "route_label": "Original working image"}
        for index, channel in enumerate(channels)
    }
    panel.set_prepared_channels(channels, provenance)
    panel.resize(520, 500)
    panel.show()
    qapp.processEvents()
    assert panel.selection_counter.text() == "4 of 4 channels selected"
    for row in range(4):
        item = panel.channels.item(row)
        assert item.checkState() == Qt.CheckState.Checked
        assert item.icon().isNull()
        rect = panel.channels.visualItemRect(item)
        indicator = panel.channels.itemDelegate()._indicator_rect(rect)
        QTest.mouseClick(
            panel.channels.viewport(), Qt.MouseButton.LeftButton, pos=indicator.center()
        )
        assert item.checkState() == Qt.CheckState.Unchecked
        QTest.mouseClick(
            panel.channels.viewport(), Qt.MouseButton.LeftButton, pos=indicator.center()
        )
        assert item.checkState() == Qt.CheckState.Checked
    assert panel.selection_counter.text() == "4 of 4 channels selected"

    first = panel.channels.item(0)
    first_state = first.checkState()
    first_rect = panel.channels.visualItemRect(first)
    QTest.mouseClick(
        panel.channels.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(first_rect.right() - 4, first_rect.center().y()),
    )
    assert first.checkState() == first_state
    panel.channels.setCurrentRow(0)
    panel.channels.setFocus()
    QTest.keyClick(panel.channels, Qt.Key.Key_Space)
    assert first.checkState() == Qt.CheckState.Unchecked
    QTest.keyClick(panel.channels, Qt.Key.Key_Space)
    assert first.checkState() == Qt.CheckState.Checked

    states = {
        int(panel.channels.item(row).data(Qt.ItemDataRole.UserRole)):
        panel.channels.item(row).checkState()
        for row in range(4)
    }
    assert panel.channels.model().moveRow(QModelIndex(), 0, QModelIndex(), 4)
    assert {
        int(panel.channels.item(row).data(Qt.ItemDataRole.UserRole)):
        panel.channels.item(row).checkState()
        for row in range(4)
    } == states
    panel.channels.setCurrentRow(0)
    panel._move_current(1)
    assert {
        int(panel.channels.item(row).data(Qt.ItemDataRole.UserRole)):
        panel.channels.item(row).checkState()
        for row in range(4)
    } == states
    panel._set_all_checked(False)
    assert panel.selection_counter.text() == "0 of 4 channels selected"
    assert not panel.export_button.isEnabled()
    panel._set_all_checked(True)
    assert panel.selection_counter.text() == "4 of 4 channels selected"
    panel.set_busy(True)
    assert not panel.export_button.isEnabled()
    panel.set_busy(False)
    assert panel.export_button.isEnabled()
    panel.hide()
    panel.deleteLater()
    qapp.processEvents()
