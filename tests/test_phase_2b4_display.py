import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from napari.components import ViewerModel
from qtpy.QtWidgets import QApplication

from nuclear_segmentation.image_display_panel import ImageDisplayPanel
from nuclear_segmentation.project_state import ProjectState


def test_image_display_controls_are_event_driven_and_non_destructive():
    qapp = QApplication.instance() or QApplication([])
    viewer = ViewerModel()
    state = ProjectState()
    panel = ImageDisplayPanel(viewer, state)
    uint8_data = np.arange(100, dtype=np.uint8).reshape(10, 10)
    uint16_data = (np.arange(100, dtype=np.uint16) * 500).reshape(10, 10)
    float_data = np.linspace(-2.0, 4.0, 100, dtype=np.float32).reshape(10, 10)
    originals = [array.copy() for array in (uint8_data, uint16_data, float_data)]
    layers = [
        viewer.add_image(uint8_data, name="uint8"),
        viewer.add_image(uint16_data, name="uint16"),
        viewer.add_image(float_data, name="float32"),
    ]

    for layer, original in zip(layers, originals):
        viewer.layers.selection.active = layer
        qapp.processEvents()
        assert panel.layer is layer
        assert panel.layer_name.text() == layer.name
        assert panel.auto_contrast()
        expected = np.percentile(original, (1.0, 99.0))
        np.testing.assert_allclose(layer.contrast_limits, expected)
        panel.gamma.setValue(1.4)
        panel.opacity.setValue(0.6)
        assert layer.gamma == 1.4
        assert layer.opacity == 0.6
        np.testing.assert_array_equal(layer.data, original)

    active = layers[-1]
    active.contrast_limits = (-1.0, 3.0)
    active.gamma = 1.2
    active.opacity = 0.75
    qapp.processEvents()
    assert panel.minimum.value() == -1.0
    assert panel.maximum.value() == 3.0
    assert panel.gamma.value() == 1.2
    assert panel.opacity.value() == 0.75
    assert panel.reset_contrast()
    np.testing.assert_array_equal(active.data, originals[-1])

    viewer.layers.remove(active)
    qapp.processEvents()
    assert panel.layer is None
    assert not panel.section.content.isEnabled()
    panel.dispose()


def test_auto_contrast_excludes_artificial_polygon_fill_but_keeps_genuine_zeros():
    qapp = QApplication.instance() or QApplication([])
    viewer = ViewerModel()
    state = ProjectState()
    panel = ImageDisplayPanel(viewer, state)
    data = np.zeros((2, 4, 4), dtype=np.float32)
    mask = np.zeros((4, 4), dtype=bool)
    mask[1:3, 1:3] = True
    data[:, 1:3, 1:3] = np.array([[[0, 10], [20, 30]], [[0, 40], [50, 60]]])
    state.roi_mask_yx = mask
    layer = viewer.add_image(
        data,
        name="polygon crop",
        metadata={"nucytoseg_imported": True},
    )
    viewer.layers.selection.active = layer
    qapp.processEvents()
    assert panel.auto_contrast()
    expected = np.percentile(data[..., mask], (1.0, 99.0))
    np.testing.assert_allclose(layer.contrast_limits, expected)
    assert "valid polygon ROI" in panel.scope.text()
    assert expected[0] == 0.0
    panel.dispose()
