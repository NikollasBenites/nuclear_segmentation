import os
from dataclasses import dataclass
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication, QDialog, QWidget

import nuclear_segmentation.desktop_window as desktop_module
from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.lif_import import enumerate_lif_series, load_lif_dataset
from nuclear_segmentation.series_browser import ImageSeriesBrowser


@dataclass
class FakePhysicalSizes:
    Z: float | None
    Y: float | None
    X: float | None


class FakeDims:
    def __init__(self, **sizes):
        self.order = "TCZYX" + ("M" if sizes.get("M", 1) > 1 else "")
        for name, value in sizes.items():
            setattr(self, name, value)


class FakeLazy:
    def __init__(self, data):
        self.data = data

    def compute(self):
        return self.data


class FakeMetadata:
    def __init__(self, objective=None, imaging_datetime=None):
        self.objective = objective
        self.imaging_datetime = imaging_datetime


class FakeObjective:
    nominal_magnification = 63.0


class FakeBioImage:
    instances = []

    def __init__(self):
        self.scenes = ("Brainstem tile", "Ipsilateral MNTB", "Unsupported mosaic")
        self.closed = False
        self.set_scene(0)
        self.instances.append(self)

    def set_scene(self, index):
        self.index = int(index)
        if self.index == 0:
            self.dims = FakeDims(T=1, C=2, Z=4, Y=5, X=6, M=1, S=1)
            self.shape = (1, 2, 4, 5, 6)
            self.physical_pixel_sizes = FakePhysicalSizes(2.0, 0.4, 0.3)
            self.channel_names = ("Green", "Red")
            self.standard_metadata = FakeMetadata(FakeObjective(), "2024-01-02")
        elif self.index == 1:
            self.dims = FakeDims(T=2, C=2, Z=3, Y=5, X=7, M=1, S=1)
            self.shape = (2, 2, 3, 5, 7)
            self.physical_pixel_sizes = FakePhysicalSizes(1.5, 0.25, 0.25)
            self.channel_names = ("DAPI", "MAP2")
            self.standard_metadata = FakeMetadata(None, None)
        else:
            self.dims = FakeDims(T=1, C=1, Z=2, Y=4, X=4, M=4, S=1)
            self.shape = (1, 1, 2, 4, 4, 4)
            self.physical_pixel_sizes = FakePhysicalSizes(1.0, 1.0, 1.0)
            self.channel_names = ("Gray",)
            self.standard_metadata = FakeMetadata()

    def get_image_dask_data(self, order, T):
        assert order == "CZYX"
        if self.index != 1:
            raise AssertionError("The test must load only the selected series")
        data = np.arange(2 * 3 * 5 * 7, dtype=np.uint16).reshape(2, 3, 5, 7)
        return FakeLazy(data + int(T) * 1000)

    def close(self):
        self.closed = True


class FakeQtViewer(QWidget):
    def __init__(self, viewer, show_welcome_screen=False):
        super().__init__()
        self.viewer = viewer


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lif_path(tmp_path):
    path = tmp_path / "experiment.lif"
    path.write_bytes(b"mock only")
    return path


def fake_factory(path):
    assert str(path).endswith("experiment.lif")
    return FakeBioImage()


def test_lif_enumeration_does_not_load_pixels_and_preserves_series_metadata(lif_path):
    FakeBioImage.instances.clear()
    series = enumerate_lif_series(lif_path, bioimage_factory=fake_factory)
    assert [item.name for item in series] == [
        "Brainstem tile",
        "Ipsilateral MNTB",
        "Unsupported mosaic",
    ]
    assert series[0].channel_count == 2
    assert series[0].z_planes == 4
    assert series[0].objective_magnification == 63.0
    assert series[0].voxel_spacing_zyx_um == (2.0, 0.4, 0.3)
    assert series[1].time_points == 2
    assert not series[2].supported
    assert "mosaic" in series[2].limitation.lower()
    assert FakeBioImage.instances[-1].closed


def test_lif_selected_series_loading_order_time_calibration_and_cleanup(lif_path):
    FakeBioImage.instances.clear()
    dataset = load_lif_dataset(
        lif_path, 1, time_index=1, bioimage_factory=fake_factory
    )
    assert dataset.source_format == "Leica LIF"
    assert dataset.series_index == 1
    assert dataset.series_name == "Ipsilateral MNTB"
    assert dataset.standardized_shape == (2, 3, 5, 7)
    assert dataset.dtype == "uint16"
    assert dataset.voxel_spacing_zyx_um == (1.5, 0.25, 0.25)
    assert [channel.name for channel in dataset.channels] == ["DAPI", "MAP2"]
    assert dataset.data_czyx[0, 0, 0, 0] == 1000
    assert FakeBioImage.instances[-1].closed


def test_lif_rejects_unsupported_series_and_invalid_selection(lif_path):
    with pytest.raises(ValueError, match="mosaic"):
        load_lif_dataset(lif_path, 2, bioimage_factory=fake_factory)
    assert FakeBioImage.instances[-1].closed
    with pytest.raises(ValueError, match="unavailable"):
        load_lif_dataset(lif_path, 7, bioimage_factory=fake_factory)
    assert FakeBioImage.instances[-1].closed


def test_image_series_browser_displays_missing_values_and_blocks_mosaic(
    qapp, lif_path
):
    series = enumerate_lif_series(lif_path, bioimage_factory=fake_factory)
    browser = ImageSeriesBrowser(lif_path, series)
    assert browser.windowTitle() == "Image Series Browser"
    assert browser.table.item(1, 6).text() == "Not available"
    browser.table.selectRow(2)
    qapp.processEvents()
    assert not browser.load_button.isEnabled()
    assert "mosaic" in browser.note.text().lower()
    browser.table.selectRow(1)
    qapp.processEvents()
    assert browser.load_button.isEnabled()
    assert browser.selected_series.index == 1
    browser.close()


def test_desktop_lif_selection_loads_only_chosen_series(
    qapp, lif_path, tmp_path, monkeypatch
):
    series = enumerate_lif_series(lif_path, bioimage_factory=fake_factory)

    class AcceptedBrowser:
        DialogCode = QDialog.DialogCode

        def __init__(self, source, available, parent=None):
            assert source == lif_path
            self.selected_series = available[1]

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(desktop_module, "ImageSeriesBrowser", AcceptedBrowser)
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
        bioimage_factory=fake_factory,
    )
    window._show_series_browser(lif_path, series)
    deadline = monotonic() + 5
    while window._import_thread is not None and monotonic() < deadline:
        qapp.processEvents()
        sleep(0.01)
    assert window.project_state.dataset_ready
    assert window.project_state.dataset.series_index == 1
    assert [layer.name for layer in window.embedding.viewer.layers] == ["DAPI", "MAP2"]
    window.close()
