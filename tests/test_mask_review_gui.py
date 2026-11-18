import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication, QWidget

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.project_state import ChannelDescriptor, ImportedDataset


class FakeQtViewer(QWidget):
    def __init__(self, viewer, show_welcome_screen=False):
        super().__init__()
        self.viewer = viewer


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def review_window(qapp, tmp_path):
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=QSettings(
            str(tmp_path / "review.ini"), QSettings.Format.IniFormat
        ),
        restore_workspace=False,
    )
    image = np.ones((1, 4, 8, 9), dtype=np.uint16)
    channels = (ChannelDescriptor(0, "DAPI", "DAPI", "segmentation"),)
    dataset = ImportedDataset(
        source_path=tmp_path / "review.ome.tif",
        source_format="OME-TIFF",
        series_index=0,
        series_identifier="0",
        series_name="Image",
        reported_axes="CZYX",
        reported_shape=image.shape,
        standardized_shape=image.shape,
        dtype=str(image.dtype),
        data_czyx=image,
        voxel_spacing_zyx_um=(2.0, 0.5, 0.5),
        calibration_source="OME",
        channels=channels,
    )
    window.project_state.set_dataset(dataset)
    masks = np.zeros(image.shape[1:], dtype=np.uint16)
    masks[:, 1:3, 1:3] = 1
    masks[:, 5:7, 6:8] = 2
    result = {
        "masks": masks,
        "mask_shape": masks.shape,
        "object_count": 2,
        "device": "cpu",
    }
    window.project_state.set_segmentation_result(result)
    yield window, masks
    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_mask_review_page_activation_and_result_summary(review_window):
    window, masks = review_window
    window.set_workflow("mask_review")
    panel = window.context_panel.mask_review_page
    assert window.context_panel.heading.text() == "Mask Review"
    assert window.context_panel.stack.currentWidget().widget() is panel
    assert panel.segmentation_status.text() == "Segmentation: available and current"
    assert panel.mask_count.text() == "Detected masks: 2"
    assert panel.show_all_button.isEnabled()
    assert not panel.show_selected_button.isEnabled()
    assert not panel.keep_button.isEnabled()
    assert "ManualReview" in panel.review_safeguard.text()
    layer = window.segmentation.result_layer
    assert layer is not None
    assert np.shares_memory(np.asarray(layer.data), masks)
    assert not layer.editable


def test_existing_selection_updates_review_and_visualization(review_window):
    window, masks = review_window
    panel = window.context_panel.mask_review_page
    layer = window.segmentation.result_layer
    window.embedding.viewer.layers.selection.active = layer
    assert window.viewer_toolbar.activate_tool("review")
    callback = window.mask_selection._on_mouse_press
    assert callback in layer.mouse_drag_callbacks

    first = tuple(np.argwhere(masks == 1)[0])
    second = tuple(np.argwhere(masks == 2)[0])
    background = tuple(np.argwhere(masks == 0)[0])
    assert window.mask_selection.select_at_data_position(layer, first) == 1
    assert panel.mask_id.text() == "Selected Mask ID: 1"
    assert panel.active_layer.text() == "Active segmentation layer: Raw Cellpose masks"
    assert panel.mask_type.text() == "Mask type: nuclear"
    assert panel.show_selected_button.isEnabled()
    panel.show_selected_button.click()
    assert layer.show_selected_label
    assert layer.selected_label == 1
    assert "Mask ID 1" in panel.visualization_status.text()

    assert window.mask_selection.select_at_data_position(layer, second) == 2
    assert panel.mask_id.text() == "Selected Mask ID: 2"
    panel.show_all_button.click()
    assert not layer.show_selected_label
    assert callback in layer.mouse_drag_callbacks

    assert window.mask_selection.select_at_data_position(layer, background) is None
    assert panel.mask_id.text() == "Selected Mask ID: —"
    assert not panel.show_selected_button.isEnabled()
    np.testing.assert_array_equal(layer.data, masks)


def test_clear_selection_outdated_replacement_and_failed_rerun(review_window):
    window, masks = review_window
    panel = window.context_panel.mask_review_page
    old_layer = window.segmentation.result_layer
    window.embedding.viewer.layers.selection.active = old_layer
    window.viewer_toolbar.activate_tool("review")
    position = tuple(np.argwhere(masks == 1)[0])
    window.mask_selection.select_at_data_position(old_layer, position)
    panel.clear_button.click()
    assert window.mask_selection.selected_mask_id is None
    assert panel.mask_id.text() == "Selected Mask ID: —"
    assert not old_layer.show_selected_label

    previous = window.project_state.segmentation_result
    window.segmentation._failed("RuntimeError: simulated failure")
    assert window.project_state.segmentation_result is previous
    assert panel.mask_count.text() == "Detected masks: 2"

    window.project_state.invalidate_segmentation()
    assert panel.segmentation_status.text() == "Segmentation: outdated"
    assert "outdated" in panel.review_status.text().lower()

    replacement = np.zeros_like(masks)
    replacement[:, 2:5, 3:6] = 7
    window.project_state.set_segmentation_result(
        {
            "masks": replacement,
            "mask_shape": replacement.shape,
            "object_count": 1,
            "device": "cpu",
        }
    )
    new_layer = window.segmentation.result_layer
    assert new_layer is not old_layer
    assert old_layer not in window.embedding.viewer.layers
    assert np.shares_memory(np.asarray(new_layer.data), replacement)
    assert panel.mask_count.text() == "Detected masks: 1"
    assert panel.segmentation_status.text() == "Segmentation: available and current"


def test_project_close_clears_review_state(review_window):
    window, _ = review_window
    panel = window.context_panel.mask_review_page
    assert window.close_project(confirm=False)
    assert panel.segmentation_status.text() == "Segmentation: missing"
    assert panel.mask_count.text() == "Detected masks: —"
    assert panel.mask_id.text() == "Selected Mask ID: —"
    assert window.segmentation.result_layer is None
