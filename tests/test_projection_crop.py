import os
from pathlib import Path
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication, QWidget

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.projection_crop import (
    crop_czyx,
    generate_xy_projection,
    masked_crop_czyx,
    polygon_roi_mask,
    selection_bounding_box,
)
from nuclear_segmentation.project_state import (
    ChannelDescriptor,
    ImportedDataset,
    ProjectState,
)


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
    raise AssertionError("Timed out waiting for background projection work")


def test_maximum_and_mean_projection_preserve_geometry_and_precision():
    integer = np.array(
        [[[65535, 10], [20, 30]], [[65535, 30], [40, 50]]], dtype=np.uint16
    )
    maximum = generate_xy_projection(integer, "maximum")
    mean = generate_xy_projection(integer, "mean")
    assert maximum.dtype == np.uint16
    np.testing.assert_array_equal(maximum, [[65535, 30], [40, 50]])
    assert mean.dtype == np.float64
    np.testing.assert_allclose(mean, [[65535, 20], [30, 40]])
    floating = integer.astype(np.float32)
    assert generate_xy_projection(floating, "mean").dtype == np.float32
    np.testing.assert_array_equal(integer[0], [[65535, 10], [20, 30]])


def test_polygon_rectangle_bounds_clipping_and_invalid_selection():
    polygon = np.array([[-3.2, 2.1], [4.2, 11.8], [9.9, 4.0]])
    assert selection_bounding_box(polygon, (8, 10)) == (0, 8, 2, 10)
    rectangle = np.array([[2, 3], [2, 8], [7, 8], [7, 3]])
    assert selection_bounding_box(rectangle, (20, 30)) == (2, 7, 3, 8)
    with pytest.raises(ValueError, match="non-empty"):
        selection_bounding_box([], (20, 30))
    with pytest.raises(ValueError, match="outside"):
        selection_bounding_box([[-5, -5], [-2, -1]], (20, 30))


def test_polygon_mask_is_boolean_deterministic_and_rejects_invalid_geometry():
    vertices = np.array([[2, 3], [2, 9], [8, 3]], dtype=float)
    bbox, mask = polygon_roi_mask(vertices, (12, 14))
    assert bbox == (2, 8, 3, 9)
    assert mask.dtype == bool
    assert mask.shape == (6, 6)
    assert mask[0, 0]
    assert not mask[-1, -1]
    bbox_again, mask_again = polygon_roi_mask(vertices, (12, 14))
    assert bbox_again == bbox
    np.testing.assert_array_equal(mask_again, mask)
    with pytest.raises(ValueError, match="Self-intersecting"):
        polygon_roi_mask([[1, 1], [7, 7], [1, 7], [7, 1]], (10, 10))
    with pytest.raises(ValueError, match="degenerate"):
        polygon_roi_mask([[1, 1], [2, 2], [3, 3]], (10, 10))
    with pytest.raises(ValueError, match="finite"):
        polygon_roi_mask([[1, 1], [2, np.nan], [3, 1]], (10, 10))


def test_polygon_masked_crop_preserves_dtype_z_channels_and_source():
    source = np.arange(2 * 3 * 10 * 11, dtype=np.uint16).reshape(2, 3, 10, 11)
    original = source.copy()
    bbox, mask = polygon_roi_mask([[1, 2], [1, 8], [7, 2]], source.shape[-2:])
    cropped = masked_crop_czyx(source, bbox, mask)
    assert cropped.shape == (2, 3, *mask.shape)
    assert cropped.dtype == source.dtype
    expected = source[:, :, bbox[0] : bbox[1], bbox[2] : bbox[3]].copy()
    expected[:, :, ~mask] = 0
    np.testing.assert_array_equal(cropped, expected)
    np.testing.assert_array_equal(source, original)
    with pytest.raises(ValueError, match="does not match"):
        masked_crop_czyx(source, bbox, np.ones((2, 2), dtype=bool))


def _dataset(source_format="TIFF"):
    data = np.arange(2 * 4 * 12 * 14, dtype=np.uint16).reshape(2, 4, 12, 14)
    return ImportedDataset(
        source_path=Path("experiment.lif" if source_format == "Leica LIF" else "image.tif"),
        source_format=source_format,
        series_index=2 if source_format == "Leica LIF" else 0,
        series_identifier="MNTB",
        series_name="Ipsilateral MNTB",
        reported_axes="TCZYX" if source_format == "Leica LIF" else "CZYX",
        reported_shape=data.shape,
        standardized_shape=data.shape,
        dtype=str(data.dtype),
        data_czyx=data,
        voxel_spacing_zyx_um=(2.0, 0.5, 0.25),
        calibration_source="test metadata",
        channels=(
            ChannelDescriptor(0, "DNA", "DNA", "segmentation", "cyan", True),
            ChannelDescriptor(1, "Marker", "Marker", "measurement", "magenta", True),
        ),
    )


@pytest.mark.parametrize("source_format", ["TIFF", "Leica LIF"])
def test_project_crop_preserves_source_identity_z_dtype_values_and_provenance(
    source_format,
):
    dataset = _dataset(source_format)
    original = dataset.data_czyx.copy()
    state = ProjectState()
    state.set_dataset(dataset)
    vertices = ((2.2, 3.1), (2.2, 10.0), (9.0, 10.0), (9.0, 3.1))
    working = state.apply_crop((2, 9, 3, 10), vertices)
    assert working.source_format == source_format
    assert working.series_index == dataset.series_index
    assert working.data_czyx.shape == (2, 4, 7, 7)
    assert working.data_czyx.dtype == dataset.data_czyx.dtype
    np.testing.assert_array_equal(working.data_czyx, original[:, :, 2:9, 3:10])
    np.testing.assert_array_equal(state.original_dataset.data_czyx, original)
    assert state.crop_provenance.bounding_box_y0_y1_x0_x1 == (2, 9, 3, 10)
    assert state.crop_provenance.spatial_offset_yx_um == (1.0, 0.75)
    assert state.working_offset_yx == (2, 3)
    assert state.reset_crop() is dataset
    np.testing.assert_array_equal(state.working_dataset.data_czyx, original)


def test_project_polygon_crop_records_independent_mask_and_physical_provenance():
    dataset = _dataset("Leica LIF")
    original = dataset.data_czyx.copy()
    state = ProjectState()
    state.set_dataset(dataset)
    vertices = np.array([[2, 3], [2, 10], [9, 3]], dtype=float)
    bbox, mask = polygon_roi_mask(vertices, dataset.data_czyx.shape[-2:])
    working = state.apply_crop(bbox, vertices, mask, "polygon")
    assert working.data_czyx.shape == (2, 4, *mask.shape)
    assert working.data_czyx.dtype == np.uint16
    assert np.all(working.data_czyx[:, :, ~mask] == 0)
    rectangular = original[:, :, bbox[0] : bbox[1], bbox[2] : bbox[3]]
    np.testing.assert_array_equal(working.data_czyx[:, :, mask], rectangular[:, :, mask])
    np.testing.assert_array_equal(state.original_dataset.data_czyx, original)
    assert state.roi_mask_yx is not mask
    np.testing.assert_array_equal(state.roi_mask_yx, mask)
    assert state.crop_provenance.selection_type == "polygon"
    assert state.crop_provenance.spatial_offset_yx_um == (1.0, 0.75)
    assert state.crop_provenance.original_vertices_yx_um[0] == (1.0, 0.75)
    assert state.crop_provenance.roi_pixel_count == int(mask.sum())
    assert state.crop_provenance.roi_area_um2 == pytest.approx(mask.sum() * 0.5 * 0.25)


def test_crop_czyx_rejects_invalid_bounds_without_copying_source():
    data = np.arange(2 * 3 * 8 * 9, dtype=np.uint8).reshape(2, 3, 8, 9)
    cropped = crop_czyx(data, (1, 7, 2, 8))
    assert cropped.shape == (2, 3, 6, 6)
    assert np.shares_memory(cropped, data)
    with pytest.raises(ValueError, match="invalid"):
        crop_czyx(data, (5, 5, 2, 8))


def test_desktop_projection_preview_apply_reset_and_review_isolation(qapp, tmp_path):
    source = tmp_path / "crop.ome.tif"
    original = np.arange(2 * 4 * 12 * 14, dtype=np.uint16).reshape(2, 4, 12, 14)
    tifffile.imwrite(
        source,
        original,
        ome=True,
        metadata={
            "axes": "CZYX",
            "Channel": {"Name": ["DNA", "Marker"]},
            "PhysicalSizeZ": 2.0,
            "PhysicalSizeY": 0.5,
            "PhysicalSizeX": 0.25,
        },
    )
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait(qapp, lambda: window._import_thread is None)
    source_identity = window.project_state.original_dataset.data_czyx
    source_copy = source_identity.copy()
    window.set_workflow("preprocessing")
    assert window.crop_workspace.generate_projection(1, "mean")
    _wait(qapp, lambda: window.crop_workspace._thread is None)
    projection = window.crop_workspace.projection_layer
    assert projection.data.shape == (12, 14)
    assert projection.data.dtype == np.float64
    assert tuple(projection.scale) == (0.5, 0.25)
    assert projection.metadata["nucytoseg_projection"]
    assert window.context_panel.preprocessing_page.clear_projection_button.isEnabled()
    temporary_overlay = window.embedding.viewer.add_image(
        np.ones((4, 12, 14), dtype=np.float32),
        name="Unrelated preprocessing preview",
        metadata={"nucytoseg_normalization_preview": True},
    )
    assert window.crop_workspace.clear_projection(confirm=False)
    assert window.crop_workspace.projection_layer is None
    assert temporary_overlay in window.embedding.viewer.layers
    assert not window.context_panel.preprocessing_page.clear_projection_button.isEnabled()
    np.testing.assert_array_equal(source_identity, source_copy)
    assert window.crop_workspace.generate_projection(1, "mean")
    _wait(qapp, lambda: window.crop_workspace._thread is None)

    assert window.crop_workspace.start_drawing("rectangle")
    selection = window.crop_workspace.selection_layer
    assert selection.ndim == 2
    assert window.viewer_toolbar.set_ndisplay(3)
    assert str(selection.mode) == "pan_zoom"
    assert window.embedding.viewer.layers.selection.active is not selection
    assert window.viewer_toolbar.set_ndisplay(2)
    assert window.crop_workspace.start_drawing("rectangle")
    selection.add(
        np.array([[2, 3], [2, 10], [9, 10], [9, 3]], dtype=float),
        shape_type="rectangle",
    )
    assert window.crop_workspace.edit_selection()
    assert str(selection.mode) == "select"
    assert window.crop_workspace.preview_crop()
    assert len(window.crop_workspace.preview_layers) == 2
    assert all(layer.data.shape == (4, 7, 7) for layer in window.crop_workspace.preview_layers)
    assert all(tuple(layer.scale) == (2.0, 0.5, 0.25) for layer in window.crop_workspace.preview_layers)
    assert tuple(window.crop_workspace.preview_layers[0].translate) == (0.0, 1.0, 0.75)
    np.testing.assert_array_equal(
        window.crop_workspace.preview_layers[1].data, original[1, :, 2:9, 3:10]
    )
    window.crop_workspace.cancel_preview()
    assert not window.crop_workspace.preview_layers
    assert all(layer.visible == channel.visible for layer, channel in zip(
        [item for item in window.embedding.viewer.layers if item.metadata.get("nucytoseg_imported")],
        window.project_state.channels,
    ))

    assert window.crop_workspace.preview_crop()
    assert window.crop_workspace.confirm_crop()
    assert window.project_state.working_dataset.standardized_shape == (2, 4, 7, 7)
    imported = [
        layer
        for layer in window.embedding.viewer.layers
        if layer.metadata.get("nucytoseg_imported")
    ]
    assert len(imported) == 2
    assert all(layer.data.shape == (4, 7, 7) for layer in imported)
    assert all(tuple(layer.translate) == (0.0, 1.0, 0.75) for layer in imported)
    assert not any(layer.metadata.get("nucytoseg_crop_preview") for layer in window.embedding.viewer.layers)
    np.testing.assert_array_equal(source_identity, source_copy)
    confirmed_shape = window.project_state.working_dataset.standardized_shape
    assert window.crop_workspace.generate_projection(0, "maximum")
    _wait(qapp, lambda: window.crop_workspace._thread is None)
    external_projection = window.crop_workspace.projection_layer
    window.embedding.viewer.layers.remove(external_projection)
    qapp.processEvents()
    assert window.crop_workspace.projection_layer is None
    assert not window.context_panel.preprocessing_page.clear_projection_button.isEnabled()
    assert window.project_state.working_dataset.standardized_shape == confirmed_shape

    labels_data = np.zeros((4, 7, 7), dtype=np.uint16)
    labels_data[:, 2:4, 2:4] = 1
    labels = window.embedding.viewer.add_labels(labels_data.copy(), name="Review masks")
    window.embedding.viewer.layers.selection.active = labels
    assert window.viewer_toolbar.activate_tool("review")
    assert window.mask_selection._on_mouse_press in labels.mouse_drag_callbacks
    assert window.crop_workspace.generate_projection(0, "maximum")
    _wait(qapp, lambda: window.crop_workspace._thread is None)
    projection_item = window.layer_panel.model.item_for_layer(
        window.crop_workspace.projection_layer
    )
    assert projection_item.parent().text() == "Annotations & Overlays"
    assert window.crop_workspace.start_drawing("polygon")
    assert window.mask_selection._on_mouse_press not in labels.mouse_drag_callbacks
    np.testing.assert_array_equal(labels.data, labels_data)

    assert window.crop_workspace.reset_crop()
    restored = [
        layer
        for layer in window.embedding.viewer.layers
        if layer.metadata.get("nucytoseg_imported")
    ]
    assert all(layer.data.shape == (4, 12, 14) for layer in restored)
    assert all(tuple(layer.translate) == (0.0, 0.0, 0.0) for layer in restored)
    np.testing.assert_array_equal(window.project_state.original_dataset.data_czyx, source_copy)

    assert window.crop_workspace.generate_projection(0, "maximum")
    _wait(qapp, lambda: window.crop_workspace._thread is None)
    assert window.crop_workspace.start_drawing("polygon")
    polygon = window.crop_workspace.selection_layer
    vertices = np.array([[2, 3], [2, 10], [9, 3]], dtype=float)
    polygon.add(vertices, shape_type="polygon")
    expected_bbox, expected_mask = polygon_roi_mask(vertices, original.shape[-2:])
    assert window.crop_workspace.preview_crop()
    for channel, layer in enumerate(window.crop_workspace.preview_layers):
        expected = masked_crop_czyx(
            original[channel : channel + 1], expected_bbox, expected_mask
        )[0]
        np.testing.assert_array_equal(layer.data, expected)
    np.testing.assert_array_equal(window.project_state.original_dataset.data_czyx, source_copy)
    assert window.crop_workspace.confirm_crop()
    np.testing.assert_array_equal(window.project_state.roi_mask_yx, expected_mask)
    assert window.project_state.crop_provenance.selection_type == "polygon"
    assert np.all(window.project_state.working_dataset.data_czyx[:, :, ~expected_mask] == 0)
    assert window.crop_workspace.reset_crop()
    window.close()
