import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
import numpy as np
import pytest
from qtpy.QtWidgets import QApplication, QMainWindow, QMenu
from nuclear_segmentation.roi_smoothing import smooth_roi
from nuclear_segmentation.roi_smoothing_ui import RoiSmoothingMenu


def test_xy_keeps_empty_z_planes_and_label_id():
    data = np.zeros((5, 30, 30), dtype=np.uint16)
    data[2, 5:25, 5:25] = 7
    data[2, 4, 10] = 7
    result = smooth_roi(data)
    assert result.dtype == data.dtype and set(np.unique(result)) == {0, 7}
    assert not result[[0, 1, 3, 4]].any()
    assert result[2, 4, 10] == 0
    assert np.array_equal(smooth_roi(data, 0, 0), data)
    data[2, 10, 10] = 2
    with pytest.raises(ValueError): smooth_roi(data)


class Layer:
    def __init__(self, data, **kwargs):
        self.data = data
        self.visible = True
        self.scale = (1, 1, 1); self.translate = (0, 0, 0)
        self.rotate = np.eye(3); self.shear = (0, 0, 0); self.affine = np.eye(4)
        self.opacity = .7
        self.__dict__.update(kwargs)


class Layers(list):
    def __init__(self, source):
        super().__init__([source]); self.selection = SimpleNamespace(active=source)


def test_preview_cancel_apply_and_undo():
    app = QApplication.instance() or QApplication([])
    data = np.zeros((3, 30, 30), dtype=np.uint8); data[:, 5:25, 5:25] = 1
    source = Layer(data.copy(), name='MNTB ROI - review before density')
    layers = Layers(source)
    viewer = SimpleNamespace(layers=layers, window=SimpleNamespace(_qt_window=QMainWindow()))
    def add_labels(data, **kw):
        layer = Layer(data, **kw); layers.append(layer); return layer
    viewer.add_labels = add_labels
    menu = QMenu(); controller = RoiSmoothingMenu(viewer, menu)
    controller.open(); dialog = controller.dialog
    assert len(layers) == 2 and not source.visible
    assert np.array_equal(source.data, data)
    dialog.reject()
    assert len(layers) == 1 and source.visible
    controller.open(); dialog = controller.dialog
    expected = dialog.result.copy(); dialog.apply()
    assert len(layers) == 1 and np.array_equal(source.data, expected)
    controller.undo()
    assert np.array_equal(source.data, data) and controller.backup is None
    viewer.window._qt_window.close()
