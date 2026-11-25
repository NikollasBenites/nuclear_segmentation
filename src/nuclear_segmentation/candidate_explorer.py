"""Inspect sparse overlaps from one nucleus to multiple cytoplasmic objects."""
from collections import defaultdict
from dataclasses import dataclass
from weakref import ref

from qtpy.QtCore import QItemSelectionModel, QSignalBlocker, Signal
from qtpy.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QLabel, QPushButton,
                            QVBoxLayout, QWidget)

from .matching_explorer import ResultSelection, inspection_table


@dataclass(frozen=True)
class MergeSelection(ResultSelection):
    nucleus: int
    ids: tuple[int, ...]

    def is_current(self, pair):
        if not super().is_current(pair):
            return False
        stats = self.source().statistics
        return (len(self.ids) >= 2 and len(set(self.ids)) == len(self.ids) and
                all(stats.intersections.get((self.nucleus, cyto), 0) > 0 for cyto in self.ids))

    def summary(self, review):
        result = self.source()
        stats = result.statistics
        count = sum(stats.intersections[self.nucleus, cyto] for cyto in self.ids)
        volume = sum(stats.cytoplasmic_voxels[cyto] for cyto in self.ids) * stats.voxel_volume_um3
        others = {n for n, c in stats.intersections if c in self.ids}
        sessions = review.key(None)[:2]
        for (nuclear_session, cyto_session, nucleus), record in review.records.items():
            if ((nuclear_session, cyto_session) == sessions and record["cytoplasmic_id"] in self.ids
                    and review.status(result, nucleus) == "Matched"):
                others.add(nucleus)
        others.discard(self.nucleus)
        total = stats.nuclear_voxels[self.nucleus]
        message = f"Combined volume {volume:.6g} µm³ · nuclear overlap {count}/{total} voxels ({count/total:.2%})."
        if others:
            message += "\nOther retained nuclei linked to or overlapping these objects: " + ", ".join(map(str, sorted(others)))
        return message


class CandidateExplorer(QWidget):
    changed = Signal()
    focus_requested = Signal()
    navigated = Signal()

    def __init__(self):
        super().__init__()
        self.result = self.nucleus = None
        self.by_nucleus = {}
        self.focus_role = "nuclear"
        form = QVBoxLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.classes = QComboBox()
        self.classes.addItems(["All classes", "MATCH", "AMBIGUOUS", "NO_MATCH"])
        form.addWidget(self.classes)
        self.multiple = QCheckBox("Only multiple candidates")
        self.multiple.setChecked(True)
        form.addWidget(self.multiple)
        self.objects = inspection_table(form, "Retained nuclei",
                ("Nucleus ID", "Automatic class", "Candidate count", "Candidate IDs"))
        self.objects.setSortingEnabled(True)
        self._navigation(form, self.objects, "Nucleus")
        instruction = QLabel("Select candidate rows for merge review")
        instruction.setWordWrap(True)
        form.addWidget(instruction)
        self.candidates = inspection_table(form, "Cytoplasmic candidates",
                ("Cyto ID", "Intersection (voxels)", "Fraction of nucleus", "Cyto volume (µm³)", "Other auto-linked nuclei"))
        self.candidates.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.candidates.setSortingEnabled(True)
        self.candidates.model().sorting_rows.connect(self._save_candidate_selection)
        self.candidates.model().sorted_rows.connect(self._restore_candidates)
        self._navigation(form, self.candidates, "Object")
        self.focus = QPushButton("Focus Selected Object")
        self.focus.clicked.connect(lambda: self.focus_requested.emit())
        self.follow = QCheckBox("Follow selected object")
        self.follow.setChecked(True)
        self.isolate = QCheckBox("Isolate nucleus and candidates")
        self.summary = QLabel("Run automatic matching to inspect candidate masks.")
        self.summary.setWordWrap(True)
        self.review = QPushButton("Review Merge in Proofreading")
        for widget in (self.follow, self.focus, self.isolate, self.summary, self.review):
            form.addWidget(widget)
        self.review.setEnabled(False)
        self.multiple.toggled.connect(self._filter)
        self.classes.currentIndexChanged.connect(self._filter)
        self.objects.model().sorted_rows.connect(self._restore_nucleus)
        self.objects.selectionModel().currentRowChanged.connect(self._navigate_object)
        self.objects.clicked.connect(self._navigate_object)
        self.candidates.selectionModel().currentRowChanged.connect(self._candidate_changed)
        self.candidates.clicked.connect(self._candidate_changed)
        self.candidates.selectionModel().selectionChanged.connect(self._selection_changed)
        self.isolate.toggled.connect(lambda: self.changed.emit())

    @staticmethod
    def _navigation(form, view, noun):
        for text, delta in (("Previous", -1), ("Next", 1)):
            button = QPushButton(f"{text} {noun}")
            button.clicked.connect(lambda _, d=delta: view.selectRow(view.currentIndex().row() + d))
            form.addWidget(button)

    def set_result(self, result, review):
        if result is self.result:
            return
        self.result, self.decisions = result, review
        self.by_nucleus = defaultdict(dict)
        self.linked = defaultdict(list)
        self.nucleus = None
        if result:
            for row in result.nuclei:
                if row.accepted_cytoplasmic_id is not None:
                    self.linked[row.accepted_cytoplasmic_id].append(row.nuclear_id)
            for (nucleus, cyto), count in result.statistics.intersections.items():
                self.by_nucleus[nucleus][cyto] = count
        self._filter()

    def _filter(self):
        rows = []
        for r in self.result.nuclei if self.result else ():
            ids = self.by_nucleus[r.nuclear_id]
            if (not self.multiple.isChecked() or len(ids) >= 2) and self.classes.currentText() in ("All classes", r.auto_class):
                rows.append((r.nuclear_id, r.auto_class, len(ids), tuple(sorted(ids))))
        self.objects.model().replace(rows)
        self._restore_nucleus()

    def _restore_nucleus(self):
        rows = self.objects.model().rows
        pos = next((i for i, r in enumerate(rows) if r[0] == self.nucleus), 0)
        with QSignalBlocker(self.objects.selectionModel()):
            self.objects.selectRow(pos)
        self._object_changed(self.objects.currentIndex())

    def _navigate_object(self, index):
        if index.isValid():
            self._object_changed(index)
            self.navigated.emit()

    def _object_changed(self, index):
        self.focus_role = "nuclear"
        nucleus = self.objects.model().rows[index.row()][0] if index.isValid() else None
        if nucleus is not None and self.nucleus == nucleus:
            self.changed.emit()
            return
        self.nucleus = nucleus
        rows = []
        if self.nucleus is not None:
            stats = self.result.statistics
            for cyto, count in self.by_nucleus[self.nucleus].items():
                linked = self.linked[cyto]
                rows.append((cyto, count, count / stats.nuclear_voxels[self.nucleus],
                             stats.cytoplasmic_voxels[cyto] * stats.voxel_volume_um3,
                             tuple(n for n in linked if n != self.nucleus)))
        self.candidates.model().replace(rows)
        self._selection_changed()

    def _save_candidate_selection(self):
        selection = self.selection()
        self._sorted_ids = selection.ids if selection else ()
        self._sorted_focus = self.focus_role, self.focus_id

    def _restore_candidates(self):
        model = self.candidates.model()
        selection = self.candidates.selectionModel()
        select = QItemSelectionModel.SelectionFlag
        self.focus_role, focus_id = self._sorted_focus
        with QSignalBlocker(selection):
            for i, row in enumerate(model.rows):
                if row[0] in self._sorted_ids:
                    selection.select(model.index(i, 0), select.Select | select.Rows)
                if self.focus_role == "cytoplasmic" and row[0] == focus_id:
                    selection.setCurrentIndex(model.index(i, 0), select.NoUpdate)
        self._selection_changed()

    def _candidate_changed(self):
        self.focus_role = "cytoplasmic" if self.candidates.currentIndex().isValid() else "nuclear"
        self.changed.emit()
        if self.candidates.currentIndex().isValid():
            self.navigated.emit()

    @property
    def focus_id(self):
        index = self.candidates.currentIndex()
        if self.focus_role == "cytoplasmic" and index.isValid():
            return self.candidates.model().rows[index.row()][0]
        return self.nucleus

    def selection(self):
        if self.nucleus is None:
            return None
        ids = tuple(sorted(self.candidates.model().rows[i.row()][0]
                           for i in self.candidates.selectionModel().selectedRows()))
        return MergeSelection(ref(self.result), self.result.statistics.population_identity, self.nucleus, ids)

    def _selection_changed(self):
        selection = self.selection()
        self.review.setEnabled(bool(selection and len(selection.ids) >= 2))
        if selection and selection.ids:
            message = selection.summary(self.decisions)
        elif selection:
            count = len(self.by_nucleus[self.nucleus])
            message = (f"Nucleus {self.nucleus} · {count} overlapping cytoplasmic "
                       + ("candidate" if count == 1 else "candidates"))
            if count >= 2:
                message += ". Select two or more to review a possible merge."
        else:
            message = "No nuclei in this view."
        self.summary.setText(message)
        self.changed.emit()
