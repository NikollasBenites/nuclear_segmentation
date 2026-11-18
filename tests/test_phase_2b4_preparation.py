import os
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication, QWidget

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.image_preparation import (
    global_normalize_volume,
    prepare_channel,
)
from nuclear_segmentation.z_correction import DEFAULTS, correct_z


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
    raise AssertionError("Timed out waiting for image preparation")


def _window(qapp, tmp_path):
    source = tmp_path / "preparation.ome.tif"
    z = np.arange(4, dtype=np.float32)[:, None, None]
    data = np.empty((3, 4, 6, 7), dtype=np.float32)
    data[0] = np.arange(4 * 6 * 7, dtype=np.float32).reshape(4, 6, 7)
    data[1] = (z + 1) * np.ones((4, 6, 7), dtype=np.float32)
    data[2] = (5 - z) * np.linspace(1, 5, 42, dtype=np.float32).reshape(1, 6, 7)
    tifffile.imwrite(
        source,
        data,
        ome=True,
        metadata={
            "axes": "CZYX",
            "Channel": {"Name": ["DNA", "Marker A", "Marker B"]},
            "PhysicalSizeZ": 2.0,
            "PhysicalSizeY": 0.5,
            "PhysicalSizeX": 0.25,
        },
    )
    window = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat),
        restore_workspace=False,
    )
    assert window.prepare_image_import(source, confirm_replacement=False)
    _wait(qapp, lambda: window._import_thread is None)
    return window, data.copy()


def test_global_normalization_whole_z_mask_zeros_constants_and_validation():
    volume = np.array(
        [
            [[0, 1000, 1000], [0, 10, 20]],
            [[0, 1000, 1000], [0, 30, 40]],
        ],
        dtype=np.float32,
    )
    mask = np.array([[False, False, False], [True, True, True]])
    normalized, report = global_normalize_volume(
        volume,
        lower=0,
        upper=100,
        clip=True,
        valid_mask_yx=mask,
        scope="valid_working",
    )
    assert report["limits"] == [0.0, 40.0]
    assert report["sample_count"] == 6
    assert report["whole_z_volume_statistics"] is True
    assert report["per_plane_normalization"] is False
    assert normalized[0, 1, 1] == 0.25
    assert normalized[1, 1, 1] == 0.75
    assert np.all(normalized[:, ~mask] == 0)
    unbounded, unbounded_report = global_normalize_volume(
        volume,
        lower=40,
        upper=60,
        clip=False,
        valid_mask_yx=mask,
        scope="valid_working",
    )
    assert unbounded_report["clipping"] == "not clipped"
    assert np.min(unbounded[..., mask]) < 0
    assert np.max(unbounded[..., mask]) > 1
    constant, constant_report = global_normalize_volume(
        np.full((3, 2, 2), 7, dtype=np.uint16),
        lower=1,
        upper=99,
        clip=True,
    )
    assert constant_report["constant_input"] is True
    assert np.all(constant == 0)
    with pytest.raises(ValueError, match="NaN or infinite"):
        global_normalize_volume(
            np.full((2, 2, 2), np.nan), lower=1, upper=99, clip=True
        )
    with pytest.raises(ValueError, match="Percentiles"):
        global_normalize_volume(volume, lower=99, upper=1, clip=True)


def test_z_corrected_normalized_order_is_repeatable_and_non_cumulative():
    volume = np.arange(4 * 3 * 3, dtype=np.float32).reshape(4, 3, 3) + 1
    options = {**DEFAULTS, "sigma_planes": 0.0}
    first, first_report = prepare_channel(
        volume,
        "z_corrected_normalized",
        {"lower": 0, "upper": 100, "clip": True, "scope": "valid_working"},
        valid_mask_yx=np.ones((3, 3), bool),
        z_options=options,
    )
    second, second_report = prepare_channel(
        volume,
        "z_corrected_normalized",
        {"lower": 0, "upper": 100, "clip": True, "scope": "valid_working"},
        valid_mask_yx=np.ones((3, 3), bool),
        z_options=options,
    )
    corrected, _ = correct_z(volume, options)
    expected = (corrected - corrected.min()) / (corrected.max() - corrected.min())
    np.testing.assert_allclose(first, expected)
    np.testing.assert_allclose(second, first)
    assert first_report["processing_order"].endswith("global normalization")
    assert second_report["z_correction"]["options"] == first_report["z_correction"]["options"]
    np.testing.assert_array_equal(volume, np.arange(36, dtype=np.float32).reshape(4, 3, 3) + 1)


def test_multichannel_preparation_preview_provenance_cleanup_and_display_isolation(
    qapp, tmp_path, monkeypatch
):
    window, original = _window(qapp, tmp_path)
    panel = window.context_panel.preprocessing_page.image_preparation_panel
    options = {**DEFAULTS, "sigma_planes": 0.0}
    corrected, z_report = correct_z(original[2], options)
    z_report["options"] = options
    window.project_state.set_z_correction_preview(2, options, z_report)
    panel.channel_list.selectAll()
    panel._routes = {0: "original", 1: "normalized", 2: "z_corrected_normalized"}
    assert window.image_preparation.prepare(panel.selected_routes(), panel.options())
    _wait(qapp, lambda: not window.image_preparation.busy)
    assert set(window.image_preparation.prepared_channels) == {0, 1, 2}
    assert set(window.image_preparation.preview_layers) == {0, 1, 2}
    np.testing.assert_array_equal(window.image_preparation.prepared_channels[0], original[0])
    assert window.image_preparation.prepared_channels[1].dtype == np.float32
    assert np.min(window.image_preparation.prepared_channels[1]) >= 0
    assert np.max(window.image_preparation.prepared_channels[1]) <= 1
    expected = np.clip(
        (corrected - np.percentile(corrected, 1))
        / (np.percentile(corrected, 99) - np.percentile(corrected, 1)),
        0,
        1,
    )
    np.testing.assert_allclose(window.image_preparation.prepared_channels[2], expected)
    assert all(
        tuple(layer.scale) == (2.0, 0.5, 0.25)
        for layer in window.image_preparation.preview_layers.values()
    )
    assert window.image_preparation.provenance[2]["display_settings_applied"] is False
    prepared_copy = window.image_preparation.prepared_channels[2].copy()
    window.image_preparation.preview_layers[2].contrast_limits = (0.2, 0.8)
    np.testing.assert_array_equal(
        window.image_preparation.prepared_channels[2], prepared_copy
    )
    np.testing.assert_array_equal(window.project_state.original_dataset.data_czyx, original)
    window.project_state.update_channel(2, name="Marker B renamed")
    assert "Marker B renamed" in window.image_preparation.preview_layers[2].name
    assert window.image_preparation.provenance[2]["channel_name"] == "Marker B renamed"
    visible_layer = window.image_preparation.preview_layers[0]
    visible_layer.visible = False
    qapp.processEvents()
    assert visible_layer in window.embedding.viewer.layers

    old_layers = dict(window.image_preparation.preview_layers)
    old_arrays = {
        index: np.array(data, copy=True)
        for index, data in window.image_preparation.prepared_channels.items()
    }
    panel.lower.setValue(2.0)
    qapp.processEvents()
    assert window.image_preparation.outdated
    assert not window.image_preparation.is_current()
    assert window.image_preparation.preview_layers == old_layers
    assert all(layer in window.embedding.viewer.layers for layer in old_layers.values())
    assert all(layer.name.startswith("Outdated · ") for layer in old_layers.values())
    assert all(
        layer.metadata["nucytoseg_preparation_outdated"]
        for layer in old_layers.values()
    )
    assert "remain visible but are outdated" in panel.status.text()
    export_panel = window.context_panel.export_page
    assert export_panel.channels.count() == 0
    assert not export_panel.export_button.isEnabled()
    assert not window.image_export.export(
        tmp_path / "outdated-preparation.ome.tif", [0], "ome", "uint16"
    )
    for index, expected in old_arrays.items():
        np.testing.assert_array_equal(
            window.image_preparation.prepared_channels[index], expected
        )

    def fail_preparation(*args, **kwargs):
        raise ValueError("synthetic regeneration failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            "nuclear_segmentation.image_preparation_workspace.prepare_channel",
            fail_preparation,
        )
        assert window.image_preparation.prepare(panel.selected_routes(), panel.options())
        _wait(qapp, lambda: not window.image_preparation.busy)
    assert "failed" in panel.status.text().lower()
    assert window.image_preparation.preview_layers == old_layers
    assert window.image_preparation.outdated

    assert window.image_preparation.prepare(panel.selected_routes(), panel.options())
    _wait(qapp, lambda: not window.image_preparation.busy)
    assert window.image_preparation.is_current()
    assert not window.image_preparation.outdated
    assert all(layer not in window.embedding.viewer.layers for layer in old_layers.values())
    assert len(window.image_preparation.preview_layers) == 3
    assert all(
        not layer.metadata["nucytoseg_preparation_outdated"]
        for layer in window.image_preparation.preview_layers.values()
    )
    assert export_panel.channels.count() == 3
    assert export_panel.export_button.isEnabled()

    window.image_preparation.clear_previews()
    assert not window.image_preparation.prepared_channels
    assert not window.image_preparation.preview_layers
    assert 2 in window.project_state.z_correction_reports
    assert window.image_preparation.prepare(panel.selected_routes(), panel.options())
    _wait(qapp, lambda: not window.image_preparation.busy)
    window.image_preparation.invalidate()
    assert not window.image_preparation.prepared_channels
    assert not window.image_preparation.preview_layers
    assert window.image_preparation.prepare(panel.selected_routes(), panel.options())
    _wait(qapp, lambda: not window.image_preparation.busy)
    assert window.close_project(confirm=False)
    assert not window.image_preparation.prepared_channels
    assert not window.image_preparation.preview_layers
    window.close()
