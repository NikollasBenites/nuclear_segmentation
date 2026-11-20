"""Desktop adapters for the validated image and scientific exporters."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from functools import partial
import json
from pathlib import Path
from shutil import rmtree

import numpy as np
import pandas as pd

from qtpy.QtCore import QObject, QThread, Signal, Slot
from qtpy.QtWidgets import QFileDialog

from .config import validate_config
from .core import calibrated_label_tiff, safe_filename, safe_key, software_versions
from .filtering import calculate_perinuclear_filter_keep
from .image_export import export_images
from .manual_review import ManualReview
from .normalization import mask_digest
from .runtime import execute_stage
from .stages.export import SCIENTIFIC_OUTPUTS, _choose_export_destination, _export_folder_error


def _write_scientific_package(namespace):
    masks = namespace["masks"]
    export = namespace["desktop_export"]
    destination = export["destination"]
    error = _export_folder_error(destination.parent, destination.name)
    if error:
        raise ValueError(error)
    if "tiff" in export["outputs"]:
        # The ImageJ encoder used for this package requires uint16 label IDs.
        if masks.dtype.kind not in "iu" or masks.min() < 0 or masks.max() > np.iinfo(np.uint16).max:
            raise ValueError("Scientific TIFF export requires integer label IDs from 0 to 65,535.")
        names = [safe_filename(name).casefold() for name in export["settings"]["perinuclear_filters"]]
        if len(names) != len(set(names)) or "perinuclear_marker" in names:
            raise ValueError("Marker names would produce conflicting TIFF filenames.")

    properties = namespace["filter_properties"]
    saved = namespace.pop("saved_manual_review", None)
    review = ManualReview(masks, properties.index, saved)
    # Export only decisions that are active in the current counting result.
    if saved and "manual_decision" in properties:
        review.decisions = {label: item for label, item in review.decisions.items()
                            if properties.loc[label, "manual_decision"] == item["decision"]}
    namespace["manual_review"] = review
    try:
        execute_stage(namespace, "export")
    except Exception:
        if namespace.get("export_directory_created"):
            rmtree(destination)
        raise
    return destination


class ImageExportWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, write):
        super().__init__()
        self.write = write

    @Slot()
    def run(self):
        try:
            saved = self.write()
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(saved)
        finally:
            self.finished.emit()


class ImageExportController(QObject):
    status_changed = Signal(str)

    def __init__(
        self,
        project_state,
        preparation_controller,
        panel,
        preprocessing_page,
        parent=None,
    ):
        super().__init__(parent)
        self.project_state = project_state
        self.preparation = preparation_controller
        self.panel = panel
        self.preprocessing_page = preprocessing_page
        self._thread = None
        self._worker = None
        self._pending = None
        self.filtering = None
        panel.export_requested.connect(self.choose_path)
        preparation_controller.prepared_changed.connect(self.refresh_channels)
        project_state.channels_changed.connect(self.refresh_channels)
        self.refresh_channels()

    def bind_filtering(self, filtering) -> None:
        self.filtering = filtering
        self.panel.scientific_button.clicked.connect(self.choose_scientific_directory)
        self.panel.scientific_options_changed.connect(self.refresh_scientific)
        self.project_state.quantification_result_changed.connect(self.refresh_scientific)
        filtering.panel.filters_edited.connect(self.refresh_scientific)
        filtering.status_changed.connect(self.refresh_scientific)
        filtering.viewer.layers.events.removed.connect(self.refresh_scientific)
        self.refresh_scientific()

    def _scientific_unavailable(self):
        state = self.project_state
        if self.filtering is None:
            return "Scientific filtering is unavailable."
        if self.busy:
            return "An export is running."
        if not self.panel.selected_scientific_outputs():
            return "Select at least one scientific export output."
        if not state.segmentation_is_current or not state.filtering_is_current:
            return "Complete a current segmentation and calculate scientific measurements."
        if state.active_session.scientific_role != "nuclear":
            return "Scientific density export is available for nuclear sessions."
        if not state.scientific_roi_reviewed:
            return "Review and accept the scientific ROI."
        result = state.quantification_result or {}
        spacing = state.voxel_spacing_zyx_um
        if (
            not state.quantification_is_current or result.get("density_status") != "valid"
            or not spacing or tuple(spacing) != result["voxel_spacing_zyx_um"]
        ):
            return "Current calibrated quantification with a positive sampling volume is required."
        if self.filtering.panel.filter_settings() != state.filtering_result["settings"]:
            return "Apply valid filter and counting settings before exporting."
        try:
            self.filtering.validate_roi_layer()
        except ValueError as error:
            return str(error)
        return ""

    def refresh_scientific(self, *args) -> None:
        if self.filtering is None:
            return
        reason = self._scientific_unavailable()
        self.panel.scientific_button.setEnabled(not reason)
        self.panel.scientific_button.setToolTip(reason)

    def choose_scientific_directory(self) -> bool:
        try:
            reason = self._scientific_unavailable()
            if reason:
                raise ValueError(reason)
            state = self.project_state
            current = state.quantification_result
            source = state.original_dataset
            sample = current["executed_segmentation_config"].get("CUSTOM_SAMPLE_NAME")
            destination = _choose_export_destination(
                self.panel, safe_filename(sample or source.source_path.stem)
            )
            if destination is None:
                return False
            if not state.quantification_is_current or state.quantification_result is not current:
                raise ValueError("Scientific results changed while choosing the folder. Review and retry.")
            return self.export_scientific(destination)
        except (ValueError, TypeError, KeyError) as error:
            self._scientific_status(f"Scientific export unavailable: {error}")
            return False

    def export_scientific(self, destination) -> bool:
        try:
            namespace = self._scientific_namespace(Path(destination))
        except (ValueError, TypeError, KeyError) as error:
            self._scientific_status(f"Scientific export unavailable: {error}")
            return False
        self._start(
            ImageExportWorker(partial(_write_scientific_package, namespace)),
            {"scientific": True, "outputs": namespace["desktop_export"]["outputs"]},
        )
        self.panel.set_scientific_busy(True)
        self._scientific_status("Writing scientific results…")
        return True

    def _scientific_namespace(self, destination):
        """Snapshot current desktop data for the existing stage."""
        reason = self._scientific_unavailable()
        if reason:
            raise ValueError(reason)
        if self.filtering.busy:
            raise ValueError("Wait for active scientific measurements to finish.")
        state = self.project_state
        source = state.original_dataset
        working = state.working_dataset
        segmentation = state.segmentation_result
        filtering = state.filtering_result
        quantification = state.quantification_result
        config = validate_config(segmentation["scientific_config"])
        masks = segmentation["masks"]
        roi = state.scientific_roi
        spacing = tuple(state.voxel_spacing_zyx_um)
        properties = filtering["properties"]
        retained_ids = filtering["retained_ids"]
        settings = deepcopy(filtering["settings"])
        if (
            working.standardized_shape[1:] != masks.shape
            or filtering["filtered_masks"].shape != masks.shape
            or filtering.get("input_segmentation_provenance", {}) != segmentation.get("input_provenance", {})
        ):
            raise ValueError("The source grid, segmentation, and measurements are incompatible.")
        if (
            roi is None or roi.shape != masks.shape
            or state.scientific_roi_provenance != self.filtering._roi_provenance(filtering)
            or mask_digest(roi) != quantification["scientific_roi_sha256"]
            or not np.array_equal(roi, filtering["scientific_roi"])
            or not np.array_equal(roi, np.asarray(self.filtering.roi_layer.data) > 0)
        ):
            raise ValueError("The reviewed scientific ROI and measured ROI are incompatible.")
        if settings != quantification["filter_settings"] or retained_ids != quantification["retained_mask_ids"]:
            raise ValueError("Filtering and quantification results do not match.")
        keep = properties.index.to_series().isin(retained_ids)
        if not properties.index.is_unique or int(keep.sum()) != len(retained_ids):
            raise ValueError("The retained mask IDs do not match the measurement table.")

        executed = deepcopy(segmentation.get("parameters", {}))
        normalize = executed.get("normalize", config["CELLPOSE_NORMALIZE"])
        normalization = state.normalization_configuration or {}
        z_corrections = deepcopy(segmentation.get("z_corrections", config["Z_CORRECTIONS"]))
        z_reports = {}
        for channel in config["CHANNELS"]:
            index, name = channel["index"], channel["name"]
            report = state.z_correction_reports.get(index, {})
            if (
                name in z_corrections and index in state.z_correction_applied
                and report.get("options") == z_corrections[name]
            ):
                z_reports[name] = {
                    key: deepcopy(report[key])
                    for key in ("options", "observed_profile", "smoothed_profile", "gain",
                                "target", "positive_pixel_counts")
                }
        offset_y, offset_x = state.working_offset_yx
        crop = state.crop_provenance
        density = deepcopy(quantification["density"])
        device_type = segmentation.get("device_type")
        desktop_export = {
            "destination": destination,
            "outputs": self.panel.selected_scientific_outputs(),
            "settings": settings,
            "density": density,
            "exclusions": {
                label: quantification[key] for label, key in (
                    ("Morphology", "failed_morphology"),
                    ("Nuclear intensity", "failed_intensity"),
                    ("Perinuclear association", "failed_perinuclear"),
                    ("Physical Z guard", "excluded_by_z_guard"),
                    ("ROI / counting Z", "excluded_by_sampling_roi"),
                )
            },
            "effective_planes": quantification["effective_sampling_planes"],
            "device": segmentation.get("device", "not recorded"),
            "used_gpu": device_type != "cpu" if device_type is not None else None,
            "executed_parameters": executed,
            "roi_provenance": {
                "sha256": quantification["scientific_roi_sha256"],
                "provenance": deepcopy(state.scientific_roi_provenance),
            },
            "provenance": {
                "nominal_section_thickness_source": quantification["nominal_section_thickness_source"],
                "counting_rule": density["counting_rule"],
                "density_interpretation": density["interpretation"],
                "segmentation_input": deepcopy(segmentation.get("input_provenance", {})),
                "measurement_input": deepcopy(filtering.get("measurement_provenance", {})),
                "applied_normalization": (
                    deepcopy(normalization)
                    if normalization.get("cellpose_normalize") == normalize
                    else {"cellpose_normalize": normalize}
                ),
                "mask_grid": {
                    "axes": "ZYX",
                    "shape": masks.shape,
                    "offset_zyx_um": (0, offset_y * spacing[1], offset_x * spacing[2]),
                    "offset_yx_px": (offset_y, offset_x),
                    "crop": None if crop is None else asdict(crop),
                },
            },
        }
        return dict(
            config,
            RUN_MODE="segment",
            MODEL_NAME=segmentation.get("model_name", config["MODEL_NAME"]),
            CELLPOSE_NORMALIZE=normalize,
            SEGMENTATION_CHANNEL=segmentation["segmentation_channel"],
            CELLPOSE_MODEL_LOAD_SECONDS=segmentation.get("model_load_seconds"),
            CELLPOSE_EVAL_SECONDS=segmentation.get("evaluation_seconds"),
            CALIBRATION_SOURCE=state.calibration_source,
            VOXEL_SPACING_UM=spacing,
            ANISOTROPY=executed.get("anisotropy", spacing[0] / np.mean(spacing[1:])),
            NOMINAL_SECTION_THICKNESS_UM=density["nominal_section_thickness_um"],
            DEFAULT_INPUT=source.source_path,
            INPUT_SHA256=source.source_sha256,
            image_information={
                "source_format": source.source_format,
                "series_index": source.series_index,
                "series_identifier": source.series_identifier,
                "series_name": source.series_name,
                "time_index": source.time_index,
                "axes_override": source.axes_override,
                "original_channel_names": [channel.original_name for channel in source.channels],
                "reported_axes": source.reported_axes,
                "reported_shape": source.reported_shape,
                "axes_interpreted_before_reorder": source.acquisition_metadata.get(
                    "axes_interpreted_before_reorder", source.reported_axes,
                ),
                "standardized_axes": "CZYX",
                "standardized_shape": source.standardized_shape,
                "dtype": source.dtype,
            },
            OME_ZARR_CACHE_INFO={"storage_backend": "in-memory desktop dataset", "enabled": False},
            Z_CORRECTIONS=z_corrections,
            z_correction_reports=z_reports,
            EXPORT_CORRECTED_IMAGES=False,
            PERINUCLEAR_MARKER_RESULTS={
                item["name"]: dict(item, fraction_column=item["column"])
                for item in filtering["perinuclear_definitions"]
                if item["name"] in settings["perinuclear_filters"]
            },
            desktop_export=desktop_export,
            roi_reviewed=True,
            roi_source=filtering["roi_source"],
            roi_plane_counts=np.asarray(quantification["roi_plane_counts"]),
            mntb_roi=roi,
            masks=masks,
            filtered_masks=filtering["filtered_masks"],
            filter_properties=properties,
            saved_manual_review=segmentation.get("manual_review"),
            current_keep=keep,
            background_table=filtering["background_table"],
            calculate_perinuclear_filter_keep=partial(calculate_perinuclear_filter_keep, properties),
            calibrated_label_tiff=calibrated_label_tiff,
            safe_filename=safe_filename,
            safe_key=safe_key,
            software_versions=software_versions,
            np=np,
            pd=pd,
            json=json,
            print=lambda *args: None,
        )

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def refresh_channels(self, *args) -> None:
        self.panel.set_prepared_channels(
            self.project_state.channels,
            self.preparation.provenance if self.preparation.is_current() else {},
        )

    def choose_path(self, channel_order, file_format: str, pixel_type: str) -> bool:
        source = self.project_state.original_dataset
        if source is None:
            self._status("Load and prepare an image before exporting.")
            return False
        stem = source.source_filename
        for suffix in (".ome.tiff", ".ome.tif", ".tiff", ".tif"):
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        suffix = ".ome.tif" if file_format == "ome" else ".tif"
        suggested = source.source_path.parent / f"{stem}_prepared_{pixel_type}{suffix}"
        file_filter = (
            "OME-TIFF (*.ome.tif *.ome.tiff)"
            if file_format == "ome"
            else "ImageJ TIFF (*.tif *.tiff)"
        )
        path, _ = QFileDialog.getSaveFileName(
            self.panel,
            "Export prepared multichannel TIFF",
            str(suggested),
            file_filter,
        )
        if not path:
            return False
        return self.export(path, channel_order, file_format, pixel_type)

    def export(self, path, channel_order, file_format="ome", pixel_type="uint16") -> bool:
        if self.busy:
            self._status("A TIFF export is already running.")
            return False
        if file_format not in {"ome", "imagej"} or pixel_type not in {"uint16", "float32"}:
            self._status("Select a supported TIFF format and pixel type.")
            return False
        if not self.preparation.is_current():
            self._status("Prepared channels are missing or outdated. Generate previews again.")
            return False
        order = [int(index) for index in channel_order]
        if not order or len(order) != len(set(order)):
            self._status("Select one or more unique prepared channels.")
            return False
        if any(index not in self.preparation.prepared_channels for index in order):
            self._status("The selected channel order contains unavailable prepared data.")
            return False
        source = self.project_state.original_dataset
        if source is None:
            self._status("The source project is no longer available.")
            return False
        by_index = {channel.index: channel for channel in self.project_state.channels}
        arrays = [self.preparation.prepared_channels[index] for index in order]
        names = [by_index[index].name for index in order]
        requested_at = datetime.now(timezone.utc).isoformat()
        provenance = []
        for position, index in enumerate(order):
            record = deepcopy(self.preparation.provenance[index])
            record.update(
                export_channel_position=position,
                export_requested_at_utc=requested_at,
                export_pixel_type=pixel_type,
                export_format=file_format,
            )
            provenance.append(record)
        protected = [source.source_path]
        worker = ImageExportWorker(partial(
            export_images,
            Path(path),
            arrays,
            names,
            tuple(self.project_state.voxel_spacing_zyx_um),
            provenance,
            protected,
            file_format=file_format,
            pixel_type=pixel_type,
        ))
        self._start(worker, {
            "names": names,
            "format": file_format,
            "pixel_type": pixel_type,
        })
        self._status(
            f"Writing {len(order)} prepared channel(s) as {pixel_type} "
            f"{'OME-TIFF' if file_format == 'ome' else 'ImageJ TIFF'}…"
        )
        return True

    def _start(self, worker, details) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._succeeded)
        worker.failed.connect(self._failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._worker = worker
        self._thread = thread
        self._pending = details
        self.panel.set_busy(True)
        self.refresh_scientific()
        self.preprocessing_page.setEnabled(False)
        thread.start()

    def _succeeded(self, path) -> None:
        details = self._pending or {}
        if details.get("scientific"):
            components = ", ".join(SCIENTIFIC_OUTPUTS[key] for key in details["outputs"])
            self.panel.set_results(
                f"Destination: {path}\nComponents: {components}, scientific provenance JSON",
                scientific_folder=path,
            )
            self._scientific_status("Scientific export completed.")
            return
        names = details.get("names", [])
        self.panel.set_results(
            "\n".join(
                (
                    f"Destination: {path}",
                    "Components: multichannel image TIFF and provenance JSON",
                    f"Format: {details.get('format')}",
                    f"Pixel type: {details.get('pixel_type')}",
                    f"Channel order: {', '.join(names)}",
                    f"Provenance: {path}.json",
                    "Display settings applied: no",
                )
            )
        )
        self._status(f"TIFF export completed: {path}")

    def _failed(self, message: str) -> None:
        if self._pending.get("scientific"):
            self._scientific_status(f"Scientific export failed: {message}")
        else:
            self._status(f"TIFF export failed: {message}")

    def _finished(self) -> None:
        completion_status = self.panel.status.text()
        self._thread = None
        self._worker = None
        self._pending = None
        self.panel.set_busy(False)
        self.panel.set_scientific_busy(False)
        self.preprocessing_page.setEnabled(True)
        self.refresh_channels()
        self.refresh_scientific()
        self.panel.set_status(completion_status)

    def clear_project(self) -> None:
        self.panel.reset()

    def _status(self, message: str) -> None:
        self.panel.set_status(message)
        self.status_changed.emit(str(message))

    def _scientific_status(self, message: str) -> None:
        concise = message if len(message) <= 135 else message[:132].rsplit(" ", 1)[0] + "…"
        self.panel.scientific_status.setText(concise)
        self.panel.scientific_status.setToolTip(message if concise != message else "")
        self.panel.scientific_status.show()
        self.status_changed.emit(concise)

    def dispose(self) -> None:
        if self.filtering is not None:
            self.panel.scientific_button.clicked.disconnect(self.choose_scientific_directory)
            self.panel.scientific_options_changed.disconnect(self.refresh_scientific)
            self.project_state.quantification_result_changed.disconnect(self.refresh_scientific)
            self.filtering.panel.filters_edited.disconnect(self.refresh_scientific)
            self.filtering.status_changed.disconnect(self.refresh_scientific)
            self.filtering.viewer.layers.events.removed.disconnect(self.refresh_scientific)
        self.panel.export_requested.disconnect(self.choose_path)
        self.preparation.prepared_changed.disconnect(self.refresh_channels)
        self.project_state.channels_changed.disconnect(self.refresh_channels)
