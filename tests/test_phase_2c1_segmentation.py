import builtins
import os
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile
from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import QApplication, QWidget

from nuclear_segmentation.config import defaults, validate_config
from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.z_correction import DEFAULTS as Z_DEFAULTS


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
    raise AssertionError("Timed out waiting for TIFF import")


def _open(qapp, tmp_path, name="segmentation.ome.tif"):
    path = tmp_path / name
    data = np.arange(2 * 4 * 6 * 8, dtype=np.uint16).reshape(2, 4, 6, 8)
    tifffile.imwrite(
        path,
        data,
        ome=True,
        metadata={
            "axes": "CZYX",
            "Channel": {"Name": ["DNA", "Marker"]},
            "PhysicalSizeZ": 2.0,
            "PhysicalSizeY": 0.5,
            "PhysicalSizeX": 0.25,
        },
    )
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat),
        restore_workspace=False,
    )
    assert window.prepare_image_import(path, confirm_replacement=False)
    _wait(qapp, lambda: window._import_thread is None)
    return window, data


def test_defaults_validation_editing_modes_and_disabled_execution(qapp, tmp_path):
    window, _ = _open(qapp, tmp_path)
    panel = window.context_panel.segmentation_page
    scroll = window.context_panel.stack.widget(
        window.context_panel._indexes["segmentation"]
    )
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    expected = defaults()
    config = panel.scientific_config()
    assert config["RUN_MODE"] == "segment"
    for key in (
        "MODEL_NAME", "CELLPROB_THRESHOLD", "CELLPOSE_MIN_SIZE_VOXELS",
        "BATCH_SIZE", "FLOW3D_SMOOTH", "CELLPOSE_DO_3D",
        "CELLPOSE_STITCH_THRESHOLD", "CELLPOSE_RESAMPLE",
        "CELLPOSE_RESCALE", "DIAMETER_PIXELS",
    ):
        assert config[key] == expected[key]
    assert config["PROCESSING_DEVICE"] == "auto"
    assert panel.execute_button.isEnabled()
    assert "ready" in panel.readiness.text().lower()

    panel.model_name.setText("/models/local-cpsam")
    panel.device.setCurrentIndex(panel.device.findData("cpu"))
    panel.cellprob.setValue(-2.5)
    panel.min_size.setValue(55)
    panel.batch_size.setValue(7)
    panel.resample.setChecked(False)
    panel.rescale_enabled.setChecked(True)
    panel.rescale.setValue(0.75)
    panel.diameter_enabled.setChecked(True)
    panel.diameter.setValue(18.0)
    config = panel.scientific_config()
    assert config["MODEL_NAME"] == "/models/local-cpsam"
    assert config["PROCESSING_DEVICE"] == "cpu"
    assert config["CELLPOSE_RESAMPLE"] is False
    assert config["CELLPOSE_RESCALE"] == 0.75
    assert config["DIAMETER_PIXELS"] == 18.0
    assert validate_config(config) == config

    panel.mode.setCurrentIndex(panel.mode.findData(False))
    assert not panel.flow_smooth[0].isEnabled()
    assert panel.stitch.isEnabled()
    with pytest.raises(ValueError, match="2D segmentation"):
        panel.scientific_config()
    assert "2D segmentation" in panel.readiness.text()
    panel.stitch.setValue(0.25)
    assert panel.scientific_config()["CELLPOSE_DO_3D"] is False
    panel.model_name.clear()
    with pytest.raises(ValueError, match="model name"):
        panel.scientific_config()
    assert not panel.execute_button.isEnabled()
    window.close()


def test_input_channel_crop_preprocessing_and_array_immutability(qapp, tmp_path, monkeypatch):
    real_import = builtins.__import__
    def reject_cellpose(name, *args, **kwargs):
        if name == "cellpose" or name.startswith("cellpose."):
            raise AssertionError("Opening/configuring the panel imported Cellpose")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", reject_cellpose)
    window, source = _open(qapp, tmp_path)
    panel = window.context_panel.segmentation_page
    original = np.array(window.project_state.original_dataset.data_czyx, copy=True)
    assert panel.input_values["channel"].text() == "Index 0: DNA"
    assert panel.input_values["shape"].text() == "4 × 6 × 8"
    assert panel.input_values["spacing"].text() == "2 × 0.5 × 0.25 µm"
    assert panel.input_values["anisotropy"].text().startswith("5.33333")
    for value in panel.input_values.values():
        assert value.wordWrap()
        assert value.minimumWidth() == 0
        assert value.toolTip() == value.text()
    window.project_state.set_calibration((3.0, 0.6, 0.3))
    assert panel.input_values["spacing"].text() == "3 × 0.6 × 0.3 µm"
    assert panel.scientific_config()["VOXEL_SPACING_OVERRIDE_UM"] == [3.0, 0.6, 0.3]

    window.project_state.update_channel(0, name="Nuclear stain")
    assert panel.input_values["channel"].text() == "Index 0: Nuclear stain"
    window.project_state.update_channel(1, role="segmentation")
    assert panel.input_values["channel"].text() == "Index 1: Marker"
    assert panel.scientific_config()["SEGMENTATION_CHANNEL"] == "Marker"

    roi = np.zeros((4, 5), dtype=bool)
    roi[:, :3] = True
    window.project_state.apply_crop(
        (1, 5, 2, 7),
        ((1, 2), (1, 5), (4, 5), (4, 2)),
        roi,
        "polygon",
    )
    panel.refresh()
    assert panel.input_values["shape"].text() == "4 × 4 × 5"
    assert "polygon ROI retained" in panel.input_values["crop"].text()
    window.project_state.reset_crop()
    panel.refresh()
    assert panel.input_values["crop"].text() == "Not cropped"

    window.project_state.apply_normalization(
        {
            "mode": "percentile",
            "cellpose_normalize": {
                "normalize": True, "percentile": [1.0, 99.0], "norm3D": True,
            },
        }
    )
    assert "Percentile" in panel.input_values["normalization"].text()
    assert "applied" in panel.input_values["normalization"].text()
    index = window.project_state.segmentation_channel_index
    report = {"options": dict(Z_DEFAULTS)}
    window.project_state.set_z_correction_preview(index, Z_DEFAULTS, report)
    assert "Preview calculated" in panel.input_values["z_correction"].text()
    window.project_state.apply_z_corrections([index])
    assert "Applied" in panel.input_values["z_correction"].text()
    window.project_state.reset_z_corrections([index])
    assert panel.input_values["z_correction"].text() == "Not applied"
    window.project_state.reset_normalization()
    assert panel.input_values["normalization"].text() == "Cellpose Default"

    panel.refresh()
    panel.scientific_config()
    np.testing.assert_array_equal(window.project_state.original_dataset.data_czyx, original)
    np.testing.assert_array_equal(source, original)
    window.close()


def test_project_close_and_replacement_clear_stale_input(qapp, tmp_path):
    window, _ = _open(qapp, tmp_path, "first.ome.tif")
    panel = window.context_panel.segmentation_page
    assert panel.input_values["source"].text() == "first.ome.tif"
    window.segmentation._thread = object()
    assert not window.close_project(confirm=False)
    assert not window.prepare_image_import(
        tmp_path / "blocked.ome.tif", confirm_replacement=False
    )
    window.segmentation._thread = None
    assert window.close_project(confirm=False)
    assert panel.input_values["source"].text() == "—"
    assert "load a dataset" in panel.readiness.text()
    assert not panel.execute_button.isEnabled()

    second = tmp_path / "second.ome.tif"
    data = np.zeros((2, 2, 3, 4), dtype=np.uint16)
    tifffile.imwrite(
        second, data, ome=True, photometric="minisblack",
        metadata={"axes": "CZYX", "Channel": {"Name": ["Replacement", "Other"]},
                  "PhysicalSizeZ": 1.0, "PhysicalSizeY": 1.0, "PhysicalSizeX": 1.0},
    )
    assert window.prepare_image_import(second, confirm_replacement=False)
    _wait(qapp, lambda: window._import_thread is None)
    assert panel.input_values["source"].text() == "second.ome.tif"
    assert panel.input_values["channel"].text() == "Index 0: Replacement"
    assert panel.input_values["shape"].text() == "2 × 3 × 4"
    window.close()
