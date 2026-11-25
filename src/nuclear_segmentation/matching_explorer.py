"""Read-only, result-scoped exploration of automatic associations."""
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import NamedTuple
from weakref import ReferenceType, ref

from qtpy.QtCore import QAbstractTableModel, QModelIndex, QSignalBlocker, Qt, Signal
from qtpy.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QPushButton, QTableView,
    QVBoxLayout, QWidget,
)

from .automatic_matching import AutomaticMatchingResult


@dataclass(frozen=True)
class ResultSelection:
    """Result-scoped handoff. Population identity contains the grid and both SessionRevisions."""
    source: ReferenceType[AutomaticMatchingResult]
    population_identity: tuple

    @property
    def nuclear_revision(self):
        return self.population_identity[1][0]

    @property
    def cytoplasmic_revision(self):
        return self.population_identity[2][0]

    @property
    def project_id(self):
        return self.nuclear_revision.project_id

    def is_current(self, pair):
        """IDs were captured from this immutable result. Recheck ownership at use time."""
        result = self.source()
        return (result is not None and pair.current_result is result
                and self.population_identity == result.statistics.population_identity)


@dataclass(frozen=True)
class AssociationSelection(ResultSelection):
    cytoplasmic_id: int
    associated_ids: tuple[int, ...]
    other_candidate_ids: tuple[int, ...]
    focus_role: str
    focus_id: int

    @property
    def focus_revision(self):
        return self.nuclear_revision if self.focus_role == "nuclear" else self.cytoplasmic_revision


class CytoplasmicRow(NamedTuple):
    label_id: int
    accepted_count: int
    associated_ids: tuple[int, ...]
    candidate_count: int
    volume_um3: float


class CandidateRow(NamedTuple):
    label_id: int
    auto_class: str
    accepted_cytoplasmic_id: int | None
    voxels: int
    volume_um3: float
    nuclear_fraction: float
    accepted_here: bool


class AssociationIndex:
    def __init__(self, result):
        self.result = result
        self.nuclei = {row.nuclear_id: row for row in result.nuclei}
        self.overlaps = defaultdict(list)
        self.by_nucleus = defaultdict(list)
        accepted = defaultdict(list)
        stats = result.statistics
        for (nucleus, cyto), count in stats.intersections.items():
            self.by_nucleus[nucleus].append(cyto)
            self.overlaps[cyto].append((nucleus, count))
            if self.nuclei[nucleus].accepted_cytoplasmic_id == cyto:
                accepted[cyto].append(nucleus)
        self.objects = {}
        for cyto, size in stats.cytoplasmic_voxels.items():
            ids = tuple(sorted(accepted[cyto]))
            self.objects[cyto] = CytoplasmicRow(cyto, len(ids), ids, len(self.overlaps[cyto]),
                                                size * stats.voxel_volume_um3)

    def candidates(self, cyto):
        stats = self.result.statistics
        for nucleus, count in self.overlaps[cyto]:
            row = self.nuclei[nucleus]
            yield CandidateRow(nucleus, row.auto_class, row.accepted_cytoplasmic_id, count,
                               count * stats.voxel_volume_um3, count / stats.nuclear_voxels[nucleus],
                               row.accepted_cytoplasmic_id == cyto)


class InspectionTable(QAbstractTableModel):
    """Sort native Python values and format only cells requested by Qt."""
    sorting_rows = Signal()
    sorted_rows = Signal()

    def __init__(self, headers, parent):
        super().__init__(parent)
        self.headers = headers
        self.rows = []
        self.order = (0, Qt.SortOrder.AscendingOrder)

    def replace(self, rows):
        column, order = self.order
        self.beginResetModel()
        self.rows = sorted(rows, key=lambda r: (r[column] is None, r[column]), reverse=order == Qt.SortOrder.DescendingOrder)
        self.endResetModel()

    def sort(self, column, order):
        if 0 <= column < len(self.headers):
            self.sorting_rows.emit()
            self.order = column, order
            self.replace(self.rows)
            self.sorted_rows.emit()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role):
        if role not in (Qt.ItemDataRole.UserRole, Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole) or not index.isValid():
            return
        value = self.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return value
        if self.headers[index.column()] == "Fraction of nucleus" and role == Qt.ItemDataRole.DisplayRole:
            return f"{value:.2%}"
        if isinstance(value, tuple):
            return ", ".join(map(str, value))
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, float):
            return f"{value:.6g}"
        return "—" if value is None else str(value)


def inspection_table(layout, title, headers):
    view = QTableView()
    view.setAccessibleName(title)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.verticalHeader().hide()
    view.setModel(InspectionTable(headers, view))
    layout.addWidget(view)
    return view


class AssociationExplorer(QWidget):
    navigated = Signal()
    selection_changed = Signal()

    def __init__(self):
        super().__init__()
        self.index = None
        self.selection = None
        self.awaiting_choice = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.multiple = QCheckBox("Only multiple associations")
        self.multiple.setChecked(True)
        layout.addWidget(self.multiple)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.objects = inspection_table(layout, "Automatically linked cytoplasmic objects", (
            "Cyto ID", "Accepted nuclei", "Associated nuclear IDs", "Overlapping candidates", "Volume (µm³)"))
        self.objects.setSortingEnabled(True)
        self.objects.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        nav = QHBoxLayout()
        layout.addLayout(nav)
        self.previous = QPushButton("Previous Object")
        self.next = QPushButton("Next Object")
        for button in (self.previous, self.next):
            nav.addWidget(button)
        layout.addWidget(QLabel("Overlapping nuclear candidates"))
        self.candidates = inspection_table(layout, "Overlapping nuclear candidates", (
            "Nucleus ID", "Automatic class", "Auto-linked cyto ID", "Intersection (voxels)",
            "Intersection (µm³)", "Fraction of nucleus", "Accepted here"))
        self.candidates.setSortingEnabled(True)
        self.candidates.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.candidates.model().sorted_rows.connect(self._restore_candidate)
        self.previous_nucleus = QPushButton("Previous Nucleus")
        self.next_nucleus = QPushButton("Next Nucleus")
        for control, delta in ((self.previous_nucleus, -1), (self.next_nucleus, 1)):
            layout.addWidget(control)
            control.clicked.connect(lambda _, d=delta: self.candidates.selectRow(self.candidates.currentIndex().row() + d))
        self.follow = QCheckBox("Follow selected object")
        self.follow.setChecked(True)
        layout.addWidget(self.follow)
        self.selection_changed.connect(self._candidate_controls)
        self.focus = QPushButton("Focus Selected Object")
        self.review = QPushButton("Review in Proofreading")
        self.isolate = QPushButton("Isolate Associated Masks")
        self.isolate.setToolTip("Show the selected cytoplasmic object and its accepted nuclei.")
        self.include = QCheckBox("Include other overlapping nuclei")
        self.restore = QPushButton("Show All Masks")
        for control in (self.focus, self.review, self.isolate, self.include, self.restore):
            layout.addWidget(control)

        self.multiple.toggled.connect(self._filter)
        self.objects.model().sorted_rows.connect(self._restore_row)
        self.objects.selectionModel().currentRowChanged.connect(self._object_changed)
        self.objects.clicked.connect(self._object_clicked)
        self.candidates.selectionModel().currentRowChanged.connect(self._candidate_changed)
        self.previous.clicked.connect(lambda: self._navigate(-1))
        self.next.clicked.connect(lambda: self._navigate(1))
        self._candidate_controls()
        self._controls(-1)
        self.status.setText("Run automatic matching to inspect associations.")

    def set_result(self, result, message):
        if result is None:
            self.status.setText(message)
            if self.index is None:
                return
        if result is not None and self.index is not None and result is self.index.result:
            return
        self.index = AssociationIndex(result) if result is not None else None
        self.awaiting_choice = False
        self.selection = None
        self._filter()

    def _filter(self):
        rows = self.index.objects.values() if self.index else ()
        minimum = 2 if self.multiple.isChecked() else 0
        self.objects.model().replace(r for r in rows if r.accepted_count >= minimum)
        self._restore_row()

    def clear_inspection(self, unavailable):
        self.awaiting_choice = unavailable
        self.objects.selectionModel().clear()
        self.selection = None
        self.candidates.model().replace(())
        self._controls(-1)
        self._candidate_controls()
        if unavailable:
            self.status.setText("Previous selection unavailable. Select a cytoplasmic object.")
        self.selection_changed.emit()

    def _restore_row(self):
        if self.awaiting_choice:
            self.clear_inspection(True)
            return
        rows = self.objects.model().rows
        if rows:
            selected = self.selection.cytoplasmic_id if self.selection else None
            pos = next((i for i, r in enumerate(rows) if r.label_id == selected), 0)
            with QSignalBlocker(self.objects.selectionModel()):
                self.objects.selectRow(pos)
            self._show_object(pos)
        else:
            self.selection = None
            self.candidates.model().replace(())
            if self.index:
                message = ("No multiple associations." if self.multiple.isChecked()
                           else "No automatically linked cytoplasmic objects.")
                result = self.index.result
                if not result.nuclei or not result.associations:
                    message += (f" Current result contains {len(result.nuclei):,} retained nuclei and "
                                f"{len(result.associations):,} retained cytoplasmic objects.")
                self.status.setText(message)
            self._controls(-1)
            self.selection_changed.emit()

    def _controls(self, pos):
        self.previous.setEnabled(pos > 0)
        self.next.setEnabled(0 <= pos < self.objects.model().rowCount() - 1)
        for control in (self.focus, self.review, self.isolate, self.include):
            control.setEnabled(pos >= 0)
        self.restore.setEnabled(False)  # The display enables this during isolation.

    def _restore_candidate(self):
        if self.selection and self.selection.focus_role == "nuclear":
            rows = self.candidates.model().rows
            pos = next((i for i, row in enumerate(rows) if row.label_id == self.selection.focus_id), None)
            if pos is not None:
                with QSignalBlocker(self.candidates.selectionModel()):
                    self.candidates.selectRow(pos)
        self._candidate_controls()

    def _candidate_controls(self):
        row = self.candidates.currentIndex().row()
        self.previous_nucleus.setEnabled(row > 0)
        self.next_nucleus.setEnabled(row + 1 < self.candidates.model().rowCount())

    def _navigate(self, delta):
        pos = self.objects.currentIndex().row() + delta
        if 0 <= pos < self.objects.model().rowCount():
            self.objects.selectRow(pos)

    def _object_changed(self, current, previous):
        if current.isValid():
            self._show_object(current.row())
            self.navigated.emit()

    def focus_parent(self):
        if self.selection and self.selection.focus_role == "nuclear":
            self.candidates.setCurrentIndex(QModelIndex())
            self.candidates.clearSelection()
            self._select("cytoplasmic", self.selection.cytoplasmic_id)

    def _object_clicked(self, index):
        self.focus_parent()
        if self.selection:
            self.navigated.emit()

    def _show_object(self, pos):
        self.awaiting_choice = False
        cyto = self.objects.model().rows[pos].label_id
        self._controls(pos)
        self.status.setText(f"Cytoplasmic ID {cyto} · {pos + 1} of {self.objects.model().rowCount()}")
        if self.selection and self.selection.cytoplasmic_id == cyto:
            self.selection_changed.emit()
            return
        self.candidates.model().replace(self.index.candidates(cyto))
        self._select("cytoplasmic", cyto)

    def _candidate_changed(self, current, previous):
        if current.isValid():
            self._select("nuclear", self.candidates.model().rows[current.row()].label_id)
            self.navigated.emit()

    def _select(self, role, label):
        cyto = self.objects.model().rows[self.objects.currentIndex().row()].label_id
        result = self.index.result
        if self.selection and self.selection.cytoplasmic_id == cyto:
            self.selection = replace(self.selection, focus_role=role, focus_id=label)
            self.selection_changed.emit()
            return
        self.selection = AssociationSelection(
            ref(result), result.statistics.population_identity, cyto, self.index.objects[cyto].associated_ids,
            tuple(row.label_id for row in self.candidates.model().rows if not row.accepted_here), role, label)
        self.selection_changed.emit()
