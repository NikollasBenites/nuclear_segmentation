"""Explicit association handoff and read-only guided correction in Proofreading."""
from qtpy.QtCore import QThread
from qtpy.QtWidgets import QCheckBox, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .collapsible_section import CollapsibleSection
from .geometry_edit import editing_dtype, effective_masks, next_geometry_id
from .guided_split import candidate_records, propose, qualifying
from .label_geometry import measured_bounds
from .mask_selection import INTERNAL_HIGHLIGHT_KEY
from .matching_inspection import center_on_voxel, focus_voxel


class ProposalWorker(QThread):
    def __init__(self, owner, selection, segmentation, inputs):
        super().__init__(owner)
        self.selection = selection
        self.segmentation = segmentation
        self.inputs = inputs
        self.proposal = self.error = None

    def run(self):
        try:
            next_id = next_geometry_id(self.segmentation)
            seeds = qualifying(self.inputs["candidates"], self.inputs["threshold"])
            dtype = editing_dtype(effective_masks(self.segmentation).dtype, next_id + len(seeds) - 1)
            self.proposal = propose(**self.inputs, next_id=next_id, dtype=dtype)
        except Exception as error:
            self.error = str(error)


class GuidedPanel(CollapsibleSection):
    def __init__(self, geometry):
        super().__init__("guided_watershed", "Nuclear-guided Watershed")
        self.geometry = geometry
        self.window = geometry.window
        self.state = geometry.state
        self.selection = self.reference = self.worker = None
        self.candidates = []
        form = QVBoxLayout()
        self.set_content_layout(form)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addWidget(self.summary)
        form.addWidget(QLabel("Minimum seed overlap fraction"))
        self.threshold = QLineEdit("0.50")
        self.threshold.setAccessibleName("Minimum seed overlap fraction")
        form.addWidget(self.threshold)
        self.details = QLabel()
        self.details.setWordWrap(True)
        form.addWidget(self.details)
        self.preview = QPushButton("Preview Watershed")
        form.addWidget(self.preview)
        self.show_seeds = QCheckBox("Show nuclear seed voxels")
        self.show_seeds.hide()
        self.show_seeds.toggled.connect(self._show_reference)
        form.addWidget(self.show_seeds)
        self.threshold.textChanged.connect(self.describe)
        self.preview.clicked.connect(self.start)
        for signal in (self.state.matching_selection_changed, self.state.matching_result_changed,
                       self.state.inactive_session_validity_changed, self.state.calibration_changed,
                       self.state.active_session_changed):
            signal.connect(self.refresh)
        self.hide()

    def clear_context(self):
        self.selection = None
        self.candidates = []
        self.hide()

    def require_current(self, selection):
        if (selection is None or not selection.is_current(self.state.matching_pair)
                or selection.cytoplasmic_revision != self.state.session_revision()):
            raise ValueError("Source context changed. Return to Matching, rerun and select the objects again.")

    def idle(self):
        return (self.window._project_job is None and self.window._project_idle()
                and not self.window.matching_display.execution.busy
                and not self.window.context_panel.mask_review_page._filter_timer.isActive())

    def handoff(self):
        explorer = self.window.context_panel.matching_page.explorer
        selection = explorer.selection
        try:
            if not self.idle() or self.worker is not None:
                raise ValueError("Finish the active operation before reviewing this parent.")
            if selection is None or not selection.is_current(self.state.matching_pair):
                raise ValueError("Rerun Matching and select a current cytoplasmic parent.")
            self.window._select_session(selection.cytoplasmic_revision.session_id)
            self.require_current(selection)
            self.window.association_panel.set_expanded(False)
            self.window.set_workflow("proofreading")
            self.require_current(selection)
            layer = self.window.mask_display.layers.get("filtered")
            if layer is None or layer.data is not self.state.filtering_result["filtered_masks"]:
                self.window.filtering._create_layers(self.state.filtering_result)
            self.window.mask_display.show("filtered")
            layer = self.window.mask_display.layers["filtered"]
            parent = selection.cytoplasmic_id
            point = focus_voxel(layer.data, parent, self.state.filtering_result.get("properties"))
            if point is None:
                raise ValueError("Parent geometry is unavailable. Return to Matching and rerun.")
            self.window.viewer_toolbar.activate_tool("review")
            self.window.mask_selection.restore_selection(layer, parent)
            center_on_voxel(self.geometry.viewer, layer, point)
            self.selection = selection
            self.candidates = candidate_records(selection.source(), parent)
            source = self.state.sessions[selection.nuclear_revision.session_id]
            self.summary.setText(f"Parent {parent} · Nuclear source {source.display_name} · "
                                 f"Cytoplasmic target {self.state.active_session.display_name}")
            self.threshold.setText("0.50")
            self.describe()
            self.geometry.set_expanded(True)
            self.show()
        except ValueError as error:
            self.clear_context()
            self.window.statusBar().showMessage(str(error), 7000)
            explorer.status.setText(str(error))
            self.geometry.status.setText(str(error))

    def describe(self):
        try:
            seeds = set(qualifying(self.candidates, float(self.threshold.text())))
            self.details.setText("\n".join(
                f"ID {c['id']} · {c['classification']} · {c['intersection']}/{c['total']} voxels · "
                f"{c['fraction']:.4g} · {'eligible' if c['id'] in seeds else 'below threshold'}"
                for c in self.candidates) + ("" if len(seeds) >= 2 else
                "\nAt least two seeds must qualify. Lower the threshold or review another parent."))
            self.enough = len(seeds) >= 2
        except ValueError:
            self.enough = False
            self.details.setText("Enter a finite fraction greater than zero and at most one.")
        self.refresh()

    def refresh(self):
        if self.selection is None:
            return
        try:
            self.require_current(self.selection)
        except ValueError:
            self.clear_context()
            return
        ready = self.worker is None and not self.geometry.busy
        self.threshold.setEnabled(ready)
        self.preview.setEnabled(ready and getattr(self, "enough", False))

    def start(self):
        try:
            self.require_current(self.selection)
            if self.worker is not None or not self.idle():
                raise ValueError("Finish the active operation first.")
            pair = self.state.matching_pair.capture()
            result = self.state.segmentation_result
            parent = self.selection.cytoplasmic_id
            props = self.state.filtering_result.get("properties")
            bounds = measured_bounds(props, parent, pair.grid.shape_zyx)
            inputs = dict(target_labels=pair.cytoplasmic.labels, reference_labels=pair.nuclear.labels,
                          parent_id=parent, candidates=self.candidates, threshold=float(self.threshold.text()),
                          spacing=pair.grid.spacing_zyx_um, bounds=bounds,
                          parent_count=self.selection.source().statistics.cytoplasmic_voxels[parent])
            self.worker = ProposalWorker(self, self.selection, result, inputs)
            self.worker.finished.connect(self.finish)
            self.worker.start()
            self.geometry.status.setText("Calculating watershed…")
            self.refresh()
        except ValueError as error:
            self.geometry.status.setText(str(error))

    def finish(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        started = False
        panel = self.geometry
        try:
            if self.selection is not worker.selection or not self.idle():
                raise ValueError("Watershed context changed. Return to Matching and select the parent again.")
            self.require_current(worker.selection)
            if worker.error is not None:
                raise ValueError(worker.error)
            panel.scope.setCurrentIndex(0)
            proposal = worker.proposal
            panel.start(minimum_capacity=max(proposal.parameters["child_ids"]))
            started = True
            panel.edit.begin_guided(worker.selection, proposal)
            panel._pending_dirty = panel.strokes.changed_voxels
            panel.window.mask_selection.clear_selection(panel.layer)
            panel._set_brush(None)
            panel.layer.refresh()
            # The reference shows the exact intersecting seed support used as markers.
            origin = panel.layer.data_to_world(tuple(s.start for s in proposal.box))
            self.reference = panel.viewer.add_labels(
                proposal.reference, scale=panel.layer.scale, translate=origin, opacity=.65,
                name="Nuclear reference · qualifying seeds", metadata={INTERNAL_HIGHLIGHT_KEY: True})
            self.reference.editable = False
            self.show_seeds.setChecked(True)
            self.show_seeds.show()
            mapping = ", ".join(f"{seed} → {child}" for seed, child in
                                zip(proposal.parameters["seed_ids"], proposal.parameters["child_ids"], strict=True))
            self.details.setText(self.details.text() + f"\nNucleus → proposed child: {mapping}")
            panel.return_preview()
            panel._dimensions()
            p = proposal.parameters
            panel.status.setText(f"Read-only preview · Apply Watershed or Cancel Preview. Undo becomes available after Apply. "
                                f"{len(p['child_ids'])} children. "
                                f"{p['residual_components']} residual components, {p['residual_voxels']} voxels "
                                f"({p['residual_volume_um3']:.6g} µm³)." +
                                (" Inspect the preserved parent components." if p["residual_voxels"] else ""))
        except ValueError as error:
            if started:
                panel.discard()
            panel.status.setText(str(error))
        self.refresh()

    def _show_reference(self, visible):
        if self.reference is not None:
            self.reference.visible = visible

    def remove_reference(self):
        self.show_seeds.hide()
        if self.reference is not None and self.reference in self.geometry.viewer.layers:
            self.geometry.viewer.layers.remove(self.reference)
        self.reference = None
        if self.selection is not None:
            self.describe()
