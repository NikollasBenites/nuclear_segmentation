import os
import sys
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtpy.QtCore import QThread, QTimer
from qtpy.QtWidgets import QApplication, QWidget

from nuclear_segmentation.config import defaults
from nuclear_segmentation.project_state import (
    ChannelDescriptor,
    ImportedDataset,
    ProjectState,
)
from nuclear_segmentation.scientific_input import prepare_scientific_input
from nuclear_segmentation.segmentation_execution import (
    SegmentationController,
    execute_cellpose_stage,
)
from nuclear_segmentation.segmentation_panel import SegmentationPanel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(qapp, predicate, timeout=5):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("Timed out waiting for segmentation worker")


def _state(tmp_path):
    data = np.arange(2 * 3 * 5 * 7, dtype=np.uint16).reshape(2, 3, 5, 7) + 1
    channels = (
        ChannelDescriptor(0, "DNA", "DNA", "segmentation"),
        ChannelDescriptor(1, "Marker", "Marker"),
    )
    dataset = ImportedDataset(
        source_path=tmp_path / "input.ome.tif",
        source_format="OME-TIFF",
        series_index=0,
        series_identifier="0",
        series_name="Image",
        reported_axes="CZYX",
        reported_shape=data.shape,
        standardized_shape=data.shape,
        dtype=str(data.dtype),
        data_czyx=data,
        voxel_spacing_zyx_um=(2.0, 0.5, 0.25),
        calibration_source="OME",
        channels=channels,
    )
    state = ProjectState()
    state.set_dataset(dataset)
    return state, data


def _config(state):
    config = defaults()
    config.update(
        RUN_MODE="segment",
        PROCESSING_DEVICE="cpu",
        CHANNELS=state.analysis_channel_records(),
        SEGMENTATION_CHANNEL=state.segmentation_channel,
        VOXEL_SPACING_OVERRIDE_UM=list(state.voxel_spacing_zyx_um),
        Z_CORRECTIONS=state.legacy_z_corrections(),
        CELLPOSE_NORMALIZE=True,
        CELLPOSE_ROI_NORMALIZATION=None,
    )
    return config


class _Device:
    def __init__(self, kind):
        self.type = kind

    def __str__(self):
        return self.type


@pytest.mark.parametrize("cytoplasmic", [False, True])
def test_authoritative_stage_maps_parameters_without_preprocessing_again(
    tmp_path, monkeypatch, cytoplasmic
):
    state, source = _state(tmp_path)
    if cytoplasmic:
        state.select_session(state.create_session("MAP2", scientific_role="cytoplasmic", channel_index=1).session_id)
    config = _config(state)
    local_model = tmp_path / "cytoplasm.model"
    local_model.touch()
    config.update(
        MODEL_NAME=str(local_model) if cytoplasmic else config["MODEL_NAME"],
        CELLPOSE_DO_3D=not cytoplasmic,
        CELLPOSE_STITCH_THRESHOLD=0.35,
        CELLPROB_THRESHOLD=-2.25,
        CELLPOSE_MIN_SIZE_VOXELS=19,
        BATCH_SIZE=7,
        FLOW3D_SMOOTH=[1.0, 2.0, 3.0],
        CELLPOSE_RESAMPLE=False,
        CELLPOSE_RESCALE=0.8,
        DIAMETER_PIXELS=17.0,
    )
    scientific = prepare_scientific_input(state, config)
    calls = {}

    class FakeModel:
        def __init__(self, device, pretrained_model):
            self.pretrained_model = pretrained_model
            calls["device"] = str(device)
            calls["model"] = pretrained_model

        def eval(self, image, **kwargs):
            calls["image"] = image
            calls["kwargs"] = kwargs
            masks = np.zeros(image.shape, dtype=np.uint16)
            masks[:, 1:3, 2:4] = 1
            return masks, [None, None, np.ones(image.shape)], None

    torch = SimpleNamespace(
        device=_Device,
        cuda=SimpleNamespace(is_available=lambda: False, synchronize=lambda: None),
        backends=SimpleNamespace(
            mps=SimpleNamespace(is_built=lambda: False, is_available=lambda: False)
        ),
    )
    cellpose = SimpleNamespace(
        io=SimpleNamespace(logger_setup=lambda: None),
        models=SimpleNamespace(CellposeModel=FakeModel, MODEL_NAMES=[defaults()["MODEL_NAME"]], get_user_models=lambda: []),
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "cellpose", cellpose)
    before = source.copy()
    result = execute_cellpose_stage(scientific, lambda message: None)

    assert calls["model"] == config["MODEL_NAME"]
    assert calls["device"] == "cpu"
    assert calls["kwargs"] == {
        "z_axis": 0,
        "channel_axis": None,
        "do_3D": not cytoplasmic,
        "stitch_threshold": 0.35 if cytoplasmic else 0.0,
        "anisotropy": pytest.approx(2.0 / 0.375),
        "batch_size": 7,
        "cellprob_threshold": -2.25,
        "min_size": 19,
        "progress": True,
        "resample": False,
        "rescale": 0.8,
        "normalize": True,
        **({} if cytoplasmic else {"flow3D_smooth": [1.0, 2.0, 3.0]}),
        "diameter": 17.0,
    }
    np.testing.assert_array_equal(calls["image"], source[int(cytoplasmic)])
    assert not np.shares_memory(calls["image"], source)
    np.testing.assert_array_equal(source, before)
    assert result["mask_shape"] == source.shape[1:]
    assert result["object_count"] == 1
    assert result["cellpose_normalize"] is True
    assert np.shares_memory(
        result["model_input_volume"], scientific.namespace["model_input_volume"]
    )
    assert not result["model_input_volume"].flags.writeable
    assert result["model_path"] == config["MODEL_NAME"]
    scientific.namespace["MODEL_NAME"] = str(tmp_path / "missing-model")
    with pytest.raises(ValueError, match="missing local model"):
        execute_cellpose_stage(scientific, lambda message: None)


class _Layer:
    def __init__(self, data, name, **kwargs):
        self.data = data
        self.name = name
        self.metadata = kwargs.get("metadata", {})
        self.editable = True


class _Viewer:
    def __init__(self):
        self.layers = []

    def add_labels(self, data, name, **kwargs):
        layer = _Layer(data, name, **kwargs)
        self.layers.append(layer)
        return layer


def _fake_result(scientific):
    shape = scientific.namespace["segmentation_volume"].shape
    masks = np.zeros(shape, dtype=np.uint16)
    masks[:, 1:3, 2:4] = 1
    return {
        "masks": masks,
        "mask_shape": shape,
        "object_count": 1,
        "maximum_label_id": 1,
        "segmentation_channel": scientific.namespace["SEGMENTATION_CHANNEL"],
        "model_name": scientific.namespace["MODEL_NAME"],
        "device": "cpu",
        "device_type": "cpu",
        "parameters": {},
        "input_provenance": scientific.provenance,
        "z_corrections": scientific.namespace["Z_CORRECTIONS"],
        "cellpose_normalize": scientific.namespace["CELLPOSE_NORMALIZE"],
        "model_load_seconds": 0.01,
        "evaluation_seconds": 0.02,
        "completed_at_utc": "test",
        "raw_3d_probs": None,
    }


def test_controller_runs_off_main_thread_retains_result_and_locks_conflicts(
    qapp, tmp_path
):
    state, source = _state(tmp_path)
    panel = SegmentationPanel(state)
    viewer = _Viewer()
    conflict = QWidget()
    operation = SimpleNamespace(busy=True)
    calls = {}

    def runner(scientific, progress):
        calls["thread"] = QThread.currentThread()
        calls["normalize"] = scientific.namespace["CELLPOSE_NORMALIZE"]
        calls["shares"] = np.shares_memory(
            scientific.namespace["image_czyx"], source
        )
        progress("Mock Cellpose execution")
        sleep(0.05)
        return _fake_result(scientific)

    controller = SegmentationController(
        viewer, state, panel, (conflict,), (operation,), runner=runner
    )
    controller._update_availability()
    assert not panel.execute_button.isEnabled()
    assert not controller.run()
    operation.busy = False
    controller._update_availability()
    event_loop_responsive = []
    QTimer.singleShot(0, lambda: event_loop_responsive.append(True))
    assert controller.run()
    assert controller.busy
    assert not conflict.isEnabled()
    assert not panel.execute_button.isEnabled()
    _wait(qapp, lambda: not controller.busy)

    assert event_loop_responsive
    assert calls["thread"] is not qapp.thread()
    assert calls["normalize"] is True
    assert calls["shares"]
    assert state.segmentation_is_current
    assert state.segmentation_result["mask_shape"] == source.shape[1:]
    session = state.nuclear_session
    assert (session.session_id, session.scientific_role, session.source_channel_index) == ("nuclear", "nuclear", 0)
    assert session.segmentation_result is state.segmentation_result
    assert session.geometry_revision == 1
    assert len(viewer.layers) == 1
    assert viewer.layers[0].editable is False
    assert viewer.layers[0].data is session.segmentation_result["masks"]
    assert not viewer.layers[0].data.flags.writeable
    np.testing.assert_array_equal(np.unique(viewer.layers[0].data), [0, 1])
    assert conflict.isEnabled()
    np.testing.assert_array_equal(state.original_dataset.data_czyx, source)
    executed_cellprob = state.segmentation_result["scientific_config"][
        "CELLPROB_THRESHOLD"
    ]
    panel.cellprob.setValue(panel.cellprob.value() - 0.5)
    assert state.segmentation_is_current
    assert viewer.layers[0].name == "Raw Cellpose masks"
    assert state.segmentation_result["scientific_config"][
        "CELLPROB_THRESHOLD"
    ] == executed_cellprob
    assert "Existing masks remain available" in panel.execution_summary.text()
    panel.cellprob.setValue(executed_cellprob)
    assert panel.execution_summary.text().startswith("Completed —")
    controller.dispose()
    panel.dispose()


def test_display_only_channel_change_does_not_invalidate_result(tmp_path):
    state, _ = _state(tmp_path)
    scientific = prepare_scientific_input(state, _config(state))
    state.apply_normalization({"mode": "cellpose_default", "cellpose_normalize": True})
    state.set_segmentation_result(_fake_result(scientific))
    owner = state.session_revision()
    state.update_channel(0, visible=False, colormap="magenta")
    assert state.segmentation_is_current
    assert state.normalization_is_current
    assert state.session_revision() == owner


def test_failed_rerun_preserves_previous_valid_result(qapp, tmp_path):
    state, _ = _state(tmp_path)
    panel = SegmentationPanel(state)
    viewer = _Viewer()

    def successful(scientific, progress):
        return _fake_result(scientific)

    controller = SegmentationController(viewer, state, panel, runner=successful)
    assert controller.run()
    _wait(qapp, lambda: not controller.busy)
    previous = state.segmentation_result
    owner = state.session_revision()

    def failing(scientific, progress):
        raise RuntimeError("simulated model failure")

    controller.runner = failing
    assert controller.run()
    _wait(qapp, lambda: not controller.busy)
    assert state.segmentation_result is previous
    assert state.segmentation_is_current
    assert state.session_revision() == owner
    assert "simulated model failure" in panel.execution_log.toPlainText()
    assert not panel.execution_section.isHidden()
    assert not panel.return_to_results_button.isHidden()
    assert len(viewer.layers) == 1

    state.invalidate_segmentation()
    assert not state.segmentation_is_current
    assert "outdated" in viewer.layers[0].name
    controller.clear_project()
    assert not viewer.layers
    controller.dispose()
    panel.dispose()


@pytest.mark.parametrize("change", ["project", "input", "geometry", "session"])
def test_running_job_cannot_replace_a_newer_owner(qapp, tmp_path, change):
    state, source = _state(tmp_path)
    panel = SegmentationPanel(state)
    started, release = Event(), Event()
    dataset = state.original_dataset
    seen = {}

    def runner(scientific, progress):
        started.set()
        assert release.wait(5)
        seen.update(scientific.namespace)
        return _fake_result(scientific)

    controller = SegmentationController(_Viewer(), state, panel, runner=runner)
    try:
        assert controller.run()
        _wait(qapp, started.is_set)
        assert not controller.run()
        if change == "project":
            state.set_dataset(dataset)
        elif change == "input":
            state.set_calibration((3.0, 0.5, 0.25))
        elif change == "session":
            state.select_session(state.create_session("Second", scientific_role="cytoplasmic", channel_index=1).session_id)
        else:
            state.set_segmentation_result(_fake_result(prepare_scientific_input(state, _config(state))))
        current = state.segmentation_result
        current_owner = state.session_revision()
        release.set()
        _wait(qapp, lambda: not controller.busy)
        assert state.segmentation_result is current
        assert state.session_revision() == current_owner
        assert "Segmentation discarded" in panel.execution_log.toPlainText()
        if change == "session":
            assert state.nuclear_session.segmentation_result is None
        assert seen["image_czyx"] is source
        assert seen["VOXEL_SPACING_UM"] == (2.0, 0.5, 0.25)
    finally:
        release.set()
        _wait(qapp, lambda: not controller.busy)
        controller.dispose()
        panel.dispose()


@pytest.mark.parametrize("defect", ["provenance", "empty masks"])
def test_invalid_completion_preserves_previous_analysis(qapp, tmp_path, defect):
    state, _ = _state(tmp_path)
    panel = SegmentationPanel(state)
    previous = _fake_result(prepare_scientific_input(state, _config(state)))
    state.set_segmentation_result(previous)
    state.set_filtering_result({"filtered_masks": previous["masks"]})
    state.set_scientific_roi(previous["masks"] > 0, {}, reviewed=True)
    state.set_quantification_result({"retained_count": 1})
    session = state.nuclear_session
    raw, filtered, roi, quant = (session.segmentation_result, session.filtering_result,
                                 session.scientific_roi, session.quantification_result)
    owner = state.session_revision()

    def runner(scientific, progress):
        result = _fake_result(scientific)
        if defect == "provenance":
            result["input_provenance"] = dict(scientific.provenance, source_series=42)
        else:
            result["masks"].fill(0)
        return result

    controller = SegmentationController(_Viewer(), state, panel, runner=runner)
    try:
        assert controller.run()
        _wait(qapp, lambda: not controller.busy)
        assert "Invalid segmentation result" in panel.execution_log.toPlainText()
        assert session.segmentation_result is raw and session.segmentation_is_current
        assert session.filtering_result is filtered and session.filtering_is_current
        assert session.scientific_roi is roi and session.scientific_roi_reviewed
        assert session.quantification_result is quant and session.quantification_is_current
        assert state.session_revision() == owner
    finally:
        controller.dispose()
        panel.dispose()


def test_completed_results_use_executed_values_and_future_settings_are_separate(
    qapp, tmp_path
):
    state, _ = _state(tmp_path)
    panel = SegmentationPanel(state)
    viewer = _Viewer()
    controller = SegmentationController(
        viewer, state, panel, runner=lambda scientific, progress: _fake_result(scientific)
    )

    executed_cellprob = panel.cellprob.value()
    assert controller.run()
    _wait(qapp, lambda: not controller.busy)
    assert not panel.results_section.isHidden()
    assert panel.model_section.isHidden()
    assert "Status: Completed" in panel.result_summary.text()
    assert "Segmentation type: Nuclear" in panel.result_summary.text()

    panel.view_parameters_button.setChecked(True)
    assert not panel.executed_parameters.isHidden()
    assert f"Cell probability threshold: {executed_cellprob}" in panel.executed_parameters.text()

    previous = state.segmentation_result
    panel.run_new_button.click()
    assert not panel.model_section.isHidden()
    assert panel.results_section.isHidden()
    panel.cellprob.setValue(executed_cellprob - 1)
    assert state.segmentation_result is previous
    assert state.segmentation_is_current
    assert "Existing masks remain available" in panel.execution_summary.text()

    panel.return_to_results_button.click()
    assert not panel.results_section.isHidden()
    assert str(executed_cellprob) in panel.executed_parameters.text()
    assert str(panel.cellprob.value()) not in panel.executed_parameters.text()

    controller.dispose()
    panel.dispose()
