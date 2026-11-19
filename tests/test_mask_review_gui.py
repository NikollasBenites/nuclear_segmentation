import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest
from qtpy.QtCore import QSettings
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QLabel, QWidget

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
        "scientific_config": window.context_panel.segmentation_page.scientific_config(),
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


def test_filter_slider_is_debounced_without_requesting_measurement(review_window):
    window, _ = review_window
    panel = window.context_panel.mask_review_page
    emitted = []
    recalculated = []
    panel.filters_changed.connect(lambda: emitted.append(True))
    panel.calculate_measurements_requested.connect(lambda: recalculated.append(True))
    panel.minimum_sphericity.editor.setValue(.61)
    panel.minimum_sphericity.editor.setValue(.62)
    panel.minimum_sphericity.editor.setValue(.63)
    assert emitted == []
    QTest.qWait(150)
    assert emitted == [True]
    assert recalculated == []


def test_pending_segmentation_parameters_preserve_masks_and_filtering(review_window):
    window, masks = review_window
    state = window.project_state
    review = window.context_panel.mask_review_page
    executed = state.segmentation_result["scientific_config"]
    state.set_filtering_result({
        "filtered_masks": np.array(masks, copy=True), "properties": pd.DataFrame(),
        "retained_ids": (1, 2), "roi_reviewed": True,
    })
    window.context_panel.segmentation_page.cellprob.setValue(
        executed["CELLPROB_THRESHOLD"] - .25
    )
    assert state.segmentation_is_current
    assert state.filtering_is_current
    assert review.calculate_button.isEnabled()
    assert window.segmentation.result_layer.name == "Raw Cellpose masks"
    config = window.filtering._measurement_config()
    assert config["CELLPROB_THRESHOLD"] == executed["CELLPROB_THRESHOLD"]
    np.testing.assert_array_equal(state.segmentation_result["masks"], masks)


def test_measured_intensity_defines_slider_range(review_window):
    window, _ = review_window
    panel = window.context_panel.mask_review_page
    result = {
        "properties": pd.DataFrame(
            {"volume_um3": [12.5, 80.25]}, index=[1, 2]
        ),
        "raw_mask_shape": (4, 8, 9), "settings": {},
        "intensity_definitions": [{
            "index": 0, "name": "DAPI", "column": "mean_intensity_DAPI",
            "minimum": -5.5, "maximum": 2048.75, "enabled": True,
        }],
        "perinuclear_definitions": [], "retained_ids": (1, 2),
        "roi_reviewed": False,
    }
    panel.configure_filtering(result)
    _, _, low, high, _ = panel.intensity_controls[0]
    assert low.editor.minimum() == -5.5
    assert high.editor.maximum() == 2048.75
    assert panel.minimum_volume.editor.maximum() == 81.0


def test_optional_filter_controls_follow_active_counts(review_window):
    window, _ = review_window
    panel = window.context_panel.mask_review_page
    base = {
        "properties": pd.DataFrame({"volume_um3": [20.]}, index=[1]),
        "raw_mask_shape": (4, 8, 9), "retained_ids": (1,),
        "roi_reviewed": False,
        "intensity_definitions": [
            {"index": 0, "name": "A", "column": "mean_intensity_A",
             "minimum": 0., "maximum": 100., "enabled": True},
            {"index": 1, "name": "B", "column": "mean_intensity_B",
             "minimum": 10., "maximum": 200., "enabled": True},
        ],
        "perinuclear_definitions": [],
        "settings": {"intensity_filters": {}},
    }
    panel.configure_filtering(base)
    assert not hasattr(panel, "perinuclear_filter_note")
    assert not any(
        "No perinuclear marker measurements" in label.text()
        for label in panel.findChildren(QLabel)
    )
    assert not panel.intensity_master.isChecked()
    assert panel.intensity_combination.isHidden()
    assert all(group.isHidden() for group, *_ in panel.intensity_controls.values())
    assert panel.perinuclear_combination.isHidden()

    panel.intensity_master.setChecked(True)
    assert all(not group.isHidden() for group, *_ in panel.intensity_controls.values())
    assert panel.intensity_combination.isHidden()  # zero individually enabled
    first = panel.intensity_controls[0]
    first[1].setChecked(True)
    assert panel.intensity_combination.isHidden()
    second = panel.intensity_controls[1]
    second[1].setChecked(True)
    assert not panel.intensity_combination.isHidden()
    first[2].setValue(23.456)
    panel.intensity_master.setChecked(False)
    disabled_settings = panel.filter_settings()
    assert disabled_settings["intensity_filters"] == {}
    assert disabled_settings["intensity_filtering_enabled"] is False
    panel.intensity_master.setChecked(True)
    assert panel.filter_settings()["intensity_filtering_enabled"] is True
    assert first[2].value() == pytest.approx(23.456)

    one_marker = dict(base, perinuclear_definitions=[{
        "name": "Marker 1", "channel_index": 0,
        "column": "marker_1_ring_positive_fraction",
        "minimum_positive_fraction": .2,
    }])
    panel.configure_filtering(one_marker)
    assert panel.perinuclear_combination.isHidden()
    assert not next(iter(panel.perinuclear_controls.values()))[0].isHidden()
    two_markers = dict(one_marker, perinuclear_definitions=[
        *one_marker["perinuclear_definitions"],
        {"name": "Marker 2", "channel_index": 1,
         "column": "marker_2_ring_positive_fraction",
         "minimum_positive_fraction": .3},
    ])
    panel.configure_filtering(two_markers)
    assert not panel.perinuclear_combination.isHidden()


def test_reviewed_roi_survives_measurement_invalidation(review_window):
    window, masks = review_window
    state = window.project_state
    controller = window.filtering
    panel = window.context_panel.mask_review_page
    roi = masks > 0
    provenance = {"source": "compatible", "mask_shape": tuple(masks.shape)}
    result = {
        "properties": pd.DataFrame(
            {"volume_um3": [10.0, 12.0]}, index=[1, 2]
        ),
        "raw_mask_shape": tuple(masks.shape),
        "settings": {},
        "intensity_definitions": [],
        "perinuclear_definitions": [],
        "retained_ids": (1, 2),
        "retained_count": 2,
        "filtered_masks": np.array(masks, copy=True),
        "scientific_roi": np.array(roi, copy=True),
        "roi_reviewed": True,
        "measurement_provenance": {"source": "compatible"},
    }
    state.set_scientific_roi(roi, provenance, reviewed=True)
    state.set_filtering_result(result)
    controller._create_layers(result)
    panel.configure_filtering(result, True)

    state.invalidate_filtering()
    assert not state.filtering_is_current
    assert state.scientific_roi_reviewed
    assert controller.roi_layer.name == "MNTB ROI — reviewed"
    assert panel.accept_roi_button.isEnabled()

    controller.accept_roi()
    assert state.scientific_roi_reviewed
    assert not state.filtering_is_current

    recalculated = dict(
        result,
        scientific_roi=np.zeros_like(roi),
        roi_reviewed=False,
    )
    controller._pending_filter_settings = None
    controller._calculated(recalculated)
    assert state.filtering_is_current
    assert state.scientific_roi_reviewed
    np.testing.assert_array_equal(
        state.filtering_result["scientific_roi"], roi
    )
    assert controller.roi_layer.name == "MNTB ROI — reviewed"


def test_segmentation_result_can_navigate_to_existing_mask_review(review_window):
    window, _ = review_window
    window.set_workflow("segmentation")
    panel = window.context_panel.segmentation_page
    assert not panel.results_section.isHidden()
    panel.go_review_button.click()
    assert window.navigation.current_workflow == "mask_review"
    assert window.context_panel.heading.text() == "Mask Review"


def test_marker_configuration_is_owned_by_stable_channel_index(review_window):
    window, _ = review_window
    panel = window.context_panel.mask_review_page
    panel.add_marker()
    assert len(panel.marker_rows) == 1
    assert window.project_state.perinuclear_markers[0]["channel_index"] == 0
    row = panel.marker_rows[0]
    row.inner.setValue(0.25)
    row.outer.setValue(1.5)
    assert window.project_state.perinuclear_markers[0]["ring_inner_um"] == .25
    assert window.project_state.perinuclear_markers[0]["ring_outer_um"] == 1.5


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
