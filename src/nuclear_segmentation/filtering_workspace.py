"""Desktop adapter for the existing measurement and filtering contracts."""
from __future__ import annotations

from copy import deepcopy
import traceback

import numpy as np
from qtpy.QtCore import QObject, QThread, Signal, Slot

from .core import (
    calculate_mask_properties, calculate_perinuclear_marker_metrics, safe_key,
)
from .filtering import apply_filter_settings
from .scientific_input import prepare_scientific_input


def calculate_filtering_prerequisites(project_state, config):
    """Measure current raw masks with the same functions as legacy stages."""
    scientific = prepare_scientific_input(project_state, config)
    ns = scientific.namespace
    masks = np.asarray(project_state.segmentation_result["masks"])
    if masks.shape != ns["segmentation_volume"].shape:
        raise ValueError("Raw masks do not match the current working ZYX grid.")
    properties, background = calculate_mask_properties(
        masks, ns["channel_volumes"], ns["CHANNELS"], ns["VOXEL_SPACING_UM"],
        ns["GLOBAL_BACKGROUND_PERCENTILE"],
    )
    intensity_prefix = (
        "mean_intensity_" if ns["INTENSITY_MODE"] == "raw"
        else "background_corrected_mean_"
    )
    intensity_definitions = []
    for item in ns["CHANNELS"]:
        if not item.get("measure", True):
            continue
        column = intensity_prefix + safe_key(item["name"])
        low = float(np.floor(properties[column].min()))
        high = float(np.ceil(properties[column].max()))
        if high <= low:
            high = low + 1.0
        intensity_definitions.append({
            "index": int(item["index"]), "name": str(item["name"]),
            "column": column, "minimum": low, "maximum": high,
            "enabled": bool(item.get("filter_enabled", False)),
        })

    perinuclear_definitions = []
    ring_labels = {}
    for marker in ns["PERINUCLEAR_MARKERS"]:
        if not marker.get("enabled", True):
            continue
        name = str(marker["name"])
        if name not in ns["channel_volumes"]:
            continue
        metrics, rings, threshold, source = calculate_perinuclear_marker_metrics(
            masks, ns["channel_volumes"][name], ns["VOXEL_SPACING_UM"], name,
            marker.get("ring_inner_um", .5), marker.get("ring_outer_um", 3.0),
            marker.get("pixel_threshold"),
        )
        properties = properties.join(metrics, how="left", validate="one_to_one")
        column = f"{safe_key(name).lower()}_ring_positive_fraction"
        properties[column] = properties[column].fillna(0.0)
        ring_labels[name] = rings
        perinuclear_definitions.append({
            "name": name,
            "channel_index": int(next(item["index"] for item in ns["CHANNELS"] if item["name"] == name)),
            "column": column,
            "minimum_positive_fraction": float(marker.get("initial_min_positive_fraction", 0)),
            "resolved_pixel_threshold": float(threshold),
            "threshold_source": source,
        })

    voxel_volume = float(np.prod(ns["VOXEL_SPACING_UM"]))
    minimum_volume = ns["INITIAL_MIN_VOLUME_UM3"]
    settings = {
        "minimum_volume_um3": float(500 * voxel_volume if minimum_volume is None else minimum_volume),
        "minimum_sphericity": float(ns["INITIAL_MIN_SPHERICITY"]),
        "first_tissue_z": int(ns["INITIAL_FIRST_TISSUE_Z"]),
        "last_tissue_z": int(masks.shape[0] - 1 if ns["INITIAL_LAST_TISSUE_Z"] is None else ns["INITIAL_LAST_TISSUE_Z"]),
        "z_guard_um": float(ns["INITIAL_Z_GUARD_UM"]),
        "z_guard_mode": str(ns["INITIAL_Z_GUARD_MODE"]),
        "z_counting_rule": "directional_entry_v1",
        "perinuclear_combination": str(ns["PERINUCLEAR_COMBINATION"]),
        "perinuclear_filters": {item["name"]: {"column": item["column"], "minimum_positive_fraction": item["minimum_positive_fraction"]} for item in perinuclear_definitions},
        "intensity_combination": str(ns["FILTER_COMBINATION"]),
        "intensity_filtering_enabled": any(
            item["enabled"] for item in intensity_definitions
        ),
        "intensity_filters": {item["name"]: {"column": item["column"], "minimum": item["minimum"], "maximum": item["maximum"]} for item in intensity_definitions if item["enabled"]},
    }
    filtered_properties, keep = apply_filter_settings(
        properties, masks, ns["mntb_roi"], settings, ns["Z_SPACING_UM"]
    )
    retained = filtered_properties.index[keep].to_numpy(dtype=int)
    filtered_masks = np.where(np.isin(masks, retained), masks, 0)
    return {
        "properties": filtered_properties, "background_table": background,
        "settings": settings, "retained_ids": tuple(int(v) for v in retained),
        "retained_count": int(len(retained)), "filtered_masks": filtered_masks,
        "raw_mask_shape": tuple(masks.shape), "scientific_roi": np.array(ns["mntb_roi"], copy=True),
        "roi_reviewed": False, "intensity_definitions": intensity_definitions,
        "perinuclear_definitions": perinuclear_definitions, "ring_labels": ring_labels,
        "input_segmentation_provenance": deepcopy(project_state.segmentation_result.get("input_provenance", {})),
        "measurement_provenance": deepcopy(scientific.provenance),
    }


class FilteringWorker(QObject):
    succeeded = Signal(object); failed = Signal(str); finished = Signal()
    def __init__(self, state, config):
        super().__init__(); self.state = state; self.config = deepcopy(config)
    @Slot()
    def run(self):
        try: self.succeeded.emit(calculate_filtering_prerequisites(self.state, self.config))
        except Exception: self.failed.emit(traceback.format_exc())
        finally: self.finished.emit()


class FilteringController(QObject):
    status_changed = Signal(str)
    def __init__(self, viewer, state, panel, config_provider, parent=None,
                 conflict_operations=()):
        super().__init__(parent); self.viewer = viewer; self.state = state; self.panel = panel
        self.config_provider = config_provider; self.conflict_operations = tuple(conflict_operations); self._thread = None; self._worker = None
        self.filtered_layer = None; self.roi_layer = None; self._roi_callback = None
        self._roi_transform = None
        self._pending_filter_settings = None
        panel.calculate_measurements_requested.connect(self.calculate)
        panel.filters_changed.connect(self.apply_filters)
        panel.accept_roi_requested.connect(self.accept_roi)
        panel.marker_configuration_changed.connect(self.update_marker_configuration)
        state.filtering_result_changed.connect(self._state_changed)
    @property
    def busy(self): return self._thread is not None
    def calculate(self):
        if self.busy or any(item.busy for item in self.conflict_operations) or not self.state.segmentation_is_current: return False
        try: config = self._measurement_config()
        except Exception as error: self.status_changed.emit(f"Measurements unavailable: {error}"); return False
        self._pending_filter_settings = (
            deepcopy(self.panel.filter_settings())
            if self.state.filtering_result is not None else None
        )
        worker = FilteringWorker(self.state, config); thread = QThread(self); worker.moveToThread(thread)
        thread.started.connect(worker.run); worker.succeeded.connect(self._calculated); worker.failed.connect(self._failed)
        worker.finished.connect(thread.quit); worker.finished.connect(worker.deleteLater); thread.finished.connect(self._finished); thread.finished.connect(thread.deleteLater)
        self._worker, self._thread = worker, thread; self.panel.calculate_button.setEnabled(False)
        self.panel.measurement_status.setText("Calculating existing scientific measurements…")
        thread.start(); return True
    def _measurement_config(self):
        result = self.state.segmentation_result or {}
        executed = result.get("scientific_config")
        config = deepcopy(executed) if executed is not None else self.config_provider()
        config["PERINUCLEAR_MARKERS"] = self.state.legacy_perinuclear_markers()
        return config
    def _calculated(self, result):
        if self._pending_filter_settings is not None:
            result = self._result_with_settings(
                result, self._pending_filter_settings, result["scientific_roi"]
            )
        self.state.set_filtering_result(result); self._create_layers(result)
        self.panel.configure_filtering(result, True); self.status_changed.emit("Scientific measurements and preliminary filters are ready.")
    def _failed(self, text):
        message = str(text).strip().splitlines()[-1]; self.panel.measurement_status.setText("Measurement failed: " + message); self.status_changed.emit("Measurement failed: " + message)
    def _finished(self):
        self._thread = None; self._worker = None; self._pending_filter_settings = None; self.panel.calculate_button.setEnabled(self.state.segmentation_is_current)
    def _create_layers(self, result):
        spacing = tuple(self.state.voxel_spacing_zyx_um); oy, ox = self.state.working_offset_yx; translate = (0, oy * spacing[1], ox * spacing[2])
        self.clear_layers()
        self.filtered_layer = self.viewer.add_labels(result["filtered_masks"], name="Live filtered masks", scale=spacing, translate=translate,
            metadata={"mask_type": "nuclear", "nucytoseg_filtered_segmentation": True})
        self.filtered_layer.editable = False
        self.roi_layer = self.viewer.add_labels(result["scientific_roi"].astype(np.uint8), name="MNTB ROI — provisional review", scale=spacing, translate=translate,
            opacity=.35, visible=False, metadata={"nucytoseg_scientific_roi": True})
        self.roi_layer.editable = True
        self._roi_transform = (np.asarray(self.roi_layer.scale, dtype=float).copy(),
                               np.asarray(self.roi_layer.translate, dtype=float).copy())
        self._roi_callback = lambda event=None: self.invalidate_roi()
        self.roi_layer.events.data.connect(self._roi_callback)
    def apply_filters(self, *args):
        current = self.state.filtering_result
        if current is None or not self.state.filtering_is_current or self.busy: return
        try:
            settings = self.panel.filter_settings(); roi = np.asarray(self.roi_layer.data) > 0 if self.roi_layer is not None else current["scientific_roi"]
            updated = self._result_with_settings(current, settings, roi)
            self.state.set_filtering_result(updated)
            if self.filtered_layer is not None: self.filtered_layer.data = updated["filtered_masks"]
            self.panel.update_filter_summary(updated, True); self.panel.update_selected_properties(self.panel._selected_id)
        except ValueError as error: self.panel.filter_summary.setText(f"Invalid filter settings: {error}")
    def _result_with_settings(self, current, settings, roi):
        settings = deepcopy(settings)
        columns = set(current["properties"].columns)
        settings["intensity_filters"] = {
            name: item for name, item in settings["intensity_filters"].items()
            if item["column"] in columns
        }
        settings["perinuclear_filters"] = {
            name: item for name, item in settings["perinuclear_filters"].items()
            if item["column"] in columns
        }
        masks = self.state.segmentation_result["masks"]
        properties, keep = apply_filter_settings(
            current["properties"], masks, roi, settings,
            self.state.voxel_spacing_zyx_um[0],
        )
        retained = properties.index[keep].to_numpy(dtype=int)
        return dict(
            current, properties=properties, settings=settings,
            retained_ids=tuple(int(value) for value in retained),
            retained_count=len(retained),
            filtered_masks=np.where(np.isin(masks, retained), masks, 0),
        )
    def accept_roi(self):
        if self.roi_layer is None or self.state.filtering_result is None: return
        expected_scale, expected_translate = self._roi_transform
        if (not np.allclose(self.roi_layer.scale, expected_scale)
                or not np.allclose(self.roi_layer.translate, expected_translate)
                or not np.allclose(self.roi_layer.rotate, np.eye(3))
                or not np.allclose(self.roi_layer.shear, 0)
                or not np.allclose(self.roi_layer.affine.affine_matrix, np.eye(4))):
            self.panel.roi_status.setText(
                "ROI transform changed — restore it and edit voxels with the brush."
            )
            return
        roi = np.asarray(self.roi_layer.data) > 0
        if roi.shape != self.state.segmentation_result["masks"].shape or not roi.any():
            self.panel.roi_status.setText("Invalid or empty scientific ROI."); return
        updated = dict(self.state.filtering_result, scientific_roi=np.array(roi, copy=True), roi_reviewed=True)
        self.state.set_filtering_result(updated); self.roi_layer.name = "MNTB ROI — reviewed"; self.panel.roi_status.setText("Reviewed and accepted."); self.apply_filters()
    def invalidate_roi(self):
        if self.state.filtering_result is None or not self.state.filtering_result.get("roi_reviewed"): return
        updated = dict(self.state.filtering_result, roi_reviewed=False)
        self.state.set_filtering_result(updated)
        self.panel.roi_status.setText("ROI changed — inspect and accept again.")
        self.panel.update_filter_summary(updated, True)
    def _state_changed(self, result, current):
        if result is None: self.clear_layers(); self.panel.configure_filtering(None, False)
        elif not current:
            if self.filtered_layer is not None: self.filtered_layer.name = "Live filtered masks — outdated"
            if self.roi_layer is not None: self.roi_layer.name = "MNTB ROI — outdated"
            self.panel.configure_filtering(result, False)
    def update_marker_configuration(self, records):
        try:
            measurement_changed = self.state.set_perinuclear_markers(records)
        except ValueError as error:
            self.panel.measurement_status.setText(str(error))
            return
        if measurement_changed:
            self.panel.set_perinuclear_filter_defaults(records)
            message = "Perinuclear marker measurement configuration changed — recalculate measurements."
        else:
            self.panel.set_perinuclear_filter_defaults(records)
            message = "Perinuclear positive-fraction filters updated from cached measurements."
        self.panel.measurement_status.setText(message)
        self.status_changed.emit(message)
    def clear_layers(self):
        if self.roi_layer is not None and self._roi_callback is not None:
            try: self.roi_layer.events.data.disconnect(self._roi_callback)
            except (TypeError, ValueError): pass
        for layer in (self.filtered_layer, self.roi_layer):
            if layer is not None and layer in self.viewer.layers: self.viewer.layers.remove(layer)
        self.filtered_layer = self.roi_layer = self._roi_callback = self._roi_transform = None
    def clear_project(self): self.clear_layers()
    def dispose(self):
        self.clear_layers(); self.panel.calculate_measurements_requested.disconnect(self.calculate); self.panel.filters_changed.disconnect(self.apply_filters); self.panel.accept_roi_requested.disconnect(self.accept_roi); self.panel.marker_configuration_changed.disconnect(self.update_marker_configuration); self.state.filtering_result_changed.disconnect(self._state_changed)
