"""Read-only desktop integration for the authoritative density calculation."""
from __future__ import annotations

from copy import deepcopy

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget

from .collapsible_section import CollapsibleSection
from .core import calculate_density_summary, density_z_planes, format_density_panel
from .normalization import mask_digest


def calculate_quantification_result(project_state):
    """Assemble current desktop state for the existing density function."""
    segmentation = project_state.segmentation_result
    filtering = project_state.filtering_result
    if segmentation is None or not project_state.segmentation_is_current:
        raise ValueError("A compatible current segmentation is required.")
    if filtering is None or not project_state.filtering_is_current:
        raise ValueError("Scientific measurements require recalculation.")

    spacing = tuple(float(value) for value in project_state.voxel_spacing_zyx_um)
    roi = (
        project_state.scientific_roi
        if project_state.scientific_roi is not None
        else filtering.get("scientific_roi")
    )
    if roi is None:
        raise ValueError("A scientific MNTB ROI is required.")
    roi = np.asarray(roi, dtype=bool)
    mask_shape = tuple(segmentation["masks"].shape)
    if roi.shape != mask_shape:
        raise ValueError("The scientific ROI does not match the segmentation grid.")
    if project_state.scientific_roi_reviewed:
        if project_state.scientific_roi_provenance is None:
            raise ValueError("Reviewed scientific ROI provenance is unavailable.")
        measured_roi = filtering.get("scientific_roi")
        if (
            measured_roi is None
            or np.shape(measured_roi) != roi.shape
            or mask_digest(measured_roi) != mask_digest(roi)
        ):
            raise ValueError(
                "Reviewed scientific ROI and current filtering geometry differ."
            )

    settings = deepcopy(filtering["settings"])
    properties = filtering["properties"]
    retained_ids = tuple(int(value) for value in filtering["retained_ids"])
    plane_counts = np.count_nonzero(roi, axis=(1, 2))
    density = calculate_density_summary(
        plane_counts,
        settings,
        spacing,
        len(retained_ids),
        project_state.scientific_roi_reviewed,
    )
    _, effective_planes = density_z_planes(settings, roi.shape[0], spacing[0])

    reasons = properties["automatic_failure_reasons"].fillna("").astype(str)
    reason_sets = reasons.map(
        lambda text: {item.strip() for item in text.split(";") if item.strip()}
    )
    total = int(segmentation["object_count"])
    retained = len(retained_ids)
    result = {
        **density,
        "total_cellpose_masks": total,
        "retained_masks": retained,
        "excluded_masks": max(0, total - retained),
        "excluded_by_z_guard": int(
            properties["excluded_by_z_guard"].astype(bool).sum()
        ),
        "excluded_by_sampling_roi": int(
            properties["excluded_by_sampling_roi"].astype(bool).sum()
        ),
        "failed_morphology": int(
            reason_sets.map(lambda items: bool({"volume", "sphericity"} & items)).sum()
        ),
        "failed_intensity": int(
            reason_sets.map(lambda items: "intensity" in items).sum()
        ),
        "failed_perinuclear": int(
            reason_sets.map(lambda items: "shell association" in items).sum()
        ),
        "eligible_sampling_planes": int(effective_planes.sum()),
        "first_tissue_z": int(settings["first_tissue_z"]),
        "last_tissue_z": int(settings["last_tissue_z"]),
        "z_guard_um": float(settings["z_guard_um"]),
        "z_guard_mode": str(settings["z_guard_mode"]),
        "source_identity": deepcopy(filtering.get("measurement_provenance", {})),
        "executed_segmentation_config": deepcopy(
            segmentation.get("scientific_config", {})
        ),
        "segmentation_channel": segmentation.get("segmentation_channel"),
        "voxel_spacing_zyx_um": spacing,
        "scientific_roi_sha256": mask_digest(roi),
        "scientific_roi_provenance": deepcopy(
            project_state.scientific_roi_provenance
        ),
        "filter_settings": settings,
        "retained_mask_ids": retained_ids,
        "legacy_density_panel_text": format_density_panel(density),
    }
    return result


class QuantificationPanel(QWidget):
    """Display current counts, ROI volumes, and descriptive nuclear density."""

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        self.status = self._label("Quantification unavailable: calculate measurements.")
        root.addWidget(self.status)

        self.summary_values = self._section(root, "quantification_summary", "Quantification Summary", (
            ("total", "Total Cellpose masks"),
            ("retained", "Retained masks"),
            ("excluded", "Excluded masks"),
        ))
        self.roi_values = self._section(root, "quantification_roi", "Scientific ROI", (
            ("reviewed", "Reviewed ROI status"),
            ("stack_volume", "Full ROI stack volume"),
            ("tissue_volume", "ROI tissue volume"),
            ("effective_volume", "Effective sampling ROI volume"),
            ("effective_mm3", "Effective sampling volume"),
            ("tissue_z", "First / last tissue Z"),
            ("guard", "Physical Z guard"),
            ("planes", "Eligible sampling planes"),
        ))
        self.density_values = self._section(root, "quantification_density", "Nuclear Density", (
            ("density_mm3", "Measured nuclei/mm³"),
            ("density_cube", "Measured nuclei/(100 µm)³ cube"),
            ("measured_thickness", "Measured full tissue thickness"),
            ("nominal_thickness", "Nominal section thickness"),
            ("thickness_factor", "Thickness density correction factor"),
            ("corrected_density", "Nominal-thickness-corrected density"),
        ))
        self.exclusion_values = self._section(root, "quantification_exclusions", "Exclusion Summary", (
            ("morphology", "Morphology failures"),
            ("intensity", "Channel-intensity failures"),
            ("perinuclear", "Perinuclear failures"),
            ("z_guard", "Physical Z-guard exclusions"),
            ("sampling", "ROI / sampling exclusions"),
        ))
        self.exclusion_note = self._label(
            "Individual exclusion reasons can overlap and are not additive."
        )
        root.addWidget(self.exclusion_note)
        self.interpretation = self._label(
            "Descriptive retained-nucleus density; not an unbiased stereological estimator."
        )
        root.addWidget(self.interpretation)
        root.addStretch(1)

        project_state.segmentation_result_changed.connect(self.refresh)
        project_state.filtering_result_changed.connect(self.refresh)
        project_state.scientific_roi_changed.connect(self.refresh)
        project_state.calibration_changed.connect(self.refresh)
        self.refresh()

    @staticmethod
    def _label(text="—"):
        label = QLabel(str(text))
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        return label

    def _section(self, root, key, title, fields):
        section = CollapsibleSection(key, title)
        form = QFormLayout()
        form.setContentsMargins(10, 8, 10, 10)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        values = {}
        for name, label in fields:
            values[name] = self._label()
            form.addRow(label, values[name])
        section.set_content_layout(form)
        root.addWidget(section)
        return values

    @staticmethod
    def _number(value, suffix="", decimals=1):
        if value is None:
            return "Unavailable"
        return f"{float(value):,.{decimals}f}{suffix}"

    def refresh(self, *args):
        state = self.project_state
        segmentation = state.segmentation_result
        filtering = state.filtering_result
        if segmentation is None or not state.segmentation_is_current:
            state.clear_quantification_result()
            self._clear("Quantification unavailable: a current segmentation is required.")
            return
        self.summary_values["total"].setText(f"{int(segmentation['object_count']):,}")
        if filtering is None:
            state.clear_quantification_result()
            self._clear("Quantification pending: calculate scientific measurements.", preserve_total=True)
            return
        if not state.filtering_is_current:
            state.invalidate_quantification()
            self._clear("Quantification pending: scientific measurements require recalculation.", preserve_total=True)
            return
        try:
            result = calculate_quantification_result(state)
        except (KeyError, TypeError, ValueError) as error:
            state.clear_quantification_result()
            self._clear(f"Quantification unavailable: {error}", preserve_total=True)
            return
        state.set_quantification_result(result)
        self._display(result)

    def _clear(self, message, *, preserve_total=False):
        self.status.setText(message)
        for values in (
            self.summary_values, self.roi_values,
            self.density_values, self.exclusion_values,
        ):
            for key, label in values.items():
                if preserve_total and values is self.summary_values and key == "total":
                    continue
                label.setText("—")

    def _display(self, result):
        valid = result["density_status"] == "valid"
        self.status.setText(
            "Current scientific quantification is valid."
            if valid else
            "Preliminary counts available; density pending reviewed ROI."
            if result["density_status"] == "review ROI" else
            "Density unavailable: the effective sampling volume is empty."
        )
        self.summary_values["total"].setText(f"{result['total_cellpose_masks']:,}")
        self.summary_values["retained"].setText(f"{result['retained_masks']:,}")
        self.summary_values["excluded"].setText(f"{result['excluded_masks']:,}")
        self.roi_values["reviewed"].setText("Reviewed and accepted" if result["roi_reviewed"] else "Review required")
        self.roi_values["stack_volume"].setText(self._number(result["roi_stack_volume_um3"], " µm³"))
        self.roi_values["tissue_volume"].setText(self._number(result["roi_tissue_volume_um3"], " µm³"))
        self.roi_values["effective_volume"].setText(self._number(result["roi_effective_volume_um3"], " µm³"))
        self.roi_values["effective_mm3"].setText(self._number(result["roi_effective_volume_mm3"], " mm³", 9))
        self.roi_values["tissue_z"].setText(f"{result['first_tissue_z']} / {result['last_tissue_z']}")
        self.roi_values["guard"].setText(f"{result['z_guard_um']:g} µm ({result['z_guard_mode']})")
        self.roi_values["planes"].setText(str(result["eligible_sampling_planes"]))
        self.density_values["density_mm3"].setText(self._number(result["retained_nuclei_per_mm3"], " nuclei/mm³"))
        self.density_values["density_cube"].setText(self._number(result["retained_nuclei_per_100um_cube"], " nuclei/(100 µm)³"))
        self.density_values["measured_thickness"].setText(self._number(result["measured_full_tissue_thickness_um"], " µm"))
        self.density_values["nominal_thickness"].setText(self._number(result["nominal_section_thickness_um"], " µm"))
        self.density_values["thickness_factor"].setText(self._number(result["thickness_density_correction_factor"], "", 6))
        self.density_values["corrected_density"].setText(self._number(result["nominal_corrected_nuclei_per_100um_cube"], " nuclei/(100 µm)³"))
        for key, result_key in (
            ("morphology", "failed_morphology"),
            ("intensity", "failed_intensity"),
            ("perinuclear", "failed_perinuclear"),
            ("z_guard", "excluded_by_z_guard"),
            ("sampling", "excluded_by_sampling_roi"),
        ):
            self.exclusion_values[key].setText(f"{result[result_key]:,}")

    def dispose(self):
        state = self.project_state
        state.segmentation_result_changed.disconnect(self.refresh)
        state.filtering_result_changed.disconnect(self.refresh)
        state.scientific_roi_changed.disconnect(self.refresh)
        state.calibration_changed.disconnect(self.refresh)
