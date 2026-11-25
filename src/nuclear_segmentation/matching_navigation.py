"""Transient Matching navigation. Keep IDs and UI values, never masks or results."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SpatialView:
    dimensions: int
    order: tuple
    point: tuple
    center: tuple
    zoom: float
    angles: tuple

    @classmethod
    def capture(cls, viewer):
        dims, camera = viewer.dims, viewer.scene.camera
        return cls(dims.ndisplay, dims.order, dims.point, camera.center, camera.zoom, camera.angles)

    def restore(self, viewer):
        dims, camera = viewer.dims, viewer.scene.camera
        if len(self.order) == dims.ndim:
            dims.ndisplay = self.dimensions
            dims.order = self.order
            for axis in dims.not_displayed:
                dims.set_point(axis, self.point[axis])
            camera.angles = self.angles
            camera.center = self.center
            camera.zoom = self.zoom


def sources(result):
    grid, nuclear, cyto = result.statistics.population_identity
    return grid, nuclear[0], cyto[0]


def result_key(result):
    _, nuclear, cyto = sources(result)
    return nuclear.project_id, nuclear.session_id, cyto.session_id


def tables(panel):
    return (panel.table, panel.explorer.objects, panel.explorer.candidates,
            panel.candidate_explorer.objects, panel.candidate_explorer.candidates)


def row_for(table, label):
    return next((i for i, row in enumerate(table.model().rows) if row[0] == label), None)


@dataclass(frozen=True)
class TableView:
    column: int
    order: object
    vertical: int
    horizontal: int

    @classmethod
    def capture(cls, table):
        header = table.horizontalHeader()
        return cls(header.sortIndicatorSection(), header.sortIndicatorOrder(),
                   table.verticalScrollBar().value(), table.horizontalScrollBar().value())

    def scroll(self, table):
        table.doItemsLayout()
        table.verticalScrollBar().setValue(self.vertical)
        table.horizontalScrollBar().setValue(self.horizontal)


@dataclass(frozen=True)
class ExplorerPreferences:
    mode: int
    parent_count: int
    include: bool
    follow_parent: bool
    multiple_nuclei: bool
    class_filter: str
    follow_nucleus: bool
    pick_target: int
    tables: tuple
    nucleus_count: int
    review_state: int
    overlap_percent: float

    @classmethod
    def capture(cls, panel):
        a, c = panel.explorer, panel.candidate_explorer
        return cls(panel.explorer_mode.currentIndex(), a.accepted.currentIndex(), a.include.isChecked(),
                   a.follow.isChecked(), c.multiple.isChecked(), c.classes.currentText(), c.follow.isChecked(),
                   panel.pick_target.currentIndex(), tuple(TableView.capture(t) for t in tables(panel)),
                   c.accepted.currentIndex(), c.review_filter.currentIndex(), panel.display_overlap.value())

    def apply(self, panel):
        a, c = panel.explorer, panel.candidate_explorer
        panel.explorer_mode.setCurrentIndex(self.mode)
        panel.pick_target.setCurrentIndex(self.pick_target)
        a.accepted.setCurrentIndex(self.parent_count)
        c.accepted.setCurrentIndex(self.nucleus_count)
        c.review_filter.setCurrentIndex(self.review_state)
        panel.display_overlap.setValue(self.overlap_percent)
        a.include.setChecked(self.include)
        a.follow.setChecked(self.follow_parent)
        c.multiple.setChecked(self.multiple_nuclei)
        c.classes.setCurrentText(self.class_filter)
        c.follow.setChecked(self.follow_nucleus)
        for view, table in zip(self.tables, tables(panel), strict=True):
            table.sortByColumn(view.column, view.order)


@dataclass(frozen=True)
class InspectionLocation:
    revisions: tuple
    parent: int | None
    child: int | None
    nucleus: int | None
    candidates: tuple
    candidate_focus: int | None
    isolate_parent: bool
    isolate_nucleus: bool
    preferences: ExplorerPreferences
    view: SpatialView

    @property
    def inspected_nucleus(self):
        return self.nucleus if self.preferences.mode else self.child

    @classmethod
    def capture(cls, display):
        p = display.panel
        a, c = p.explorer, p.candidate_explorer
        s, merge = a.selection, c.selection()
        return cls(sources(a.index.result), s.cytoplasmic_id if s else None,
                   s.focus_id if s and s.focus_role == "nuclear" else None,
                   c.nucleus, merge.ids if merge else (), c.focus_id if c.focus_role == "cytoplasmic" else None,
                   bool(display.inspection.appearance), c.isolate.isChecked(), ExplorerPreferences.capture(p),
                   SpatialView.capture(display.viewer))

    def restore(self, display, result):
        a, c = display.panel.explorer, display.panel.candidate_explorer
        self.preferences.apply(display.panel)
        compatible = sources(result) == self.revisions
        parent_pos = row_for(a.objects, self.parent) if compatible else None
        if parent_pos is not None:
            a.objects.selectRow(parent_pos)
            a.focus_parent()
            if self.child is not None:
                child_pos = row_for(a.candidates, self.child)
                if child_pos is None:
                    parent_pos = None
                else:
                    a.candidates.selectRow(child_pos)
        if parent_pos is None:
            a.clear_inspection(self.parent is not None)

        nucleus_pos = row_for(c.objects, self.nucleus) if compatible else None
        ids = c.by_nucleus.get(self.nucleus, {})
        if not all(i in ids for i in self.candidates) or (self.candidate_focus is not None and self.candidate_focus not in ids):
            nucleus_pos = None
        if nucleus_pos is not None:
            c.objects.selectRow(nucleus_pos)
            c.restore_candidates(self.candidates, self.candidate_focus)
        else:
            c.clear_inspection(self.nucleus is not None)
        c.isolate.setChecked(self.isolate_nucleus and nucleus_pos is not None)
        if not self.preferences.mode and self.isolate_parent and parent_pos is not None:
            display.inspection.apply_isolation()
        restored = nucleus_pos is not None if self.preferences.mode else parent_pos is not None
        if restored:
            self.view.restore(display.viewer)
        return restored

    def scroll(self, panel):
        for view, table in zip(self.preferences.tables, tables(panel), strict=True):
            view.scroll(table)
