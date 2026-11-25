"""Temporary, read-only inspection of the two embedded matching layers."""
import numpy as np
from napari.utils.colormaps import CyclicLabelColormap

from .matching_appearance import ROLE_COLORS, lock_labels
from .label_geometry import measured_bounds, selected_bounds
from .matching_pair import MATCHING_LAYER


def add_population(viewer, pair, role, name, visible, opacity):
    source = getattr(pair, role)
    layer = viewer.add_labels(
        source.labels, name=name, scale=pair.grid.spacing_zyx_um, translate=pair.grid.translation_zyx_um,
        units=("µm", "µm", "µm"), blending="additive", visible=visible, opacity=opacity,
        colormap=CyclicLabelColormap(colors=[(0, 0, 0, 0), ROLE_COLORS[role]]),
        metadata={MATCHING_LAYER: role, "matching_pair": pair.population_identity,
                  "source_revision": source.owner, "measurement_revision": source.measurement_revision},
    )
    lock_labels(layer)
    return layer


def focus_population(viewer, state, layer, label):
    owner = layer.metadata["source_revision"]
    properties = state.sessions[owner.session_id].filtering_result.get("properties")
    point = focus_voxel(layer.data, label, properties)
    if point is not None:
        center_on_voxel(viewer, layer, point)


def _point_in_bounds(data, label, low, high):
    middle = (low[0] + high[0] - 1) / 2
    for z in sorted(range(low[0], high[0]), key=lambda plane: abs(plane - middle)):
        points = np.argwhere(data[z, low[1]:high[1], low[2]:high[2]] == label)
        if len(points):
            center = points.mean(axis=0)
            y, x = points[np.argmin(((points - center) ** 2).sum(axis=1))] + low[1:]
            return int(z), int(y), int(x)
    return None


def focus_voxel(data, label, properties):
    """Prefer measured bounds and verify the chosen optical section against geometry."""
    bounds = measured_bounds(properties, label, data.shape)
    if bounds is not None:
        point = _point_in_bounds(data, label, *bounds)
        if point is not None:
            return point
    bounds = selected_bounds(data, label)
    return _point_in_bounds(data, label, *bounds) if bounds is not None else None


def center_on_voxel(viewer, layer, point):
    world = layer.data_to_world(point)
    for axis in viewer.dims.not_displayed:
        viewer.dims.set_point(axis, world[axis])
    viewer.camera.center = tuple(world[axis] for axis in viewer.dims.displayed)


class MatchingInspection:
    def __init__(self, display):
        self.display = display
        self.ui = display.panel.explorer
        self.appearance = {}
        self.ui.isolate.clicked.connect(self.isolate)
        self.ui.restore.clicked.connect(self.restore)
        self.ui.include.toggled.connect(self._include_changed)
        self.ui.selection_changed.connect(display.render)
        self.ui.focus.clicked.connect(self.focus)
        self.ui.navigated.connect(self.navigate)

    def reset(self):
        saved, self.appearance = self.appearance, {}
        for layer, (visible, opacity) in saved.items():
            layer.visible = visible
            layer.opacity = opacity
        self.ui.restore.setEnabled(False)

    def restore(self):
        self.reset()
        self.display.render()

    def isolate(self):
        if not self.ui.selection:
            return
        self.display.show()
        if not self.appearance:
            self.appearance = {layer: (layer.visible, layer.opacity) for layer in self.display.layers.values()}
            for layer in self.appearance:
                layer.visible = True
                if layer.opacity == 0:
                    layer.opacity = .6
        self.display.render()

    def _include_changed(self, enabled):
        selection = self.ui.selection
        if (self.appearance and not enabled and selection and selection.focus_role == "nuclear"
                and selection.focus_id in selection.other_candidate_ids):
            self.ui.focus_parent()
        self.display.render()

    def navigate(self):
        if self.display._syncing:
            return
        s = self.ui.selection
        if s and self.appearance and s.focus_role == "nuclear" and s.focus_id in s.other_candidate_ids:
            self.ui.include.setChecked(True)
        if self.ui.follow.isChecked():
            self.focus()

    def focus(self):
        s = self.ui.selection
        if s and s.is_current(self.display.state.matching_pair):
            self.display.show()
            if s.focus_role in self.display.layers:
                focus_population(self.display.viewer, self.display.state, self.display.layers[s.focus_role], s.focus_id)
