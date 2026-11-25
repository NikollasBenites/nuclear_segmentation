"""Proofreading controls for association decisions on retained scientific objects."""
from qtpy.QtCore import QSignalBlocker
from qtpy.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .association_review import STATES
from .collapsible_section import CollapsibleSection
from .label_geometry import selected_bounds
from .matching_inspection import center_on_voxel, focus_voxel
from .matching_pair import MATCHING_LAYER


class AssociationPanel(CollapsibleSection):
    def __init__(self, window):
        super().__init__("association_review", "Manual Association Review", expanded=False)
        self.window = window
        self.state = window.project_state
        self.result = None
        self.active = False
        self.layers = []
        self.isolated = False
        form = QVBoxLayout()
        self.set_content_layout(form)
        self.message = QLabel()
        self.message.setWordWrap(True)
        form.addWidget(self.message)
        def combo(title, values=()):
            form.addWidget(QLabel(title))
            control = QComboBox()
            control.addItems(values)
            form.addWidget(control)
            return control
        def button(title, callback):
            control = QPushButton(title)
            form.addWidget(control)
            control.clicked.connect(callback)
            return control
        self.classes = combo("Automatic classification", ["All", "MATCH", "AMBIGUOUS", "NO_MATCH"])
        self.filter = combo("Review status", ["All", *STATES, "Requires Review"])
        self.nuclei = combo("Nuclear object")
        button("Previous nucleus", lambda: self.nuclei.setCurrentIndex(max(0, self.nuclei.currentIndex()-1)))
        button("Next nucleus", lambda: self.nuclei.setCurrentIndex(min(self.nuclei.count()-1, self.nuclei.currentIndex()+1)))
        self.targets = combo("Cytoplasmic candidate or retained ID")
        self.targets.setEditable(True)
        button("Focus nucleus", lambda: self.inspect(False, False))
        button("Focus target", lambda: self.inspect(True, False))
        button("Isolate nucleus and target", lambda: self.inspect(False, True))
        button("Restore masks", self.restore)
        self.decision = combo("Manual decision", STATES)
        self.comment = QLineEdit()
        self.comment.setPlaceholderText("Reviewer comment")
        form.addWidget(self.comment)
        button("Confirm automatic match", lambda: self.save(True))
        button("Record manual decision", lambda: self.save(False))
        button("Undo last decision", self.undo)
        self.classes.currentIndexChanged.connect(self.populate)
        self.filter.currentIndexChanged.connect(self.populate)
        self.nuclei.currentIndexChanged.connect(self.show_object)
        self.targets.currentTextChanged.connect(self.restore)
        for signal in (self.state.matching_result_changed, self.state.matching_selection_changed,
                       self.state.sessions_changed, self.state.inactive_session_validity_changed,
                       self.state.segmentation_result_changed, self.state.filtering_result_changed,
                       self.state.calibration_changed, self.state.changed):
            signal.connect(self.refresh)
        self.state.association_review_changed.connect(self.populate)

    @property
    def review(self):
        return self.state.matching_pair.review

    def refresh(self, *args):
        current = self.state.matching_pair.current_result if self.active else None
        if current is not self.result or not self.nuclei.count():
            self.result = current
            self.populate()

    def populate(self, *args):
        self.restore()
        selected = self.nuclei.currentText()
        with QSignalBlocker(self.nuclei):
            self.nuclei.clear()
            for row in self.result.nuclei if self.result else ():
                status = self.review.status(self.result, row.nuclear_id)
                if self.classes.currentText() in ("All", row.auto_class) and self.filter.currentText() in ("All", status):
                    self.nuclei.addItem(str(row.nuclear_id))
            index = self.nuclei.findText(selected)
            if index >= 0:
                self.nuclei.setCurrentIndex(index)
        pair = self.review.key(None)[:2]
        self.absent_count = sum(1 for key in self.review.records if key[:2] == pair
                                and self.result and key[2] not in self.result.statistics.nuclear_voxels)
        self.show_object()

    def show_object(self, *args):
        self.restore()
        self.targets.clear()
        self.comment.clear()
        self.decision.setCurrentIndex(0)
        absent = (f" · {self.absent_count} historical decisions on absent nuclei require review"
                  if getattr(self, "absent_count", 0) else "")
        if not self.nuclei.currentText():
            self.message.setText(("No nuclei in this view." + absent) if self.result else "Run automatic matching for a measured pair.")
            return
        label = int(self.nuclei.currentText())
        row = next(r for r in self.result.nuclei if r.nuclear_id == label)
        counts = sorted(((count, c) for (n,c),count in self.result.statistics.intersections.items() if n == label),
                        key=lambda item: (-item[0], item[1]))
        self.targets.addItems([str(c) for _,c in counts])
        record = self.review.records.get(self.review.key(label))
        status = self.review.status(self.result, label)
        self.message.setText(f"Automatic {row.auto_class} · best {row.best_cytoplasmic_id} · manual {status}{absent}")
        if record:
            self.comment.setText(record["comment"])
            if status != "Requires Review":
                self.decision.setCurrentText(status)
                if record["cytoplasmic_id"] is not None:
                    self.targets.setCurrentText(str(record["cytoplasmic_id"]))

    def selection(self):
        if (not self.window._project_idle() or self.window._project_job is not None or self.result is None
                or self.state.matching_pair.current_result is not self.result or not self.nuclei.currentText()):
            raise ValueError("Finish active operations and select a current matching result and nucleus.")
        return int(self.nuclei.currentText())

    def save(self, confirm):
        try:
            label = self.selection()
            status = "Matched" if confirm else self.decision.currentText()
            target = None
            if confirm:
                target = next(r.accepted_cytoplasmic_id for r in self.result.nuclei if r.nuclear_id == label)
            elif status == "Matched":
                target = int(self.targets.currentText())
            self.review.decide(self.result, label, status, target, self.comment.text())
            self.state.association_review_changed.emit()
        except ValueError as error:
            self.message.setText(str(error))

    def undo(self):
        try:
            if not self.window._project_idle() or self.window._project_job is not None:
                raise ValueError("Finish the active operation before undo.")
            self.review.undo()
            self.state.association_review_changed.emit()
        except ValueError as error:
            self.message.setText(str(error))

    def restore(self, *args):
        viewer = self.window.embedding.viewer
        for layer in self.layers:
            if layer in viewer.layers:
                viewer.layers.remove(layer)
        self.layers.clear()
        if self.isolated:
            self.isolated = False
            self.window.mask_display.resume()

    def inspect(self, target, isolate):
        self.restore()
        try:
            label = self.selection()
            pair = self.state.matching_pair.capture()
            viewer = self.window.embedding.viewer
            roles = [("nuclear", label)]
            if target or isolate and self.targets.currentText():
                cyto = int(self.targets.currentText())
                if cyto not in self.result.associations:
                    raise ValueError("Enter a retained cytoplasmic ID.")
                roles.append(("cytoplasmic", cyto))
            if isolate:
                self.window.mask_display.suspend(keep_hidden=True)
                self.isolated = True
            for role, object_id in roles:
                data = getattr(pair, role).labels
                low, high = selected_bounds(data, object_id)
                box = tuple(slice(a,b) for a,b in zip(low, high))
                support = (data[box] == object_id).astype("uint8")
                origin = low * pair.grid.spacing_zyx_um + pair.grid.translation_zyx_um
                layer = viewer.add_labels(support, name=f"Review · {role} {object_id}", translate=origin,
                                          scale=pair.grid.spacing_zyx_um, metadata={MATCHING_LAYER:role},
                                          colormap={None:"transparent", 0:"transparent",
                                                    1:"cyan" if role == "nuclear" else "orange"})
                layer.editable = False
                self.layers.append(layer)
                if role == ("cytoplasmic" if target else "nuclear"):
                    center_on_voxel(viewer, layer, focus_voxel(support, 1, None))
        except ValueError as error:
            self.restore()
            self.message.setText(str(error))
