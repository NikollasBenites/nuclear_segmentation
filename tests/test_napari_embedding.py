import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
from qtpy.QtCore import QPoint, Qt
from qtpy.QtGui import QContextMenuEvent
from qtpy.QtWidgets import QApplication, QWidget

from nuclear_segmentation.napari_embedding import (
    DEMO_SPACING_ZYX_UM,
    DISPLAY_NAME,
    EmbeddedNapariWidget,
    make_demo_volumes,
)


class FakeQtViewer(QWidget):
    """Canvas-free QtViewer stand-in for non-OpenGL embedding tests."""

    def __init__(self, viewer, show_welcome_screen=False):
        super().__init__()
        self.viewer = viewer
        self.show_welcome_screen = show_welcome_screen
        self.close_count = 0

    def closeEvent(self, event):
        self.close_count += 1
        event.accept()


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def embedded(qapp):
    widget = EmbeddedNapariWidget(qt_viewer_factory=FakeQtViewer)
    yield widget
    widget.shutdown()
    widget.deleteLater()
    qapp.processEvents()


def test_demo_volumes_are_deterministic_and_independent():
    first = make_demo_volumes((8, 24, 32))
    second = make_demo_volumes((8, 24, 32))
    assert DISPLAY_NAME == "NuCytoSeg"
    assert first.nuclear.dtype == np.float32
    assert first.cytoplasmic.dtype == np.float32
    assert first.labels.dtype == np.uint16
    assert first.nuclear.shape == first.cytoplasmic.shape == first.labels.shape == (8, 24, 32)
    assert first.multiscale[1].shape == (8, 12, 16)
    np.testing.assert_array_equal(first.nuclear, second.nuclear)
    assert set(np.unique(first.labels)) == {0, 1, 2, 3}
    first.labels[0, 0, 0] = 99
    assert second.labels[0, 0, 0] == 0
    assert DEMO_SPACING_ZYX_UM[0] > DEMO_SPACING_ZYX_UM[1]


@pytest.mark.parametrize("shape", [(7, 24, 32), (8, 7, 32), (8, 24, 7), (8, 24)])
def test_demo_volumes_reject_invalid_shape(shape):
    with pytest.raises(ValueError):
        make_demo_volumes(shape)


def test_embedded_model_initialization_and_calibration(embedded):
    assert len(embedded.viewer.layers) == 4
    labels = embedded.demo_labels()
    assert embedded.viewer.layers.selection.active is labels
    assert tuple(labels.scale) == DEMO_SPACING_ZYX_UM
    assert all(str(unit) == 'micrometer' for unit in labels.units)
    assert embedded.viewer.layers['Multiscale demonstration'].multiscale
    assert embedded.viewer.dims.axis_labels == ('Z', 'Y', 'X')
    assert embedded.viewer.dims.current_step[0] == labels.data.shape[0] // 2
    assert embedded.qt_viewer.show_welcome_screen is False


def test_owned_layer_list_selection_visibility_and_right_click(embedded, qapp):
    layer_list = embedded.layer_list
    image = embedded.viewer.layers['Nuclear channel — synthetic']
    image_item = layer_list._item_for_layer(image)
    layer_list.setCurrentItem(image_item)
    assert embedded.viewer.layers.selection.active is image
    image_item.setCheckState(Qt.CheckState.Unchecked)
    assert not image.visible
    image_item.setCheckState(Qt.CheckState.Checked)
    assert image.visible

    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse,
        QPoint(4, 4),
        layer_list.mapToGlobal(QPoint(4, 4)),
    )
    QApplication.sendEvent(layer_list, event)
    qapp.processEvents()
    assert layer_list.contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu


def test_application_owned_label_modes_and_selected_label(embedded):
    labels = embedded.demo_labels()
    embedded.mode_buttons['pick'].click()
    assert str(labels.mode) == 'pick'
    embedded.mode_buttons['paint'].click()
    assert str(labels.mode) == 'paint'
    embedded.selected_label_control.setValue(12)
    assert labels.selected_label == 12
    labels.selected_label = 8
    assert embedded.selected_label_control.value() == 8

    position = tuple(np.argwhere(labels.data == 2)[0])
    assert embedded.pick_demo_label_at_data_position(position) == 2
    assert labels.selected_label == 2


def test_paint_erase_display_and_z_navigation(embedded):
    labels = embedded.demo_labels()
    position = (labels.data.shape[0] // 2, 4, 4)
    embedded.set_selected_label(19)
    embedded.set_demo_label_mode('paint')
    labels.brush_size = 1
    labels.paint(position, 19)
    assert labels.data[position] == 19
    embedded.set_demo_label_mode('erase')
    labels.paint(position, 0)
    assert labels.data[position] == 0

    embedded.display_buttons[3].click()
    assert embedded.viewer.dims.ndisplay == 3
    embedded.display_buttons[2].click()
    assert embedded.viewer.dims.ndisplay == 2
    embedded.viewer.dims.set_current_step(0, 3)
    assert embedded.viewer.dims.current_step[0] == 3


def test_expected_ui_error_is_contained(embedded):
    messages = []
    embedded.status_changed.connect(messages.append)

    def fail():
        raise ValueError('controlled failure')

    assert not embedded._run_ui_action(fail)
    assert messages[-1] == 'Viewer action failed: controlled failure'


def test_cleanup_removes_callbacks_and_is_idempotent(embedded):
    labels = embedded.demo_labels()
    callback = embedded._on_demo_label_mouse_drag
    assert callback in labels.mouse_drag_callbacks
    embedded.set_ndisplay(3)
    embedded.shutdown()
    assert callback not in labels.mouse_drag_callbacks
    assert len(embedded.viewer.layers) == 0
    assert embedded.layer_list.count() == 0
    assert embedded.qt_viewer.close_count == 1
    embedded.shutdown()
    assert embedded.qt_viewer.close_count == 1


def test_repeated_construction_and_destruction(qapp):
    for _ in range(3):
        widget = EmbeddedNapariWidget(qt_viewer_factory=FakeQtViewer)
        widget.set_ndisplay(3)
        widget.shutdown()
        assert len(widget.viewer.layers) == 0
        assert widget.qt_viewer.close_count == 1
        widget.deleteLater()
        qapp.processEvents()
