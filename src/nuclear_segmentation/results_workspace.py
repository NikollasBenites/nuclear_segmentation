"""Persistent views of controller-owned results in the horizontal inspection area."""
from qtpy.QtCore import QAbstractTableModel, QModelIndex, Qt
from qtpy.QtWidgets import (QAbstractItemView, QDialog, QListView,
                            QPlainTextEdit, QTableView, QTabWidget, QVBoxLayout, QWidget)


class MeasurementModel(QAbstractTableModel):
    """Expose the published DataFrame by reference, including its exact object index."""
    def __init__(self, parent):
        super().__init__(parent)
        self.frame = None
        self.order = None

    def set_frame(self, frame):
        if self.frame is frame:
            return
        self.beginResetModel()
        self.frame = frame
        self.order = None
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.frame is None else len(self.frame)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.frame is None else len(self.frame.columns) + 1

    def headerData(self, section, orientation, role):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal and self.frame is not None:
            return str(self.frame.columns[section - 1]) if section else "Object ID"
        return super().headerData(section, orientation, role)

    def data(self, index, role):
        if role == Qt.ItemDataRole.DisplayRole and index.isValid():
            row = index.row() if self.order is None else self.order[index.row()]
            return str(self.frame.iat[row, index.column()-1] if index.column() else self.frame.index[row])

    def sort(self, column, order):
        if self.frame is None or not 0 <= column < self.columnCount():
            return
        values = self.frame.iloc[:, column - 1] if column else self.frame.index.to_series()
        labels = values.sort_values(ascending=order == Qt.SortOrder.AscendingOrder, kind="stable").index
        self.beginResetModel()
        self.order = self.frame.index.get_indexer(labels)
        self.endResetModel()


class GraphWindow(QDialog):
    def closeEvent(self, event):
        super().closeEvent(event)
        if hasattr(self.parent(), "results"):
            self.parent().results.dock_graph()


class ResultsWorkspace(QTabWidget):
    def __init__(self, window):
        super().__init__()
        self.setAccessibleName("Results and data inspection")
        self.setMinimumHeight(80)
        self.window = window
        self.pages = {}
        self.applicable = {}
        self.last_tab = {}
        self.stage = None
        def page(key, title, widget, stages):
            self.pages[key] = widget
            self.applicable[key] = stages.split()
            self.addTab(widget, title)

        state, context = window.project_state, window.context_panel
        measurement = QTableView()
        self.measurement_model = MeasurementModel(measurement)
        measurement.setModel(self.measurement_model)
        measurement.setSortingEnabled(True)
        measurement.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        measurement.verticalHeader().hide()
        page("measurements", "Measurements", measurement, "mask_review segmentation quantification proofreading")
        matching = context.matching_page
        page("associations", "Automatic associations", matching.table, "matching")
        self.objects, self.candidates = QTabWidget(), QTabWidget()
        for ui, title in ((matching.explorer, "Cytoplasmic"), (matching.candidate_explorer, "Nuclear")):
            self.objects.addTab(ui.objects, title)
            self.candidates.addTab(ui.candidates, title)
        for tabs in (self.objects, self.candidates):
            tabs.tabBar().hide()
            matching.explorer_mode.currentIndexChanged.connect(tabs.setCurrentIndex)
        page("objects", "Retained objects", self.objects, "matching")
        page("candidates", "Candidates", self.candidates, "matching")

        self.graph_page = QWidget()
        self.graph_layout = QVBoxLayout(self.graph_page)
        self.graph_layout.setContentsMargins(0, 0, 0, 0)
        self.graphs = context.quality_control_page
        self.graph_layout.addWidget(self.graphs)
        page("graphs", "QC graphs", self.graph_page, "quality_control matching")
        history = QListView()
        history.setModel(window.geometry_panel.history.model())
        page("history", "Edit history", history, "proofreading mask_review")
        self.sampling = QPlainTextEdit()
        self.sampling.setReadOnly(True)
        page("sampling", "Sampling summary", self.sampling, "quantification mask_review")
        for key, title, editor, stages in (
            ("execution", "Execution log", context.segmentation_page.execution_log, "segmentation project preprocessing"),
            ("export", "Scientific export", context.export_page.scientific_results.text, "export"),
            ("images", "Image export", context.export_page.results.text, "export"),
        ):
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setDocument(editor.document())
            page(key, title, view, stages)

        self.currentChanged.connect(self._remember)
        for signal in (state.changed, state.active_session_changed, state.filtering_result_changed,
                       state.quantification_result_changed, state.scientific_roi_changed):
            signal.connect(self.refresh)
        self.refresh()

    def refresh(self, *args):
        state = self.window.project_state
        frame = (state.filtering_result or {}).get("properties") if state.filtering_is_current else None
        self.measurement_model.set_frame(frame)
        panel = self.window.context_panel.quantification_page
        text = panel.status.text()
        if state.quantification_is_current:
            text = "\n".join([text, *(f"{label.parentWidget().layout().labelForField(label).text()}   {label.text()}"
                                      for label in (*panel.roi_values.values(), *panel.density_values.values()))])
        if text != self.sampling.toPlainText():
            self.sampling.setPlainText(text)

    def _remember(self, index):
        if self.stage and index >= 0:
            self.last_tab[self.stage] = self.tabText(index)

    def set_stage(self, stage, preferred=""):
        if self.stage == stage and not preferred:
            return
        wanted = preferred or self.last_tab.get(stage, "")
        self.stage = None
        first = None
        for key, widget in self.pages.items():
            i = self.indexOf(widget)
            visible = stage in self.applicable[key]
            self.setTabVisible(i, visible)
            if visible and (first is None or self.tabText(i) == wanted):
                first = i
        if first is not None:
            self.setCurrentIndex(first)
        self.stage = stage
        self._remember(self.currentIndex())
        self.refresh()

    def dock_graph(self):
        if self.graphs.parentWidget() is not self.graph_page:
            self.graph_layout.addWidget(self.graphs)
            self.graphs.show()

    def detach_graph(self):
        dialog = self.window.graph_window
        if self.graphs.parentWidget() is not dialog:
            dialog.layout().addWidget(self.graphs)
        self.graphs.show()
        dialog.show()
        dialog.raise_()

