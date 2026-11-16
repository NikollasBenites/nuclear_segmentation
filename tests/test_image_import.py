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
