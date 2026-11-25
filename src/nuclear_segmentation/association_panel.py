"""Proofreading controls for association decisions on retained scientific objects."""
from qtpy.QtCore import QSignalBlocker
from qtpy.QtWidgets import QCheckBox, QSpinBox, QComboBox, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .association_review import STATES
from .collapsible_section import CollapsibleSection
from .matching_inspection import add_population, focus_population
from .matching_appearance import LEGEND, MaskAppearance, appearance_control, relationships
from .matching_explorer import AssociationIndex
from .matching_pair import MATCHING_ROLES


class AssociationPanel(CollapsibleSection):
    def __init__(self, window):
        super().__init__("association_review", "Manual Association Review", expanded=False)
        self.window = window
        self.state = window.project_state
        self.result = None
        self.active = False
        self.renderer = MaskAppearance(window.embedding.viewer, self.state)
        self.index = None
        self.focus_role = "nuclear"
        self.styles = {}
        self.layers = []
        self.previous_layer = None
        self.display_result = None
        form = QVBoxLayout()
        self.set_content_layout(form)
        self.message = QLabel()
        self.message.setWordWrap(True)
        legend = QLabel(LEGEND)
        legend.setWordWrap(True)
        form.addWidget(legend)
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
        self.previous = button("Previous nucleus", lambda: self.nuclei.setCurrentIndex(max(0, self.nuclei.currentIndex()-1)))
        self.next = button("Next nucleus", lambda: self.nuclei.setCurrentIndex(min(self.nuclei.count()-1, self.nuclei.currentIndex()+1)))
        self.targets = combo("Cytoplasmic candidate or retained ID")
        self.targets.setEditable(True)
        self.follow = QCheckBox("Follow selected object")
        self.follow.setChecked(True)
        form.addWidget(self.follow)
        self.visibility, self.opacity = {}, {}
        for role in MATCHING_ROLES:
            self.styles[role] = appearance_control(form, role)
            self.styles[role].currentIndexChanged.connect(self._render)
            visible = self.visibility[role] = QCheckBox(f"Show {role} masks")
            visible.setChecked(True)
            form.addWidget(visible)
            opacity = self.opacity[role] = QSpinBox()
            opacity.setRange(0, 100)
            opacity.setValue(60)
            opacity.setPrefix(f"{role.title()} opacity ")
            opacity.setSuffix("%")
            form.addWidget(opacity)
            visible.toggled.connect(self._appearance)
            opacity.valueChanged.connect(self._appearance)
        button("Focus nucleus", lambda: self.inspect(False, self.isolation.isChecked()))
        button("Focus target", lambda: self.inspect(True, self.isolation.isChecked()))
        self.isolation = QCheckBox("Isolate selected nucleus and target")
        form.addWidget(self.isolation)
        self.isolation.toggled.connect(self._render)
        self.expanded_changed.connect(self._expanded_changed)
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
        self.targets.currentTextChanged.connect(self._target_changed)
        for signal in (self.state.matching_result_changed, self.state.matching_selection_changed,
                       self.state.sessions_changed, self.state.inactive_session_validity_changed,
                       self.state.segmentation_result_changed, self.state.filtering_result_changed,
                       self.state.calibration_changed, self.state.changed):
            signal.connect(self.refresh)
        self.state.association_review_changed.connect(self.populate)
        self.renderer.viewer.dims.events.order.connect(self._render)
        self.renderer.viewer.dims.events.ndisplay.connect(self._render)

    @property
    def review(self):
        return self.state.matching_pair.review

    def refresh(self, *args):
        current = self.state.matching_pair.current_result if self.active else None
        if current is not self.result or not self.nuclei.count():
            self.result = current
            self.index = AssociationIndex(current) if current else None
            self.populate()

    def populate(self, *args):
        if self.display_result is not self.result:
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

    def _target_changed(self):
        self.focus_role = "cytoplasmic"
        self._render(focus=self.focus_role if self.follow.isChecked() else None)

    def show_object(self, *args):
        self.focus_role = "nuclear"
        index = self.nuclei.currentIndex()
        self.previous.setEnabled(index > 0)
        self.next.setEnabled(0 <= index < self.nuclei.count() - 1)
        with QSignalBlocker(self.targets):
            self.targets.clear()
            self.comment.clear()
            self.decision.setCurrentIndex(0)
            absent = (f" · {self.absent_count} historical decisions on absent nuclei require review"
                      if getattr(self, "absent_count", 0) else "")
            if not self.nuclei.currentText():
                self.message.setText(("No nuclei in this view." + absent) if self.result else "Run automatic matching for a measured pair.")
                self._render()
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
        self._render(focus="nuclear" if self.follow.isChecked() else None)

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

    def _expanded_changed(self, _, expanded):
        if expanded:
            self._render(focus="nuclear" if self.follow.isChecked() else None)
        else:
            self.restore()

    def restore(self, *args):
        if self.display_result is None:
            return
        self.display_result = None
        self.renderer.clear()
        viewer = self.window.embedding.viewer
        for layer in self.layers:
            if layer in viewer.layers:
                viewer.layers.remove(layer)
        self.layers.clear()
        self.window.mask_display.resume()
        if self.previous_layer in viewer.layers and self.previous_layer.visible:
            viewer.layers.selection.active = self.previous_layer
        self.previous_layer = None

    def _appearance(self):
        for role, layer in zip(MATCHING_ROLES, self.layers):
            layer.visible = self.visibility[role].isChecked()
            layer.opacity = self.opacity[role].value() / 100

        self._render()

    def inspect(self, target, isolate):
        self.set_expanded(True, emit=False)
        with QSignalBlocker(self.isolation):
            self.isolation.setChecked(isolate)
        self.focus_role = "cytoplasmic" if target else "nuclear"
        self._render(focus=self.focus_role)

    def _render(self, *args, focus=None):
        if (not self.active or not self.expanded or self.window.geometry_panel.busy or self.result is None
                or self.result is not self.state.matching_pair.current_result):
            self.restore()
            return
        viewer = self.window.embedding.viewer
        if self.display_result is None:
            if self.window.mask_display.suspended:
                return
            pair = self.state.matching_pair.capture()
            self.previous_layer = viewer.layers.selection.active
            self.window.mask_display.suspend(keep_hidden=True)
            self.display_result = self.result
            for role in MATCHING_ROLES:
                self.layers.append(add_population(viewer, pair, role, f"Review · {role} masks",
                                                  self.visibility[role].isChecked(), self.opacity[role].value() / 100))
        try:
            target = int(self.targets.currentText())
        except ValueError:
            target = None
        if target not in self.result.associations:
            target = None
        nucleus = int(self.nuclei.currentText()) if self.nuclei.currentText() else None
        marks = relationships(self.index, "nuclear", nucleus) if nucleus is not None else {}
        if target is not None and self.focus_role == "cytoplasmic":
            marks = relationships(self.index, "cytoplasmic", target)
            if nucleus is not None and ("nuclear", nucleus) not in marks:
                marks["nuclear", nucleus] = "selected"
        elif target is not None and ("cytoplasmic", target) not in marks:
            marks["cytoplasmic", target] = "selected"
        isolated = None
        if self.isolation.isChecked():
            isolated = {role: {label for pop, label in marks if pop == role} for role in MATCHING_ROLES}
        layers = dict(zip(MATCHING_ROLES, self.layers, strict=True))
        message = f"Nucleus {nucleus} · {self.index.nuclei[nucleus].auto_class}" if nucleus else ""
        if target is not None and self.focus_role == "cytoplasmic":
            message += f"\nCytoplasmic object {target} · {self.index.objects[target].accepted_count} accepted nuclei"
        self.renderer.legend(message)
        self.renderer.render(layers, self.styles, self.index, True, marks, isolated)
        label = target if focus == "cytoplasmic" else nucleus
        if focus and label is not None:
            focus_population(viewer, self.state, layers[focus], label)
