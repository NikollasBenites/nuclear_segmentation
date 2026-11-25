"""Matching controls and read-only layers in the existing desktop viewer."""
from __future__ import annotations

from weakref import ref

import numpy as np
from qtpy.QtCore import QAbstractTableModel, QModelIndex, QObject, QSignalBlocker, Qt, Signal
from qtpy.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QProgressBar, QPushButton, QSpinBox,
    QStackedWidget, QTableView, QVBoxLayout, QWidget,
)

from .automatic_matching import MatchingSettings
from .matching_appearance import MaskAppearance, appearance_control, relationships
from .candidate_explorer import CandidateExplorer
from .matching_explorer import AssociationExplorer, ResultSelection
from .matching_inspection import MatchingInspection, add_population, focus_population
from .matching_navigation import ExplorerPreferences, InspectionLocation, result_key, row_for, sources
from .matching_execution import MatchingExecution
from .collapsible_section import CollapsibleSection
from .mask_selection import pick_world_label
from .matching_pair import MATCHING_LAYER, MATCHING_ROLES
from .ui_icons import icon


class MatchingTable(QAbstractTableModel):
    sorted_rows = Signal()
    columns = (("Nucleus ID", "nuclear_id"), ("Automatic class", "auto_class"),
               ("Auto-linked cyto ID", "accepted_cytoplasmic_id"),
               ("Best cyto ID", "best_cytoplasmic_id"), ("Second cyto ID", "second_cytoplasmic_id"),
               ("Best fraction", "best_nuclear_fraction"), ("Second fraction", "second_nuclear_fraction"),
               ("Total fraction", "total_nuclear_fraction"), ("Candidates", "cytoplasmic_candidate_count"),
               ("Overlap (voxels)", "overlap_voxels"), ("Overlap (µm³)", "overlap_volume_um3"),
               ("Nucleus (µm³)", "nuclear_volume_um3"), ("Best cyto (µm³)", "best_cytoplasmic_volume_um3"),
               ("IoU", "iou"), ("Dice", "dice"), ("Automatic reason", "classification_reason"))

    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.rows = []
        self.row_numbers = {}
        self.order = 0, Qt.SortOrder.AscendingOrder

    def show_result(self, result):
        if self.result is not result:
            self.result = result
            self.sort(*self.order)

    def sort(self, column, order):
        if not 0 <= column < len(self.columns):
            return
        field = self.columns[column][1]

        def sort_key(row):
            value = getattr(row, field)
            return value is None, value

        self.beginResetModel()
        self.order = column, order
        rows = self.result.nuclei if self.result else ()
        self.rows = sorted(rows, key=sort_key, reverse=order == Qt.SortOrder.DescendingOrder)
        self.row_numbers = {row.nuclear_id: i for i, row in enumerate(self.rows)}
        self.endResetModel()
        self.sorted_rows.emit()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns) if not parent.isValid() else 0

    def headerData(self, section, orientation, role):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section][0]
        return super().headerData(section, orientation, role)

    def data(self, index, role):
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole) and index.isValid():
            row = self.rows[index.row()]
            value = getattr(row, self.columns[index.column()][1])
            if role == Qt.ItemDataRole.UserRole:
                return value
            if self.columns[index.column()][1].endswith("_nuclear_fraction"):
                return f"{value:.2%}"
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
        self.styles = {}
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
            self.styles[role] = appearance_control(visualization, role)
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
        self.class_colors = QCheckBox("Color nuclei by automatic class")
        visualization.addWidget(self.class_colors)
        visualization.addWidget(QLabel("Click to inspect"))
        self.pick_target = QComboBox()
        self.pick_target.setAccessibleName("Mask selection target")
        self.pick_target.addItems(["Nuclear", "Cytoplasmic"])
        visualization.addWidget(self.pick_target)
        self.selection_status = self._note(root)
        automatic = self._section(root, "automatic", "Automatic Matching")
        self.total_minimum = self._fraction(automatic, "Minimum total overlap fraction")
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
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.verticalHeader().hide()
        results.addWidget(self.table)
        self.graphs = QPushButton("Show Matching Graphs")
        results.addWidget(self.graphs)
        associations = self._section(root, "associations", "Multiple Associations")
        self.explorer = AssociationExplorer()
        self.explorer_mode = QComboBox()
        self.explorer_mode.setAccessibleName("Association explorer")
        self.explorer_mode.addItems(["Multiple Associations", "Multiple Cytoplasmic Candidates"])
        associations.addWidget(self.explorer_mode)
        self.candidate_explorer = CandidateExplorer()
        pages = QStackedWidget()
        pages.addWidget(self.explorer)
        pages.addWidget(self.candidate_explorer)
        associations.addWidget(pages)
        self.explorer_mode.currentIndexChanged.connect(pages.setCurrentIndex)
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
        self.total_minimum.setText(str(settings.minimum_total_overlap_fraction))
        self.minimum.setText(str(settings.match_min))
        self.maximum.setText(str(settings.second_max))

    def sync_thresholds(self, settings):
        with QSignalBlocker(self.total_minimum), QSignalBlocker(self.minimum), QSignalBlocker(self.maximum):
            self.total_minimum.setText(str(settings.minimum_total_overlap_fraction) if settings else "")
            self.minimum.setText(str(settings.match_min) if settings else "")
            self.maximum.setText(str(settings.second_max) if settings else "")

    def reset_appearance(self):
        self.sync_thresholds(MatchingSettings())
        with QSignalBlocker(self.class_colors):
            self.class_colors.setChecked(False)
        for role in MATCHING_ROLES:
            visible, opacity = self.visibility[role], self.opacity[role]
            with QSignalBlocker(visible), QSignalBlocker(opacity), QSignalBlocker(self.styles[role]):
                self.styles[role].setCurrentIndex(0)
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


    legend_changed = Signal(object)

    def __init__(self, viewer, state, panel, session_display, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.state = state
        self.panel = panel
        self.session_display = session_display
        self.layers = {}
        self._pair = state.matching_pair
        self._displayed = None
        self._default_preferences = ExplorerPreferences.capture(panel)
        self._preferences_key = None
        self.locations = {}
        self.review_choices = {}
        self._unavailable = ""
        self._previous_layer = None
        self._active = False
        self._requested = False
        self._showing = False
        self._syncing = False
        self._isolated_ids = None
        self.renderer = MaskAppearance(viewer, state)
        self.inspection = MatchingInspection(self)
        viewer.mouse_drag_callbacks.append(self._pick)
        viewer.dims.events.order.connect(self.render)
        viewer.dims.events.ndisplay.connect(self.render)
        panel.table_model.sorted_rows.connect(self.render)
        panel.table.selectionModel().currentRowChanged.connect(self._result_row)
        for box in panel.styles.values():
            box.currentIndexChanged.connect(self.render)
        panel.explorer_mode.currentIndexChanged.connect(self._explorer_changed)
        panel.candidate_explorer.changed.connect(self.render)
        panel.candidate_explorer.focus_requested.connect(self._focus_candidate)
        panel.candidate_explorer.navigated.connect(self._candidate_navigated)
        state.association_review_changed.connect(panel.candidate_explorer._selection_changed)
        self.execution = MatchingExecution(state, self)
        self.execution.changed.connect(self.refresh)
        self.execution.progress_changed.connect(self._progress)
        panel.total_minimum.textChanged.connect(self._settings_changed)
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
        if not active:
            self._save_location()
        self._active = active
        if active:
            self.refresh()
        else:
            self._stop_showing()
            self.panel.table_model.show_result(None)
            self.panel.explorer.set_result(None, "Matching inactive.")
            self.panel.candidate_explorer.set_result(None, self._pair.review)
            self.panel.candidate_explorer.isolate.setChecked(False)
            self.panel.selection_status.clear()
            self.legend_changed.emit(None)

    def proofreading_target(self):
        p = self.panel
        index = p.explorer.index
        if not self._active or not index or index.result is not self._pair.current_result:
            return None
        if p.explorer_mode.currentIndex():
            ui = p.candidate_explorer
            if ui.nucleus is None:
                return None
            role, label = ui.focus_role, ui.focus_id
            refs = (ui.selection().ids or tuple(ui.by_nucleus[ui.nucleus])) if role == "nuclear" else (ui.nucleus,)
        else:
            selected = p.explorer.selection
            if not selected:
                return None
            role, label = selected.focus_role, selected.focus_id
            refs = (selected.cytoplasmic_id,) if role == "nuclear" else selected.associated_ids
        selection = ResultSelection(ref(index.result), index.result.statistics.population_identity)
        return selection, role, label, refs

    def _save_location(self):
        index = self.panel.explorer.index
        if index is not None and not self._unavailable:
            self.locations[result_key(index.result)] = InspectionLocation.capture(self)

    def _pair_key(self):
        return (self.state.session_revision().project_id, *(self._pair.session_id(role) for role in MATCHING_ROLES))

    def location(self):
        return self.locations.get(self._pair_key())

    def review_nucleus(self, result):
        loc = self.location()
        choice = self.review_choices.get(result_key(result))
        if choice is None and loc is not None:
            choice = loc.revisions, loc.inspected_nucleus
        if choice and choice[1] is not None:
            if choice[0] != sources(result) or choice[1] not in result.statistics.nuclear_voxels:
                return None, True
            return choice[1], False
        return None, False

    def remember_review_nucleus(self, result, label):
        if result is self._pair.current_result and label in result.statistics.nuclear_voxels:
            self.review_choices[result_key(result)] = sources(result), label

    def show(self):
        if self._active:
            self.refresh(request_visualization=True)

    def _result_row(self, current, previous):
        if self._syncing:
            return
        if current.isValid() and self.panel.table_model.result is self._pair.current_result:
            label = self.panel.table_model.rows[current.row()].nuclear_id
            self.select_object("nuclear", label)
            if self.panel.candidate_explorer.follow.isChecked() and "nuclear" in self.layers:
                focus_population(self.viewer, self.state, self.layers["nuclear"], label)

    def _pick(self, viewer, event):
        if not self._active or event.button != 1 or event.modifiers:
            return
        role = self.panel.pick_target.currentText().lower()
        layer = self.layers.get(role)
        result = self._pair.current_result
        if not result or layer is None or not layer.visible or not layer.opacity:
            return
        start = np.array(event.pos)
        direction = event.view_direction
        axes = tuple(event.dims_displayed)
        ids = None if self._isolated_ids is None else self._isolated_ids[role]
        value = pick_world_label(layer, event.position, axes, direction, visible_ids=ids)
        if not value:
            return
        yield
        while event.type == "mouse_move":
            if np.abs(event.pos - start).sum() >= QApplication.startDragDistance():
                return
            yield
        if (event.type == "mouse_release" and not event.modifiers and self._active
                and self.layers.get(role) is layer and self._pair.current_result is result
                and np.abs(event.pos - start).sum() < QApplication.startDragDistance()):
            self.select_object(role, int(value))

    def select_object(self, role, label):
        if self._syncing or not self._active:
            return
        index = self.panel.explorer.index
        if not index or index.result is not self._pair.current_result:
            return
        nuclear = role == "nuclear"
        if label not in (index.nuclei if nuclear else index.objects):
            return
        switching = self.panel.explorer_mode.currentIndex() != int(nuclear)
        was_isolated = (self.panel.candidate_explorer.isolate.isChecked() if not nuclear
                        else bool(self.inspection.appearance))
        self._syncing = True
        try:
            ui = self.panel.candidate_explorer if nuclear else self.panel.explorer
            self.panel.explorer_mode.setCurrentIndex(int(nuclear))
            if switching and nuclear:
                ui.isolate.setChecked(was_isolated)
            if nuclear and ui.classes.currentText() not in ("All classes", index.nuclei[label].auto_class):
                ui.classes.setCurrentIndex(0)
            if not any(row[0] == label for row in ui.objects.model().rows):
                ui.multiple.setChecked(False)
            pos = next(i for i, row in enumerate(ui.objects.model().rows) if row[0] == label)
            ui.objects.selectRow(pos)
            if nuclear:
                ui._object_changed(ui.objects.currentIndex())
            else:
                ui._object_clicked(ui.objects.currentIndex())
        finally:
            self._syncing = False
        if switching and not nuclear and was_isolated:
            self.inspection.isolate()
        else:
            self.render()

    def _explorer_changed(self):
        self.inspection.reset()
        if not self._syncing:
            self.show()
            self.render()

    def _candidate_navigated(self):
        if self.panel.candidate_explorer.follow.isChecked():
            self._focus_candidate()

    def _focus_candidate(self):
        ui = self.panel.candidate_explorer
        if self._syncing or not self._active or self.panel.explorer_mode.currentIndex() != 1:
            return
        if ui.result is self._pair.current_result and ui.focus_id is not None:
            if ui.focus_role not in self.layers:
                self.show()
            if ui.focus_role in self.layers:
                focus_population(self.viewer, self.state, self.layers[ui.focus_role], ui.focus_id)

    def render(self, *args):
        if self._syncing or not self._active:
            return
        index = self.panel.explorer.index
        focus = isolated = None
        marks = {}
        if index and index.result is self._pair.current_result:
            if self.panel.explorer_mode.currentIndex():
                ui = self.panel.candidate_explorer
                if ui.result is index.result and ui.nucleus is not None:
                    focus = ui.focus_role, ui.focus_id
                    if ui.isolate.isChecked():
                        ids = ui.selection().ids or ui.by_nucleus[ui.nucleus]
                        isolated = {"nuclear": {ui.nucleus}, "cytoplasmic": set(ids)}
            else:
                ui = self.panel.explorer
                s = ui.selection
                if s:
                    focus = s.focus_role, s.focus_id
                    if self.inspection.appearance:
                        ids = set(s.associated_ids)
                        if ui.include.isChecked():
                            ids.update(s.other_candidate_ids)
                        isolated = {"nuclear": ids, "cytoplasmic": {s.cytoplasmic_id}}
                ui.restore.setEnabled(bool(self.inspection.appearance))
        else:
            index = None
        message = ""
        if focus:
            role, label = focus
            marks = relationships(index, role, label)
            if isolated is not None:
                if role == "cytoplasmic" and not self.panel.explorer_mode.currentIndex() and not ui.include.isChecked():
                    marks = {k: v for k, v in marks.items() if v != "candidate"}
                if self.panel.explorer_mode.currentIndex() and role == "nuclear":
                    marks = {k: v for k, v in marks.items() if v != "candidate" or k[1] in isolated[k[0]]}
                for pop, obj in marks:
                    isolated[pop].add(obj)
            if role == "nuclear":
                row = index.nuclei[label]
                message = f"Nucleus {label} · {row.auto_class} · {row.classification_reason}"
            else:
                message = f"Cytoplasmic object {label} · {index.objects[label].accepted_count} accepted nuclei"
        if focus:
            self.review_choices.pop(result_key(index.result), None)
            self._unavailable = ""
        message = message or self._unavailable
        self._isolated_ids = isolated
        self.panel.selection_status.setText(message)
        self.legend_changed.emit(message if index or message else None)
        table = self.panel.table
        with QSignalBlocker(table.selectionModel()):
            if focus and focus[0] == "nuclear" and focus[1] in self.panel.table_model.row_numbers:
                table.selectRow(self.panel.table_model.row_numbers[focus[1]])
            else:
                table.setCurrentIndex(QModelIndex())
                table.clearSelection()
        if len(self.layers) == 2:
            self.renderer.render(self.layers, self.panel.styles, index, self.panel.class_colors.isChecked(), marks, isolated)

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
            settings = MatchingSettings(float(self.panel.minimum.text()), float(self.panel.maximum.text()),
                                        float(self.panel.total_minimum.text()))
        except ValueError:
            settings = None
        self._pair.set_settings(settings)

    def _toggle_colors(self, enabled):
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
            self.locations.clear()
            self.review_choices.clear()
            self._unavailable = ""
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
            candidates = self.panel.candidate_explorer
            if candidates.result is not None and candidates.result is not self._pair.current_result:
                candidates.set_result(None, self._pair.review)
            index = self.panel.explorer.index
            if index is not None and self._pair.current_result is not index.result:
                self.panel.explorer.set_result(None, "Matching results unavailable.")
            return
        pair, checks = self._pair.inspect()
        result = self._pair._result_for(pair)
        explorer = self.panel.explorer
        previous = explorer.index.result if explorer.index else None
        changed = result is not previous
        if changed and previous is not None:
            self._save_location()
            self.inspection.reset()
        loc = self.location()
        if not result:
            self._unavailable = "Previous selection unavailable until matching results are current." if loc else ""
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
        self._syncing = True
        try:
            key = self._pair_key()
            if key != self._preferences_key:
                self._preferences_key = key
                (loc.preferences if loc else self._default_preferences).apply(self.panel)
                self.panel.candidate_explorer.isolate.setChecked(False)
            self._show_results(pair, result)
            explorer.set_result(result, self.panel.result_status.text())
            self.panel.candidate_explorer.set_result(result, self._pair.review)
            if changed and result:
                self._unavailable = ""
                if loc and not loc.restore(self, result):
                    label = loc.nucleus if loc.preferences.mode else loc.parent
                    if label is not None:
                        role = "nuclear" if loc.preferences.mode else "cytoplasmic"
                        self._unavailable = f"Previous {role} selection {label} unavailable for the current geometry or filters."
        finally:
            self._syncing = False
        if changed and result:
            self._restore_review_choice(result, loc)
        self.render()
        if changed and result and loc:
            loc.scroll(self.panel)
        self.panel.compatibility.setText(message)
        self.panel.show_inputs.setEnabled(pair is not None and len(self.layers) != len(MATCHING_ROLES))
        for role in MATCHING_ROLES:
            self.panel.visibility[role].setEnabled(role in self.layers)
            self.panel.opacity[role].setEnabled(role in self.layers)

    def _restore_review_choice(self, result, loc):
        explorer = self.panel.explorer
        choice = self.review_choices.pop(result_key(result), None)
        if choice and (choice[0] != sources(result) or choice[1] not in result.statistics.nuclear_voxels):
            self._syncing = True
            try:
                self.panel.explorer_mode.setCurrentIndex(1)
                self.panel.candidate_explorer.clear_inspection(True)
            finally:
                self._syncing = False
            self.review_choices[result_key(result)] = choice
            self._unavailable = f"Previous nuclear selection {choice[1]} unavailable for the current geometry."
        elif choice:
            pos = row_for(explorer.candidates, choice[1])
            if not self.panel.explorer_mode.currentIndex() and explorer.selection and pos is not None:
                explorer.candidates.selectRow(pos)
            elif not loc or choice[1] != loc.inspected_nucleus or self._unavailable:
                self.select_object("nuclear", choice[1])
                self._candidate_navigated()

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
            lines.append(f"Last completed settings · Best ≥ {settings.match_min}, second ≤ {settings.second_max}, "
                         f"total ≥ {settings.minimum_total_overlap_fraction}")
        if context.job is not None:
            status = "Running automatic matching…"
        elif context.pending_settings is None:
            status = ("Enter finite fractions in [0, 1]. Best must be positive. "
                      "Minimum total overlap must be at most the minimum best overlap.")
        elif context.error is not None:
            status = f"Calculation failed. {context.error} Run automatic matching to retry."
        elif result is not None:
            total = len(result.nuclei)
            status = "Current automatic result." if total else "Current empty result · Zero nuclei to classify."
            lines.append(f"Total retained nuclei · {total:,}")
            for name, count in result.class_counts.items():
                fraction = f"{count / total:.1%}" if total else "—"
                lines.append(f"{name} · {count:,} ({fraction})")
            for reason, count in result.no_match_reason_counts.items():
                lines.append(f"  {reason} · {count:,}")
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
            layer = add_population(self.viewer, pair, role, self._layer_name(role),
                                   self.panel.visibility[role].isChecked(), self.panel.opacity[role].value() / 100)
            self.layers[role] = layer
            self.panel.visibility[role].setEnabled(True)
            self.panel.opacity[role].setEnabled(True)
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

        self.render()

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
            del self.layers[role]
            self.renderer.clear()
            self.inspection.reset()
            layer.events.visible.disconnect(self._appearance_changed)
            layer.events.opacity.disconnect(self._appearance_changed)
            if self._active and self._displayed is not None:
                self.panel.visibility[role].setEnabled(False)
                self.panel.opacity[role].setEnabled(False)
                self.panel.show_inputs.setEnabled(True)
        elif self._previous_layer is layer:
            self._previous_layer = None

    def _remove_layers(self):
        self.inspection.reset()
        self.renderer.clear()
        self._displayed = None
        for layer in tuple(self.layers.values()):
            self.viewer.layers.remove(layer)

    def dispose(self):
        self.set_active(False)
        self.panel.explorer.set_result(None, "Matching results unavailable.")
        self.panel.candidate_explorer.set_result(None, self._pair.review)
        self.state.association_review_changed.disconnect(self.panel.candidate_explorer._selection_changed)
        for signal in self._state_signals:
            signal.disconnect(self.refresh)
        self.viewer.layers.events.removed.disconnect(self._removed)
        self.viewer.mouse_drag_callbacks.remove(self._pick)
        self.viewer.dims.events.order.disconnect(self.render)
        self.viewer.dims.events.ndisplay.disconnect(self.render)
