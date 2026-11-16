import os
from dataclasses import dataclass
import logging
from threading import Event
from time import monotonic, sleep
from xml.etree import ElementTree as ET

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


class FakeBioImage:
    instances = []

    def __init__(self):
        self.scenes = ("Brainstem tile", "Ipsilateral MNTB", "Unsupported mosaic")
        self.closed = False
        self.set_scene(0)
        self.instances.append(self)

    @property
    def standard_metadata(self):
        raise AssertionError("optional BioIO standard metadata must not be requested")

    @property
    def metadata(self):
        root = ET.Element("Element", Name="Experiment")
        children = ET.SubElement(root, "Children")
        first = ET.SubElement(children, "Element", Name="Brainstem tile")
        ET.SubElement(
            first,
            "Objective",
            Magnification="63.0",
            AcquisitionDate="2024-01-02",
        )
        first_channels = ET.SubElement(first, "Channels")
        ET.SubElement(first_channels, "ChannelDescription", Name="Green")
        ET.SubElement(first_channels, "ChannelDescription", Name="Red")
        second = ET.SubElement(children, "Element", Name="Ipsilateral MNTB")
        second_channels = ET.SubElement(second, "Channels")
        ET.SubElement(second_channels, "ChannelDescription", Name="DAPI")
        ET.SubElement(second_channels, "ChannelDescription", Name="MAP2")
        ET.SubElement(children, "Element", Name="Unsupported mosaic")
        return root

    def set_scene(self, index):
        self.index = int(index)
        if self.index == 0:
            self.dims = FakeDims(T=1, C=2, Z=4, Y=5, X=6, M=1, S=1)
            self.shape = (1, 2, 4, 5, 6)
            self.physical_pixel_sizes = FakePhysicalSizes(2.0, 0.4, 0.3)
            self.channel_names = ("Green", "Red")
        elif self.index == 1:
            self.dims = FakeDims(T=2, C=2, Z=3, Y=5, X=7, M=1, S=1)
            self.shape = (2, 2, 3, 5, 7)
            self.physical_pixel_sizes = FakePhysicalSizes(1.5, 0.25, 0.25)
            self.channel_names = ("DAPI", "MAP2")
        else:
            self.dims = FakeDims(T=1, C=1, Z=2, Y=4, X=4, M=4, S=1)
            self.shape = (1, 1, 2, 4, 4, 4)
            self.physical_pixel_sizes = FakePhysicalSizes(1.0, 1.0, 1.0)
            self.channel_names = ("Gray",)

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
    assert series[0].acquisition_date == "2024-01-02"
    assert series[1].objective_magnification is None
    assert series[1].acquisition_date is None
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
    assert "objective_magnification" not in dataset.acquisition_metadata
    assert dataset.acquisition_metadata["leica_channel_metadata"][0]["Name"] == "DAPI"
    assert FakeBioImage.instances[-1].closed


def test_lif_diagnostics_are_ordered_and_selected_scene_metadata_is_isolated(lif_path):
    diagnostics = []
    dataset = load_lif_dataset(
        lif_path,
        1,
        bioimage_factory=fake_factory,
        diagnostic=lambda stage, message, elapsed: diagnostics.append(stage),
    )
    assert diagnostics == [
        "file_opened",
        "series_selected",
        "dimensions_validated",
        "calibration_validated",
        "pixel_loading_started",
        "pixel_loading_completed",
        "dataset_validated",
    ]
    assert "objective_magnification" not in dataset.acquisition_metadata


def test_lif_worker_failure_reports_stage_and_logs_traceback(qapp, lif_path, caplog):
    class BrokenLazy:
        def compute(self):
            raise RuntimeError("synthetic pixel failure")

    class BrokenBioImage(FakeBioImage):
        def get_image_dask_data(self, order, T):
            return BrokenLazy()

    from nuclear_segmentation.lif_import import LifImportWorker

    worker = LifImportWorker(lif_path, 1, {}, lambda path: BrokenBioImage())
    failures = []
    worker.failed.connect(failures.append)
    with caplog.at_level(logging.ERROR, logger="nuclear_segmentation.lif_import"):
        worker.run()
    assert len(failures) == 1
    assert "experiment.lif" in failures[0]
    assert "pixel_loading_started" in failures[0]
    assert "RuntimeError" in failures[0]
    assert "synthetic pixel failure" in caplog.text
    assert "Traceback" in caplog.text


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


def test_lif_loading_dialog_stays_responsive_and_prevents_duplicate_loads(
    qapp, lif_path, tmp_path, monkeypatch
):
    started = Event()
    release = Event()

    class SlowLazy:
        def compute(self):
            started.set()
            assert release.wait(5)
            return np.zeros((2, 3, 5, 7), dtype=np.uint16)

    class SlowBioImage(FakeBioImage):
        def get_image_dask_data(self, order, T):
            assert order == "CZYX"
            return SlowLazy()

    series = enumerate_lif_series(lif_path, bioimage_factory=fake_factory)

    class AcceptedBrowser:
        DialogCode = QDialog.DialogCode

        def __init__(self, source, available, parent=None):
            self.selected_series = available[1]

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(desktop_module, "ImageSeriesBrowser", AcceptedBrowser)
    settings = QSettings(str(tmp_path / "loading.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
        bioimage_factory=lambda path: SlowBioImage(),
    )
    window._show_series_browser(lif_path, series)
    deadline = monotonic() + 5
    while not started.is_set() and monotonic() < deadline:
        qapp.processEvents()
        sleep(0.01)
    assert started.is_set()
    assert window._loading_dialog is not None
    assert window._loading_dialog.isVisible()
    assert window._loading_dialog.progress.minimum() == 0
    assert window._loading_dialog.progress.maximum() == 0
    assert "experiment.lif" in window._loading_dialog.file_label.text()
    assert "Ipsilateral MNTB" in window._loading_dialog.series_label.text()
    assert not window.load_pending_image()
    release.set()
    deadline = monotonic() + 5
    while window._import_thread is not None and monotonic() < deadline:
        qapp.processEvents()
        sleep(0.01)
    assert window._import_thread is None
    assert window._loading_dialog is None
    assert window.project_state.dataset_ready
    assert any(text.startswith("pixel_loading_started:") for text in window.import_diagnostics)
    assert any(text.startswith("import_completed:") for text in window.import_diagnostics)
    window.close()


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
    previous_dataset = window.project_state.dataset
    previous_layers = list(window.embedding.viewer.layers)
    original_add_image = window.embedding.viewer.add_image
    calls = 0

    def fail_second_layer(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic layer creation failure")
        return original_add_image(*args, **kwargs)

    monkeypatch.setattr(type(window.embedding.viewer), "add_image", fail_second_layer)
    window._apply_imported_dataset(previous_dataset)
    assert window.project_state.dataset is previous_dataset
    assert list(window.embedding.viewer.layers) == previous_layers
    assert "synthetic layer creation failure" in window.project_state.import_status
    window.close()
