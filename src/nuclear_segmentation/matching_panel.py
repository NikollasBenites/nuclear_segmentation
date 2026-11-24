"""Matching controls and read-only layers in the existing desktop viewer."""
from __future__ import annotations

from napari.utils.colormaps import CyclicLabelColormap, DirectLabelColormap
from qtpy.QtCore import QAbstractTableModel, QModelIndex, QObject, QSignalBlocker, Qt
from qtpy.QtWidgets import (
    QCheckBox, QComboBox, QLabel, QLineEdit, QProgressBar, QPushButton, QSpinBox,
    QTableView, QVBoxLayout, QWidget,
)

from .automatic_matching import COLORS, MatchingSettings
from .matching_explorer import AssociationExplorer
from .matching_inspection import MatchingInspection
from .matching_execution import MatchingExecution
from .collapsible_section import CollapsibleSection
from .matching_pair import MATCHING_LAYER, MATCHING_ROLES
from .ui_icons import icon


class MatchingTable(QAbstractTableModel):
    columns = (("Nucleus ID", "nuclear_id"), ("Automatic class", "auto_class"),
               ("Auto-linked cyto ID", "accepted_cytoplasmic_id"),
               ("Best cyto ID", "best_cytoplasmic_id"), ("Second cyto ID", "second_cytoplasmic_id"),
               ("Best fraction", "best_nuclear_fraction"), ("Second fraction", "second_nuclear_fraction"),
               ("Total fraction", "total_nuclear_fraction"), ("Candidates", "cytoplasmic_candidate_count"),
               ("Overlap (voxels)", "overlap_voxels"), ("Overlap (µm³)", "overlap_volume_um3"),
               ("Nucleus (µm³)", "nuclear_volume_um3"), ("Best cyto (µm³)", "best_cytoplasmic_volume_um3"),
               ("IoU", "iou"), ("Dice", "dice"))

    def __init__(self, parent):
        super().__init__(parent)
        self.result = None

    def show_result(self, result):
        if self.result is not result:
            self.beginResetModel()
            self.result = result
            self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self.result.nuclei) if self.result and not parent.isValid() else 0

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns) if not parent.isValid() else 0

    def headerData(self, section, orientation, role):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section][0]
        return super().headerData(section, orientation, role)

    def data(self, index, role):
        if role == Qt.ItemDataRole.DisplayRole and index.isValid():
            row = self.result.nuclei[index.row()]
            value = getattr(row, self.columns[index.column()][1])
            if isinstance(value, float):
                return f"{value:.6g}"
            return "—" if value is None else str(value)


class MatchingPanel(QWidget):
    _default_opacity = 60

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        self.selectors = {}
        self.summaries = {}
        self.visibility = {}
        self.opacity = {}
        for role in MATCHING_ROLES:
            layout = self._section(root, role, f"{role.title()} Masks")
            label = QLabel(f"{role.title()} Session")
            selector = self.selectors[role] = QComboBox()
            selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            selector.setAccessibleName(label.text())
            label.setBuddy(selector)
            layout.addWidget(label)
            layout.addWidget(selector)
            self.summaries[role] = self._note(layout)

        compatibility = self._section(root, "compatibility", "Compatibility")
        self.compatibility = self._note(compatibility)
        visualization = self._section(root, "visualization", "Visualization")
        self.show_inputs = QPushButton(icon("labels"), "Show Matching Inputs")
        visualization.addWidget(self.show_inputs)
        for role in MATCHING_ROLES:
            visible = self.visibility[role] = QCheckBox(f"Show {role} masks")
            visualization.addWidget(visible)
            label = QLabel(f"{role.title()} opacity")
            opacity = self.opacity[role] = QSpinBox()
            opacity.setRange(0, 100)
            opacity.setSuffix("%")
            opacity.setAccessibleName(label.text())
            label.setBuddy(opacity)
            visualization.addWidget(label)
            visualization.addWidget(opacity)
        self.class_colors = QCheckBox("Color nuclei by automatic classification")
        visualization.addWidget(self.class_colors)
        legend = self._note(visualization)
        legend.setText("Automatic classes · Green MATCH · Yellow AMBIGUOUS · Red NO_MATCH")
        automatic = self._section(root, "automatic", "Automatic Matching")
        self.minimum = self._fraction(automatic, "Minimum best overlap fraction")
        self.maximum = self._fraction(automatic, "Maximum second-best overlap fraction")
        self.defaults = QPushButton("Restore Defaults")
        automatic.addWidget(self.defaults)
        self.run = QPushButton("Run Automatic Matching")
        automatic.addWidget(self.run)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Automatic matching progress")
        automatic.addWidget(self.progress)
        results = self._section(root, "results", "Matching Results")
        self.result_status = self._note(results)
        self.summary = self._note(results)
        self.table = QTableView()
        self.table.setAccessibleName("Automatic classifications by nuclear ID")
        self.table_model = MatchingTable(self.table)
        self.table.setModel(self.table_model)
        self.table.verticalHeader().hide()
        results.addWidget(self.table)
        self.graphs = QPushButton("Show Matching Graphs")
        results.addWidget(self.graphs)
        associations = self._section(root, "associations", "Multiple Associations")
        self.explorer = AssociationExplorer()
        associations.addWidget(self.explorer)
        self.defaults.clicked.connect(self.reset_thresholds)
        root.addStretch(1)
        self.reset_appearance()

    @staticmethod
    def _fraction(layout, title):
        control = QLineEdit()
        control.setAccessibleName(title)
        label = QLabel(title)
        label.setBuddy(control)
        layout.addWidget(label)
        layout.addWidget(control)
        return control

    def reset_thresholds(self):
        settings = MatchingSettings()
        self.minimum.setText(str(settings.match_min))
        self.maximum.setText(str(settings.second_max))

    def reset_appearance(self):
        with QSignalBlocker(self.minimum), QSignalBlocker(self.maximum), QSignalBlocker(self.class_colors):
            self.reset_thresholds()
            self.class_colors.setChecked(False)
        for role in MATCHING_ROLES:
            visible, opacity = self.visibility[role], self.opacity[role]
            with QSignalBlocker(visible), QSignalBlocker(opacity):
                visible.setChecked(True)
                opacity.setValue(self._default_opacity)

    @staticmethod
    def _section(root, key, title):
        section = CollapsibleSection(key, title)
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 5, 8, 8)
        section.set_content_layout(layout)
        root.addWidget(section)
        return layout

    @staticmethod
    def _note(layout):
        label = QLabel()
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(label)
        return label


class MatchingDisplay(QObject):
    """Present a captured pair without adopting the active session's layer ownership."""

    _colors = {"nuclear": (0.16, 0.8, 1.0, 1.0), "cytoplasmic": (1.0, 0.52, 0.16, 1.0)}

    def __init__(self, viewer, state, panel, session_display, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.state = state
        self.panel = panel
        self.session_display = session_display
        self.layers = {}
        self._pair = state.matching_pair
        self._displayed = None
        self._previous_layer = None
        self._active = False
        self._requested = False
        self._showing = False
        self._colored_result = None
        self.inspection = MatchingInspection(self)
        self.execution = MatchingExecution(state, self)
        self.execution.changed.connect(self.refresh)
        self.execution.progress_changed.connect(self._progress)
        panel.minimum.textChanged.connect(self._settings_changed)
        panel.maximum.textChanged.connect(self._settings_changed)
        panel.run.clicked.connect(self.execution.start)
        panel.class_colors.toggled.connect(self._toggle_colors)
        self._state_signals = (
            state.sessions_changed, state.channels_changed, state.matching_selection_changed,
            state.segmentation_result_changed, state.filtering_result_changed,
            state.inactive_session_validity_changed, state.calibration_changed, state.matching_result_changed,
        )
        for signal in self._state_signals:
            signal.connect(self.refresh)
        panel.show_inputs.clicked.connect(self.show)
        viewer.layers.events.removed.connect(self._removed)
        for role in MATCHING_ROLES:
            panel.selectors[role].currentIndexChanged.connect(lambda _, role=role: self._select(role))
            panel.visibility[role].toggled.connect(lambda visible, role=role: self._set_visible(role, visible))
            panel.opacity[role].valueChanged.connect(lambda value, role=role: self._set_opacity(role, value))

    def set_active(self, active):
        if self._active == active:
            return
        self._active = active
        if active:
            self.refresh()
        else:
            self._stop_showing()
            self.panel.table_model.show_result(None)

    def show(self):
        if self._active:
            self.refresh(request_visualization=True)

    def _stop_showing(self):
        self._remove_layers()
        if self._showing:
            self._showing = False
            self.session_display.resume()
            previous = self._previous_layer
            if previous in self.viewer.layers and previous.visible:
                self.viewer.layers.selection.active = previous
        self._previous_layer = None

    def _progress(self, percent):
        self.panel.progress.setValue(percent)
        self.panel.progress.setFormat("Classifying overlaps…" if percent == 100 else "Counting voxels · %p%")

    def _settings_changed(self):
        try:
            settings = MatchingSettings(float(self.panel.minimum.text()), float(self.panel.maximum.text()))
        except ValueError:
            settings = None
        self._pair.set_settings(settings)

    def _toggle_colors(self, enabled):
        self.inspection.restore()
        self.refresh(request_visualization=enabled)

    def _select(self, role):
        self._pair.select(role, self.panel.selectors[role].currentData())

    def _session_summary(self, session):
        segmentation = ("current" if session.segmentation_is_current else
                        "outdated" if session.segmentation_result else "missing")
        filtering = ("current" if session.filtering_is_current else
                     "outdated" if session.filtering_result or session.pending_geometry_revision is not None
                     else "missing")
        count = (session.filtering_result or {}).get("retained_count")
        summary = f"Segmentation {segmentation} · Filtering {filtering}"
        if count is not None:
            summary += f" · {count:,} retained" if session.filtering_is_current else f" · {count:,} previously retained"
        return summary

    def _refresh_selector(self, role, check):
        selector = self.panel.selectors[role]
        with QSignalBlocker(selector):
            selector.clear()
            selector.addItem(f"Select a {role} session", None)
            if self.state.dataset_ready:
                channels = {c.index: c.name for c in self.state.channels}
                for session in self.state.sessions.values():
                    if session.scientific_role != role:
                        continue
                    index = session.source_channel_index
                    if index is None:
                        index = self.state.segmentation_channel_index
                    source = channels.get(index, "Source channel unavailable")
                    title = f"{session.display_name} · {source}"
                    selector.addItem(title, session.session_id)
                    selector.setItemData(
                        selector.count() - 1, f"{title}\n{self._session_summary(session)}",
                        Qt.ItemDataRole.ToolTipRole,
                    )
            selector.setCurrentIndex(max(0, selector.findData(self._pair.session_id(role))))
            selector.setEnabled(selector.count() > 1)
            selector.setToolTip(selector.currentText())
        session = self.state.sessions.get(self._pair.session_id(role))
        summary = self._session_summary(session) if session else ""
        if check.input is not None and not check.input.retained_ids:
            summary = f"0 retained · Validated\nNo retained {role} objects. Review filter settings."
        self.panel.summaries[role].setText(summary)

    def refresh(self, *args, request_visualization=False):
        if self._pair is not self.state.matching_pair:
            self._stop_showing()
            self.panel.explorer.set_result(None, "Run automatic matching to inspect associations.")
            self.panel.explorer.multiple.setChecked(True)
            self.panel.explorer.include.setChecked(False)
            self._pair = self.state.matching_pair
            self._requested = False
            self.panel.reset_appearance()
            self.panel.table_model.show_result(None)
            self.panel.summary.clear()
            self.panel.result_status.clear()
        if not self._active:
            index = self.panel.explorer.index
            if index is not None and self._pair.current_result is not index.result:
                self.panel.explorer.set_result(None, "Matching results unavailable.")
            return
        pair, checks = self._pair.inspect()
        for role in MATCHING_ROLES:
            self._refresh_selector(role, checks[role])
        if not self.state.dataset_ready:
            message = "Load an image to select matching inputs."
        elif pair is None:
            message = "\n".join(
                f"{role.title()} · {checks[role].message}" for role in MATCHING_ROLES if checks[role].input is None
            )
        else:
            message = "Compatible grid and validated retained masks."
        if pair is None:
            self._stop_showing()
        else:
            if request_visualization:
                self._requested = True
            if self._requested:
                self._show_pair(pair)
        result = self._pair._result_for(pair)
        explorer = self.panel.explorer
        if explorer.index is not None and explorer.index.result is not result:
            self.inspection.reset()
        self._show_results(pair, result)
        explorer.set_result(result, self.panel.result_status.text())
        self._apply_colors(result if self.panel.class_colors.isChecked() else None)
        self.panel.compatibility.setText(message)
        self.panel.show_inputs.setEnabled(pair is not None and len(self.layers) != len(MATCHING_ROLES))
        for role in MATCHING_ROLES:
            self.panel.visibility[role].setEnabled(role in self.layers)
            self.panel.opacity[role].setEnabled(role in self.layers)

    def _show_results(self, pair, result):
        panel, context = self.panel, self._pair
        panel.run.setEnabled(pair is not None and context.pending_settings is not None and not self.execution.busy)
        panel.progress.setVisible(context.job is not None)
        self._progress(self.execution.percent)
        panel.class_colors.setEnabled(result is not None)
        panel.table_model.show_result(result)
        lines = []
        if context.executed_settings is not None:
            settings = context.executed_settings
            lines.append(f"Last completed settings · Best ≥ {settings.match_min}, second ≤ {settings.second_max}")
        if context.job is not None:
            status = "Running automatic matching…"
        elif context.pending_settings is None:
            status = "Enter finite fractions. Best must be greater than 0 and at most 1. Second must be between 0 and 1."
        elif context.error is not None:
            status = f"Calculation failed. {context.error} Run automatic matching to retry."
        elif result is not None:
            total = len(result.nuclei)
            status = "Current automatic result." if total else "Current empty result · Zero nuclei to classify."
            lines.append(f"Total retained nuclei · {total:,}")
            for name, count in result.class_counts.items():
                fraction = f"{count / total:.1%}" if total else "—"
                lines.append(f"{name} · {count:,} ({fraction})")
            lines.append(f"Multiple associations · {result.multiple_objects:,} cytoplasmic objects, "
                         f"{result.nuclei_in_multiple:,} nuclei")
            if not pair.cytoplasmic.retained_ids:
                lines.append("The retained cytoplasmic population is empty.")
            if not pair.cytoplasmic.retained_ids or result.class_counts["NO_MATCH"]:
                lines.append("A missing retained cytoplasmic candidate does not establish absence of marker expression.")
        elif context.attempted:
            status = "Outdated · Check the selected inputs and run with the displayed settings."
        else:
            status = "Ready to run automatic matching." if pair else "Select compatible inputs to run automatic matching."
        if self.execution.busy and context.job is None and result is None:
            status += " Waiting for the previous worker to finish."
        panel.result_status.setText(status)
        panel.summary.setText("\n".join(lines))

    def _apply_colors(self, result):
        layer = self.layers.get("nuclear")
        if layer is None or self.inspection.appearance or self._colored_result is result:
            return
        if result is None:
            layer.colormap = CyclicLabelColormap(colors=[(0, 0, 0, 0), self._colors["nuclear"]])
        else:
            colors = {r.nuclear_id: COLORS[r.auto_class] for r in result.nuclei}
            colors.update({None: (0, 0, 0, 0), 0: (0, 0, 0, 0)})
            layer.colormap = DirectLabelColormap(color_dict=colors)
        self._colored_result = result

    def _show_pair(self, pair):
        if not self._showing:
            if self.session_display.suspended:
                return
            self._previous_layer = self.viewer.layers.selection.active
            self.session_display.suspend(keep_hidden=True)
            self._showing = True
        if self._displayed != pair or len(self.layers) != len(MATCHING_ROLES):
            self._remove_layers()
            self._add_layers(pair)
        else:
            for role, layer in self.layers.items():
                layer.name = self._layer_name(role)

    def _layer_name(self, role):
        session = self.state.sessions[self._pair.session_id(role)]
        return f"Matching · {role.title()} Masks · {session.display_name}"

    def _add_layers(self, pair):
        for role in MATCHING_ROLES:
            source = getattr(pair, role)
            layer = self.viewer.add_labels(
                source.labels, name=self._layer_name(role),
                scale=pair.grid.spacing_zyx_um, translate=pair.grid.translation_zyx_um,
                units=("µm", "µm", "µm"), blending="additive",
                colormap=CyclicLabelColormap(colors=[(0, 0, 0, 0), self._colors[role]]),
                opacity=self.panel.opacity[role].value() / 100,
                visible=self.panel.visibility[role].isChecked(),
                metadata={MATCHING_LAYER: role, "matching_pair": pair.population_identity,
                          "source_revision": source.owner, "measurement_revision": source.measurement_revision},
            )
            layer.editable = False
            self.layers[role] = layer
            layer.events.visible.connect(self._appearance_changed)
            layer.events.opacity.connect(self._appearance_changed)
        self._displayed = pair

    def _appearance_changed(self, event):
        role = event.source.metadata[MATCHING_LAYER]
        if event.type == "visible":
            control = self.panel.visibility[role]
            with QSignalBlocker(control):
                control.setChecked(event.source.visible)
        else:
            control = self.panel.opacity[role]
            with QSignalBlocker(control):
                control.setValue(round(event.source.opacity * 100))

    def _set_visible(self, role, visible):
        if role in self.layers:
            self.layers[role].visible = visible

    def _set_opacity(self, role, value):
        if role in self.layers:
            self.layers[role].opacity = value / 100

    def _removed(self, event):
        layer = event.value
        role = layer.metadata.get(MATCHING_LAYER)
        if self.layers.get(role) is layer:
            self.inspection.restore()
            layer.events.visible.disconnect(self._appearance_changed)
            layer.events.opacity.disconnect(self._appearance_changed)
            del self.layers[role]
            if self._active and self._displayed is not None:
                self.panel.visibility[role].setEnabled(False)
                self.panel.opacity[role].setEnabled(False)
                self.panel.show_inputs.setEnabled(True)
        elif self._previous_layer is layer:
            self._previous_layer = None

    def _remove_layers(self):
        self.inspection.reset()
        self._colored_result = None
        self._displayed = None
        for layer in tuple(self.layers.values()):
            self.viewer.layers.remove(layer)

    def dispose(self):
        self.set_active(False)
        self.panel.explorer.set_result(None, "Matching results unavailable.")
        for signal in self._state_signals:
            signal.disconnect(self.refresh)
        self.viewer.layers.events.removed.disconnect(self._removed)
