import os
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication, QWidget

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.image_import import inspect_tiff, load_tiff_dataset


class FakeQtViewer(QWidget):
    def __init__(self, viewer, show_welcome_screen=False):
        super().__init__()
        self.viewer = viewer


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait_until(qapp, predicate, timeout=5):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        sleep(0.01)
    raise AssertionError("Timed out waiting for the image import worker")


def test_single_channel_tiff_requires_manual_spacing_and_preserves_values(tmp_path):
    source = tmp_path / "single.tif"
    original = (np.arange(7 * 9, dtype=np.uint16) * 3).reshape(7, 9)
    tifffile.imwrite(source, original, metadata={"axes": "YX"})

    inspection = inspect_tiff(source)
    assert inspection.source_format == "TIFF"
    assert inspection.reported_axes == "YX"
    assert inspection.voxel_spacing_zyx_um is None

    with pytest.raises(ValueError, match="Complete physical"):
        load_tiff_dataset(source)
    dataset = load_tiff_dataset(source, spacing_override=(2.5, 0.4, 0.5))
    assert dataset.standardized_shape == (1, 1, 7, 9)
    assert dataset.data_czyx.dtype == original.dtype
    np.testing.assert_array_equal(dataset.data_czyx[0, 0], original)
    assert dataset.voxel_spacing_zyx_um == (2.5, 0.4, 0.5)
    assert dataset.calibration_source == "manual override"


def test_ome_tiff_multichannel_axes_names_and_calibration(tmp_path):
    source = tmp_path / "multichannel.ome.tif"
    original = np.arange(2 * 4 * 6 * 8, dtype=np.uint16).reshape(2, 4, 6, 8)
    tifffile.imwrite(
        source,
        original,
        ome=True,
        metadata={
            "axes": "CZYX",
            "Channel": {"Name": ["Nuclei", "Marker"]},
            "PhysicalSizeZ": 1.7,
            "PhysicalSizeZUnit": "µm",
            "PhysicalSizeY": 0.31,
            "PhysicalSizeYUnit": "µm",
            "PhysicalSizeX": 0.29,
            "PhysicalSizeXUnit": "µm",
        },
    )
    dataset = load_tiff_dataset(source)
    assert dataset.source_format == "OME-TIFF"
    assert dataset.reported_axes == "CZYX"
    assert dataset.standardized_shape == original.shape
    assert [channel.name for channel in dataset.channels] == ["Nuclei", "Marker"]
    assert [channel.index for channel in dataset.channels] == [0, 1]
    assert dataset.voxel_spacing_zyx_um == pytest.approx((1.7, 0.31, 0.29))
    assert dataset.calibration_source == "OME-XML"
    np.testing.assert_array_equal(dataset.data_czyx, original)


def test_time_index_and_axes_validation_use_existing_standardization(tmp_path):
    source = tmp_path / "time.ome.tif"
    original = np.arange(2 * 3 * 5 * 7, dtype=np.uint8).reshape(2, 3, 5, 7)
    tifffile.imwrite(
        source,
        original,
        ome=True,
        metadata={
            "axes": "TZYX",
            "PhysicalSizeZ": 2.0,
            "PhysicalSizeY": 0.5,
            "PhysicalSizeX": 0.5,
        },
    )
    dataset = load_tiff_dataset(source, time_index=1)
    np.testing.assert_array_equal(dataset.data_czyx[0], original[1])
    assert dataset.time_index == 1
    with pytest.raises(ValueError, match="outside"):
        load_tiff_dataset(source, time_index=3)
    with pytest.raises(ValueError, match="characters"):
        load_tiff_dataset(source, axes_override="ZYX")


def test_desktop_import_worker_reuses_viewer_and_creates_calibrated_layers(
    qapp, tmp_path
):
    source = tmp_path / "desktop.ome.tif"
    original = np.arange(2 * 3 * 6 * 7, dtype=np.uint16).reshape(2, 3, 6, 7)
    tifffile.imwrite(
        source,
        original,
        ome=True,
        metadata={
            "axes": "CZYX",
            "Channel": {"Name": ["DNA", "Protein"]},
            "PhysicalSizeZ": 2.2,
            "PhysicalSizeY": 0.4,
            "PhysicalSizeX": 0.3,
        },
    )
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        load_demo=True,
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    window.show()
    original_viewer = window.embedding.viewer
    assert window.open_image_action.isEnabled()
    assert window.prepare_image_import(source, confirm_replacement=False)
    assert not window.open_image_action.isEnabled()
    _wait_until(qapp, lambda: window._import_thread is None)

    assert window.embedding.viewer is original_viewer
    assert window.project_state.dataset_ready
    assert [layer.name for layer in window.embedding.viewer.layers] == ["DNA", "Protein"]
    for index, layer in enumerate(window.embedding.viewer.layers):
        assert tuple(layer.scale) == pytest.approx((2.2, 0.4, 0.3))
        assert layer.metadata["channel_index"] == index
        np.testing.assert_array_equal(layer.data, original[index])
    assert window.context_panel.project_page.provenance_values["format"].text() == "OME-TIFF"
    window.set_workflow("segmentation")
    assert window.embedding.viewer is original_viewer
    assert len(window.embedding.viewer.layers) == 2
    window.close()


def test_channel_configuration_sync_validation_and_pixel_immutability(qapp, tmp_path):
    source = tmp_path / "channels.ome.tif"
    original = np.arange(2 * 3 * 5 * 7, dtype=np.uint16).reshape(2, 3, 5, 7)
    tifffile.imwrite(
        source,
        original,
        ome=True,
        metadata={
            "axes": "CZYX",
            "Channel": {"Name": ["DNA", "Marker"]},
            "PhysicalSizeZ": 2.0,
            "PhysicalSizeY": 0.4,
            "PhysicalSizeX": 0.4,
        },
    )
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait_until(qapp, lambda: window._import_thread is None)
    first, second = list(window.embedding.viewer.layers)
    first_data = first.data
    second_data = second.data

    window._update_channel_configuration(1, "name", "VGLUT2")
    assert second.name == "VGLUT2"
    assert window.project_state.channels[1].name == "VGLUT2"
    assert window.layer_panel.model.item_for_layer(second).text() == "VGLUT2"

    first.name = "Nuclear-reference"
    qapp.processEvents()
    assert window.project_state.channels[0].name == "Nuclear-reference"
    second.name = "Nuclear reference"
    qapp.processEvents()
    assert second.name == "VGLUT2"

    window._update_channel_configuration(1, "visible", True)
    window._update_channel_configuration(1, "colormap", "green")
    window._update_channel_configuration(1, "role", "segmentation")
    assert second.visible
    assert second.colormap.name == "green"
    assert window.project_state.segmentation_channel == "VGLUT2"
    assert [channel.role for channel in window.project_state.channels] == [
        "measurement",
        "segmentation",
    ]
    window._update_channel_configuration(1, "role", "reference")
    assert window.project_state.segmentation_channel == "VGLUT2"
    assert window.project_state.channels[1].role == "segmentation"
    records = window.project_state.analysis_channel_records()
    assert records[1]["index"] == 1
    assert records[1]["role"] == "segmentation"

    window.context_panel.project_page.spacing_fields[0].setText("4.0")
    window.context_panel.project_page.spacing_fields[1].setText("0.6")
    window.context_panel.project_page.spacing_fields[2].setText("0.5")
    window.context_panel.project_page.apply_calibration_button.click()
    assert window.project_state.voxel_spacing_zyx_um == (4.0, 0.6, 0.5)
    assert tuple(first.scale) == (4.0, 0.6, 0.5)
    assert tuple(second.scale) == (4.0, 0.6, 0.5)

    assert first.data is first_data
    assert second.data is second_data
    np.testing.assert_array_equal(first.data, original[0])
    np.testing.assert_array_equal(second.data, original[1])
    window.close()


def test_desktop_missing_calibration_waits_for_explicit_values(qapp, tmp_path):
    source = tmp_path / "manual.tif"
    tifffile.imwrite(source, np.ones((3, 5, 7), dtype=np.uint8), metadata={"axes": "ZYX"})
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    assert window.prepare_image_import(source, confirm_replacement=False)
    assert window._import_thread is None
    assert not window.project_state.dataset_ready
    assert "Calibration missing" in window.project_state.import_status
    for field, value in zip(
        window.context_panel.project_page.spacing_fields, (3.0, 0.8, 0.7)
    ):
        field.setText(str(value))
    assert window.load_pending_image()
    _wait_until(qapp, lambda: window._import_thread is None)
    assert window.project_state.dataset.voxel_spacing_zyx_um == (3.0, 0.8, 0.7)
    assert tuple(window.embedding.viewer.layers[0].scale) == (3.0, 0.8, 0.7)
    window.close()


def test_failed_replacement_keeps_active_dataset_and_reports_axes_error(qapp, tmp_path):
    valid = tmp_path / "valid.ome.tif"
    tifffile.imwrite(
        valid,
        np.ones((2, 4, 5), dtype=np.uint16),
        ome=True,
        metadata={
            "axes": "ZYX",
            "PhysicalSizeZ": 2.0,
            "PhysicalSizeY": 0.5,
            "PhysicalSizeX": 0.5,
        },
    )
    invalid = tmp_path / "ambiguous.tif"
    tifffile.imwrite(
        invalid,
        np.ones((2, 4, 5), dtype=np.uint16),
        metadata={"axes": "QYX"},
        photometric="minisblack",
    )
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    assert window.prepare_image_import(valid, confirm_replacement=False)
    _wait_until(qapp, lambda: window._import_thread is None)
    old_dataset = window.project_state.dataset
    old_layer = window.embedding.viewer.layers[0]

    assert window.prepare_image_import(invalid, confirm_replacement=False)
    for field, value in zip(
        window.context_panel.project_page.spacing_fields, (2.0, 0.5, 0.5)
    ):
        field.setText(str(value))
    assert window.load_pending_image()
    _wait_until(qapp, lambda: window._import_thread is None)
    assert window.project_state.dataset is old_dataset
    assert window.embedding.viewer.layers[0] is old_layer
    assert "Unsupported non-singleton axis" in window.project_state.import_status
    window.close()


def test_project_replacement_requires_confirmation(qapp, tmp_path, monkeypatch):
    source = tmp_path / "first.ome.tif"
    replacement = tmp_path / "second.ome.tif"
    metadata = {
        "axes": "ZYX",
        "PhysicalSizeZ": 1.0,
        "PhysicalSizeY": 0.5,
        "PhysicalSizeX": 0.5,
    }
    tifffile.imwrite(source, np.ones((2, 4, 5), dtype=np.uint8), ome=True, metadata=metadata)
    tifffile.imwrite(replacement, np.full((2, 4, 5), 2, dtype=np.uint8), ome=True, metadata=metadata)
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait_until(qapp, lambda: window._import_thread is None)
    monkeypatch.setattr(window, "_ask_project_changes", lambda: "cancel")
    assert not window.prepare_image_import(replacement)
    assert window.project_state.dataset.source_path == source.resolve()
    assert window.embedding.viewer.layers[0].data[0, 0, 0] == 1
    window.close()


def test_long_provenance_path_axes_and_dimensions_remain_accessible(qapp, tmp_path):
    nested = tmp_path / ("long_leica_experiment_folder_" * 3)
    nested.mkdir()
    source = nested / ("multidimensional_mntb_acquisition_" * 2 + ".ome.tif")
    tifffile.imwrite(
        source,
        np.ones((2, 3, 5, 7), dtype=np.uint8),
        ome=True,
        metadata={
            "axes": "CZYX",
            "PhysicalSizeZ": 2.0,
            "PhysicalSizeY": 0.5,
            "PhysicalSizeX": 0.5,
        },
    )
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    window.context_container.setMaximumWidth(340)
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait_until(qapp, lambda: window._import_thread is None)
    values = window.context_panel.project_page.provenance_values
    assert values["path"].text() == str(source.resolve())
    assert values["path"].toolTip() == str(source.resolve())
    assert values["path"].wordWrap()
    assert values["original_axes"].text() == "CZYX"
    assert values["original_dimensions"].text() == "(2, 3, 5, 7)"
    assert values["standardized"].text() == "(2, 3, 5, 7)"
    assert window.context_panel.project_page.sections["provenance"].expanded
    window.close()
