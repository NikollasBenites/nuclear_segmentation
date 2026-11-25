"""Interface preferences and guided navigation over the existing DesktopWindow."""
from pathlib import Path

from qtpy.QtCore import QObject, QSignalBlocker, Qt, QUrl
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                            QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                            QToolButton, QVBoxLayout, QWidget)

from .analysis_objectives import DENSITY_ROUTES, DESCRIPTIONS, OBJECTIVES
from .design_system import THEMES
from .matched_quantification import current_inputs


def choice(title, items):
    control = QComboBox()
    control.setAccessibleName(title)
    control.setToolTip(title)
    control.addItems(list(items))
    return control


def note(text=""):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class InterfaceProfiles(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.settings = window.workspace_settings.settings
        self.profile = self.settings.value("interface/profile", "Advanced")
        if self.profile not in ("Basic", "Advanced"):
            self.profile = "Advanced"
        self.objective = self.settings.value("interface/objective", "Custom Analysis")
        if self.objective not in OBJECTIVES:
            self.objective = "Custom Analysis"
        self.metric = self.settings.value("interface/density", "Nuclear density")
        if not DENSITY_ROUTES.get(self.metric):
            self.metric = "Nuclear density"
        self.controls = []
        self.disclosures = {}
        self._project_id = None
        self.skipped = set()

        self.selector = choice("Interface profile", ("Basic", "Advanced"))
        self.selector.setCurrentText(self.profile)
        self.selector.currentTextChanged.connect(self.set_profile)

        self.objective_selector = choice("Analysis objective", OBJECTIVES)
        self.objective_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.objective_selector.setMinimumContentsLength(15)
        self.objective_selector.setMaximumWidth(178)
        self.objective_selector.setCurrentText(self.objective)
        self.objective_selector.currentTextChanged.connect(self.set_objective)

        self.guide = QWidget()
        layout = QVBoxLayout(self.guide)
        layout.setContentsMargins(8, 3, 8, 5)
        self.guidance = note()
        self.guidance.setAccessibleName("Stage guidance and prerequisites")
        layout.addWidget(self.guidance)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.previous = QPushButton("Previous")
        self.skip = QPushButton("Skip")
        self.advance = QPushButton("Continue")
        self.advance.setProperty("priority", "primary")
        for button in (self.previous, self.skip, self.advance):
            row.addWidget(button)
        self.previous.clicked.connect(lambda: self.step(-1))
        self.advance.clicked.connect(lambda: self.step(1))
        self.skip.clicked.connect(lambda: self.step(1, skip=True))

    @property
    def route(self):
        if self.objective == "Density & Quantification":
            return DENSITY_ROUTES[self.metric]
        route = OBJECTIVES[self.objective].stages
        if self.objective == "Custom Analysis" and self.window.project_state.active_session.scientific_role != "nuclear":
            return tuple(stage for stage in route if stage != "quantification")
        return route

    @property
    def needs_matching(self):
        return self.objective == "Nuclear–Cytoplasmic Matching" or (
            self.objective == "Density & Quantification" and self.metric == "Matched nuclear density")

    def set_profile(self, profile):
        if profile not in ("Basic", "Advanced"):
            return
        if profile != self.profile:
            sections = self.advanced_sections()
            self.disclosures[self.profile] = tuple(section.expanded for section in sections)
            for section, expanded in zip(sections, self.disclosures.get(profile, (False,) * len(sections))):
                section.set_expanded(expanded, emit=False)
        self.profile = profile
        self.settings.setValue("interface/profile", profile)
        self.sync()
        self.window._refresh_progress()

    def advanced_sections(self):
        context = self.window.context_panel
        return context.segmentation_page.advanced_section, context.matching_page.sections["automatic"]

    def set_objective(self, name):
        if name in OBJECTIVES:
            self.objective = name
            self.settings.setValue("interface/objective", name)
            self.sync()
            self.window._refresh_progress()

    def set_metric(self, name):
        if DENSITY_ROUTES.get(name):
            self.metric = name
            self.settings.setValue("interface/density", name)
            self.sync()
            self.window._refresh_progress()

    def form(self):
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        profile = choice("Interface profile", ("Basic", "Advanced"))
        theme = choice("Application appearance", THEMES)
        objective = choice("Analysis objective", OBJECTIVES)
        density = choice("Density objective", ())
        for name, route in DENSITY_ROUTES.items():
            density.addItem(name if route else name + " · planned", name)
            density.model().item(density.count()-1).setEnabled(bool(route))
        description = note()
        for title, control in (("Interface profile", profile), ("Appearance", theme),
                               ("Analysis objective", objective), ("Density objective", density)):
            layout.addRow(title, control)
        layout.addRow(description)
        self.controls.append((profile, theme, objective, density, description))
        self.sync()
        profile.currentTextChanged.connect(self.set_profile)
        theme.currentTextChanged.connect(self.window.set_theme)
        objective.currentTextChanged.connect(self.set_objective)
        density.activated.connect(lambda i: self.set_metric(density.itemData(i)))
        return widget

    def sync(self):
        theme = self.settings.value("appearance/theme", "Dark")
        with QSignalBlocker(self.objective_selector):
            self.objective_selector.setCurrentText(self.objective)
            self.objective_selector.setToolTip(self.objective)
        with QSignalBlocker(self.selector):
            self.selector.setCurrentText(self.profile)
        for profile, appearance, objective, density, description in self.controls:
            for combo, value in ((profile, self.profile), (appearance, theme), (objective, self.objective), (density, self.metric)):
                with QSignalBlocker(combo):
                    combo.setCurrentText(value)
            density.parentWidget().layout().setRowVisible(density, self.objective == "Density & Quantification")
            description.setText(OBJECTIVES[self.objective].description)

    def preferences(self):
        if not hasattr(self, "dialog"):
            self.dialog = QDialog(self.window)
            self.dialog.setWindowTitle("NuCytoSeg Preferences")
            layout = QVBoxLayout(self.dialog)
            layout.addWidget(self.form())
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.dialog.close)
            layout.addWidget(buttons)
        self.dialog.show()
        self.dialog.raise_()

    def _scope(self):
        state = self.window.project_state
        pair = state.matching_pair
        return (state.active_session_id, pair.session_id("nuclear"), pair.session_id("cytoplasmic"),
                self.objective, self.metric)

    def prerequisite(self, key):
        s = self.window.project_state
        if key == "project":
            return ""
        if not s.dataset_ready:
            return "Open a calibrated image or project in Project."
        required_role = {"Nuclear Segmentation": "nuclear", "Cytoplasmic Segmentation": "cytoplasmic"}.get(self.objective)
        if key == "segmentation" and required_role and s.active_session.scientific_role != required_role:
            return f"Choose or create a {required_role} session in Project."
        if key == "segmentation" and s.active_channel_index is None:
            return "Choose the segmentation channel in Project."
        if key == "mask_review" and not s.segmentation_is_current:
            return "Run segmentation for the active session."
        if key == "matching" and not (s.segmentation_is_current or s.matching_pair.current_result is not None):
            return "Choose a segmented session in Project."
        if key == "quantification" and (s.active_session.scientific_role != "nuclear" or not s.filtering_is_current):
            return "Choose a nuclear session with current measurements."
        if key == "quality_control" and not (s.filtering_is_current or s.matching_pair.current_result is not None):
            return "Calculate measurements for the active session or run matching."
        return ""

    def completion(self, key, values):
        state = self.window.project_state
        if key == "project" and not state.dataset_ready:
            return "Open a calibrated image or native project."
        if key == "preprocessing" and values[key] in {"Optional", "Outdated"}:
            return "Apply preparation, or choose Skip to use the existing image."
        if key == "segmentation" and not state.segmentation_is_current:
            return "Run segmentation, or choose a session with current masks."
        if key == "mask_review":
            if not state.filtering_is_current:
                return "Calculate current measurements in Filtering."
            if self.objective == "Density & Quantification" and not state.scientific_roi_reviewed:
                return "Review and accept the scientific ROI under ROI & Counting."
        if key == "matching" and self.needs_matching and state.matching_pair.current_result is None:
            return "Select compatible measured sessions and run automatic matching."
        if key == "quantification" and self.objective == "Density & Quantification" and values[key] != "Complete":
            return "Review the nuclear ROI and sampling settings in Mask Review."
        return ""

    def update(self, values):
        w = self.window
        revision = w.project_state.session_revision()
        if self._project_id != revision.project_id:
            self.skipped.clear()
            self._project_id = revision.project_id
        basic = self.profile == "Basic"
        key = w.navigation.current_workflow
        if key == "proofreading":
            key = "matching"
        route = self.route
        if self.objective == "Density & Quantification" and self.metric == "Matched nuclear density":
            try:
                current_inputs(w.project_state)
            except ValueError:
                if values["quantification"] == "Complete":
                    values["quantification"] = "Pending"
        optional = {"preprocessing", "quality_control"}
        if not self.needs_matching:
            optional.add("matching")
        if self.objective != "Density & Quantification":
            optional.add("quantification")
        for stage, button in w.navigation._buttons.items():
            reason = self.prerequisite(stage) if basic else ""
            button.setVisible(not basic or stage in route or stage == key)
            button.setEnabled(not reason or stage == key)
            if values[stage] in ("Pending", "Optional", "Available"):
                if (self._scope(), stage) in self.skipped:
                    values[stage] = "Skipped"
                elif stage in optional or stage not in route:
                    values[stage] = "Optional"
        w.navigation.set_statuses(values)
        self.guide.setVisible(basic)
        if not basic:
            return
        # An objective or profile switch can leave a valid off-route inspection open.
        pos = route.index(key) if key in route else -1
        next_key = route[pos + 1] if pos + 1 < len(route) else None
        busy = w.geometry_panel.busy or "Running" in values.values()
        reason = self.completion(key, values)
        if next_key and (not reason or key in optional):
            reason = self.prerequisite(next_key) or reason
        if w.geometry_panel.busy:
            reason = "Save or discard the geometry preview to continue."
        self.guidance.setText(reason or DESCRIPTIONS[key])
        self.previous.setEnabled(pos > 0 and not busy)
        self.advance.setEnabled(bool(next_key and not busy and not reason))
        self.skip.setVisible(key in optional and bool(next_key))
        self.skip.setEnabled(bool(next_key and not busy and not self.prerequisite(next_key)))
        self.skip.setToolTip("Skip this optional stage for the current analysis")

    def step(self, direction, *, skip=False):
        control = self.skip if skip else self.advance if direction > 0 else self.previous
        if not control.isEnabled() or self.window.geometry_panel.busy:
            return
        key = self.window.navigation.current_workflow
        key = "matching" if key == "proofreading" else key
        route = self.route
        pos = route.index(key) if key in route else -1
        target = pos + direction
        if 0 <= target < len(route):
            if skip:
                self.skipped.add((self._scope(), key))
            else:
                self.skipped.discard((self._scope(), key))
            self.window.set_workflow(route[target])

    def welcome_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addStretch()
        body = QWidget()
        body.setMaximumWidth(780)
        content = QVBoxLayout(body)
        title = QLabel("NuCytoSeg")
        title.setObjectName("welcomeTitle")
        content.addWidget(title)
        content.addWidget(note("Segmentation and quantitative analysis of calibrated microscopy images"))
        row = QHBoxLayout()
        content.addLayout(row)
        for action in self.window.project_actions[:2]:
            button = QToolButton()
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setProperty("priority", "primary" if action is self.window.project_actions[0] else "secondary")
            row.addWidget(button)
        row.addStretch()
        columns = QHBoxLayout()
        content.addLayout(columns)
        columns.addWidget(self.form(), 2)
        recent = QVBoxLayout()
        columns.addLayout(recent, 1)
        recent.addWidget(QLabel("Recent projects"))
        self.recents = QListWidget()
        self.recents.setAccessibleName("Recent native projects")
        self.recents.itemActivated.connect(lambda item: self.window.open_project(item.data(Qt.ItemDataRole.UserRole)))
        recent.addWidget(self.recents)
        self.refresh_recents()
        links = QHBoxLayout()
        content.addLayout(links)
        for title, handler in (("Documentation", self.documentation), ("Preferences", self.preferences), ("About", self.about)):
            button = QPushButton(title)
            button.clicked.connect(handler)
            links.addWidget(button)
        outer.addWidget(body, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch()
        return page

    def refresh_recents(self, folder=None):
        paths = self.settings.value("interface/recentProjects", [], type=list)
        paths = [p for p in paths if isinstance(p, str)]
        if folder:
            paths = [str(folder)] + [p for p in paths if p != str(folder)]
            self.settings.setValue("interface/recentProjects", paths[:6])
        self.recents.clear()
        for path in paths[:6]:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.recents.addItem(item)
        if not paths:
            item = QListWidgetItem("No recent projects")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recents.addItem(item)

    def documentation(self):
        QDesktopServices.openUrl(QUrl("https://github.com/NikollasBenites/nuclear_segmentation/blob/feature/nucytoseg-qt6-shell/README.md"))

    def about(self):
        QMessageBox.about(self.window, "About NuCytoSeg",
                          "NuCytoSeg\nCalibrated microscopy segmentation, association review and quantification.\n\nPyQt6 desktop with embedded napari.")
