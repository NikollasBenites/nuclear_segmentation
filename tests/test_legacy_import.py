"""Legacy writer round trips, with the desktop import workers and checkpoint UI."""
import hashlib
import json
import shutil

import numpy as np
import pandas as pd
import pytest
from qtpy.QtWidgets import QFileDialog

from nuclear_segmentation.image_export_workspace import _write_scientific_package
from nuclear_segmentation.runtime import AnalysisSession
from nuclear_segmentation.scientific_input import prepare_scientific_input
from test_desktop_normalization import _wait
from test_native_project import _forbid_calculations, open_saved, project, save
from test_workflow import model_viewer, sample


@pytest.fixture
def package(sample, tmp_path):
    config, source, previous, masks = sample
    config["COMPUTE_INPUT_SHA256"] = True
    record = previous / "analysis_config.json"
    saved = json.loads(record.read_text())
    saved["channels"][0]["filter_enabled"] = True
    record.write_text(json.dumps(saved))
    session = AnalysisSession(config, source, previous, log=lambda *_: None).prepare()
    viewer = model_viewer()
    try:
        session.attach(viewer)
        session.ns["manual_review"].set(7, "exclude", "Reviewed manually")
        session.ns["accept_mntb_roi"]()

        class Dialog:
            @staticmethod
            def getExistingDirectory(*args):
                return str(tmp_path)

        session.ns["QFileDialog"] = Dialog
        session.export()
        return session.ns["sample_export_directory"], source, masks
    finally:
        viewer.close()


def modify(folder, change):
    path = folder / "analysis_config.json"
    record = json.loads(path.read_text())
    change(record)
    path.write_text(json.dumps(record))


def start_import(window, app, folder):
    assert window.import_legacy_project(folder)
    _wait(app, lambda: window._import_thread is None)


def test_legacy_writer_roundtrip_and_native_conversion(package, project, tmp_path, monkeypatch):
    folder, source, masks = package
    window, app, _, _, warnings = project
    approved = []
    monkeypatch.setattr(window, "_confirm_legacy_import", lambda summary: approved.append(summary) or True)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in folder.iterdir()}
    _forbid_calculations(monkeypatch)
    start_import(window, app, folder)
    assert not warnings
    state = window.project_state
    assert "VERIFIED (saved SHA-256 matches)" in approved[0]
    assert state.original_dataset.source_path == source
    assert state.segmentation_is_current and state.filtering_is_current
    assert state.scientific_roi_reviewed and state.quantification_is_current
    session_id = state.nuclear_session.session_id
    assert session_id == "nuclear"
    assert state.nuclear_session.source_channel_index == state.segmentation_channel_index
    np.testing.assert_array_equal(window.segmentation.result_layer.data, masks)
    assert state.segmentation_result["manual_review"]["decisions"]["7"]["decision"] == "exclude"
    assert state.filtering_result["retained_ids"] == (2,)
    assert state.filtering_result["settings"]["intensity_filters"]["DAPI"]["column"] == "mean_intensity_DAPI"
    assert state.perinuclear_markers
    quant = state.quantification_result
    assert quant["total_cellpose_masks"] == 2 and quant["retained_masks"] == 1
    assert quant["measured_full_tissue_thickness_um"] == 24
    assert quant["nominal_section_thickness_um"] == 60
    assert quant["nominal_corrected_nuclei_per_100um_cube"] == pytest.approx(quant["retained_nuclei_per_100um_cube"] * .4)
    panel = window.context_panel.quantification_page
    assert "Measured density" in panel.historical_density.text()
    assert "Nominal-thickness-corrected density" in panel.historical_density.text()
    assert window.project_dirty and window.project_directory is None

    save(window, app, folder, save_as=True)
    assert warnings and "outside the legacy results directory" in warnings.pop()
    save(window, app, folder / "Nested", save_as=True)
    assert "outside the legacy results directory" in warnings.pop()
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in folder.iterdir()} == before
    converted = tmp_path / "Converted"
    save(window, app, converted, save_as=True)
    shutil.rmtree(folder)
    assert window.close_project()
    open_saved(window, app, converted)
    assert not warnings
    assert state.nuclear_session.session_id == session_id
    assert state.quantification_is_current and state.quantification_result == quant
    assert state.legacy_import["configuration"]["input"]["path_at_analysis"] == str(source)
    np.testing.assert_array_equal(state.segmentation_result["masks"], masks)


def test_relocate_then_reject_wrong_hash(package, project, tmp_path, monkeypatch):
    folder, source, _ = package
    window, app, _, _, warnings = project
    original = window.project_state.original_dataset
    moved = tmp_path / "Moved original.tif"
    source.rename(moved)
    other = tmp_path / "Other.tif"
    other.write_bytes(moved.read_bytes() + b"wrong")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(other), ""))
    start_import(window, app, folder)
    assert "SHA-256" in warnings.pop()
    assert window.project_state.original_dataset is original
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(moved), ""))
    monkeypatch.setattr(window, "_confirm_legacy_import", lambda summary: True)
    start_import(window, app, folder)
    assert not warnings and window.project_state.original_dataset.source_path == moved
    assert window.project_state.legacy_import["configuration"]["input"]["path_at_analysis"] == str(source)


def test_hashless_schema_seven_requires_acknowledgement_and_keeps_history(package, project, monkeypatch):
    folder, _, _ = package
    window, app, _, _, warnings = project

    def historical(config):
        config["schema_version"] = 7
        config["input"]["sha256"] = None
        config["filters"].pop("z_counting_rule")

    modify(folder, historical)
    original = window.project_state.original_dataset
    reviews = []
    monkeypatch.setattr(window, "_confirm_legacy_import", lambda summary: reviews.append(summary) or False)
    start_import(window, app, folder)
    assert "Cryptographic verification is unavailable" in reviews[-1]
    assert window.project_state.original_dataset is original
    monkeypatch.setattr(window, "_confirm_legacy_import", lambda summary: True)
    start_import(window, app, folder)
    assert not warnings and window.project_state.segmentation_is_current
    assert not window.project_state.filtering_is_current
    assert not window.project_state.scientific_roi_reviewed
    assert not window.project_state.quantification_is_current
    assert not window.project_state.legacy_import["source_verified"]
    assert window.project_state.original_dataset.source_sha256
    assert window.project_state.segmentation_result["manual_review"]["decisions"]["7"]["decision"] == "exclude"


def test_continuing_filters_preserves_manual_decisions_only_for_original_counting(package, project, monkeypatch):
    folder, _, _ = package
    window, app, _, _, warnings = project
    monkeypatch.setattr(window, "_confirm_legacy_import", lambda summary: True)
    start_import(window, app, folder)
    state = window.project_state
    assert not warnings and state.filtering_is_current
    review = window.context_panel.mask_review_page
    review.minimum_volume.editor.setValue(1)
    _wait(app, lambda: not review._filter_timer.isActive())
    assert state.filtering_result["properties"].loc[7, "manual_decision"] == "exclude"
    review.z_guard_mode.setCurrentText("both")
    _wait(app, lambda: not review._filter_timer.isActive())
    assert state.filtering_result["properties"].loc[7, "manual_decision"] == "automatic"
    assert state.segmentation_result["manual_review"]["decisions"]["7"]["decision"] == "exclude"


@pytest.mark.parametrize("defect", ["missing", "bad_ids", "missing_roi"])
def test_missing_or_incompatible_optional_data_stays_pending(package, defect, project, tmp_path, monkeypatch):
    folder, _, _ = package
    window, app, _, _, warnings = project
    if defect == "missing":
        (folder / "all_mask_properties.csv").unlink()
    elif defect == "bad_ids":
        path = folder / "all_mask_properties.csv"
        table = pd.read_csv(path)
        table.loc[0, "label"] = 999
        table.to_csv(path, index=False)
    else:
        (folder / "mntb_roi.tif").unlink()
    monkeypatch.setattr(window, "_confirm_legacy_import", lambda summary: True)
    _forbid_calculations(monkeypatch)
    start_import(window, app, folder)
    state = window.project_state
    assert not warnings and state.segmentation_is_current
    assert not state.filtering_is_current and not state.quantification_is_current
    if defect == "missing_roi":
        assert state.scientific_roi is None and window.filtering.roi_layer is None
    else:
        assert state.scientific_roi is not None and window.filtering.roi_layer is not None
    native = tmp_path / "Pending"
    save(window, app, native, save_as=True)
    open_saved(window, app, native)
    assert not warnings and state.segmentation_is_current
    assert not state.quantification_is_current


def test_reject_incomplete_unsupported_or_wrong_grid(package, project, monkeypatch):
    folder, _, _ = package
    window, app, _, _, warnings = project
    active = window.project_state.original_dataset
    monkeypatch.setattr(window, "_confirm_legacy_import", lambda summary: pytest.fail(summary))
    modify(folder, lambda c: c.update(schema_version=5))
    start_import(window, app, folder)
    assert "Unsupported legacy schema" in warnings.pop()
    modify(folder, lambda c: c.update(schema_version=8, mask_grid=dict(
        axes="ZYX", shape=(12, 32, 32), offset_yx_px=(1, 0), offset_zyx_um=(0, 1, 0), crop=None,
    )))
    start_import(window, app, folder)
    assert "working grid" in warnings.pop()
    (folder / "cellpose_raw_masks.tif").unlink()
    start_import(window, app, folder)
    assert "requires cellpose_raw_masks.tif" in warnings.pop()
    assert window.project_state.original_dataset is active


@pytest.mark.parametrize("crop", ["rectangle", "polygon"])
def test_cropped_desktop_package_with_recorded_z_correction(project, tmp_path, monkeypatch, crop):
    window, app, _, _, warnings = project
    state = window.project_state
    from nuclear_segmentation.projection_crop import polygon_roi_mask, selection_bounding_box

    vertices = ((1, 2), (1, 8), (7, 2)) if crop == "polygon" else ((1, 2), (1, 8), (7, 8), (7, 2))
    if crop == "polygon":
        bounds, inclusion = polygon_roi_mask(vertices, state.original_dataset.standardized_shape[-2:])
    else:
        bounds, inclusion = selection_bounding_box(vertices, state.original_dataset.standardized_shape[-2:]), None
    window._display_working_dataset(state.apply_crop(bounds, vertices, inclusion, crop))
    state.set_perinuclear_markers([])
    z_panel = window.context_panel.preprocessing_page.z_correction_panel
    assert window.z_correction.calculate({0: z_panel.options_for(0)}, 0, True)
    _wait(app, lambda: not window.z_correction.busy)
    assert window.z_correction.apply([0])
    preprocessing = window.context_panel.preprocessing_page
    preprocessing.normalization_mode_combo.setCurrentIndex(preprocessing.normalization_mode_combo.findData("percentile"))
    assert window.normalization.calculate(preprocessing.normalization_options())
    _wait(app, lambda: not window.normalization.busy)
    assert window.normalization.apply()
    configuration = window.context_panel.segmentation_page.scientific_config()
    configuration.update(INITIAL_MIN_VOLUME_UM3=0., INITIAL_MIN_SPHERICITY=0., NOMINAL_SECTION_THICKNESS_UM=72.)
    prepared = prepare_scientific_input(state, configuration)
    masks = np.zeros(state.working_dataset.standardized_shape[1:], dtype=np.uint16)
    masks[:, 1:3, 1:3] = 13
    state.set_segmentation_result(dict(
        masks=masks, mask_shape=masks.shape, maximum_label_id=13, object_count=1, scientific_config=configuration,
        input_provenance=prepared.provenance, segmentation_channel=state.segmentation_channel,
        model_name=configuration["MODEL_NAME"], device="cpu", device_type="cpu", z_corrections=configuration["Z_CORRECTIONS"],
    ))
    assert window.filtering.calculate()
    _wait(app, lambda: not window.filtering.busy)
    review = window.context_panel.mask_review_page
    review.intensity_master.setChecked(False)
    review.z_guard_mode.setCurrentText("first" if crop == "rectangle" else "none")
    _wait(app, lambda: not review._filter_timer.isActive())
    window.filtering.accept_roi()
    folder = tmp_path / "Export"
    namespace = window.image_export._scientific_namespace(folder)
    namespace["desktop_export"]["outputs"] = ("csv", "tiff")
    _write_scientific_package(namespace)

    _forbid_calculations(monkeypatch)
    monkeypatch.setattr(window, "_confirm_legacy_import", lambda summary: True)
    start_import(window, app, folder)
    assert not warnings
    assert state.segmentation_is_current and state.scientific_roi_reviewed and state.quantification_is_current
    assert state.working_offset_yx == (1, 2)
    assert state.crop_provenance.selection_type == crop
    assert state.z_correction_applied == {0}
    assert state.normalization_is_current
    assert state.normalization_configuration["cellpose_normalize"] == configuration["CELLPOSE_NORMALIZE"]
    assert not window.z_correction.preview_layers
    assert not state.perinuclear_markers
    assert not state.filtering_result["settings"]["intensity_filters"]
    assert state.quantification_result["nominal_section_thickness_um"] == 72.
    rule = state.quantification_result["counting_rule"]
    assert ("minimum object Z" in rule) if crop == "rectangle" else ("Centroid-based" in rule)
    np.testing.assert_array_equal(state.segmentation_result["masks"], masks)
    converted = tmp_path / "Cropped conversion"
    save(window, app, converted, save_as=True)
    open_saved(window, app, converted)
    assert not warnings
    assert state.segmentation_is_current and state.quantification_is_current
    assert state.z_correction_applied == {0}
