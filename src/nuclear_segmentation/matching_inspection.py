"""Temporary, read-only inspection of the two embedded matching layers."""
import numpy as np
from napari.utils.colormaps import DirectLabelColormap

from .automatic_matching import COLORS
from .mask_selection import selected_bounds


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
    columns = [f"{edge}_{axis}" for edge in ("min", "max") for axis in "zyx"]
    if properties is not None and label in properties.index and all(c in properties for c in columns):
        limits = properties.loc[label, columns].to_numpy(dtype=float)
        low, high = limits[:3], limits[3:] + 1
        if np.all((0 <= low) & (low < high) & (high <= data.shape)):
            point = _point_in_bounds(data, label, low.astype(int), high.astype(int))
            if point is not None:
                return point
    bounds = selected_bounds(data, label)
    return _point_in_bounds(data, label, *bounds) if bounds is not None else None


class MatchingInspection:
    def __init__(self, display):
        self.display = display
        self.ui = display.panel.explorer
        self.appearance = {}
        self._shown = None
        self.positions = {}
        self.population = None
        self.ui.isolate.clicked.connect(self.isolate)
        self.ui.restore.clicked.connect(self.restore)
        self.ui.focus.clicked.connect(self.focus)
        self.ui.include.toggled.connect(self.update)
        self.ui.selection_changed.connect(self.update)

    def reset(self):
        self.restore()
        self.population = None
        self.positions.clear()

    def restore(self):
        self._shown = None
        saved, self.appearance = self.appearance, {}
        for layer, (colormap, visible, opacity) in saved.items():
            layer.colormap = colormap
            layer.visible = visible
            layer.opacity = opacity
        self.ui.restore.setEnabled(False)

    def _ready(self):
        selection = self.ui.selection
        if not selection or not selection.is_current(self.display.state.matching_pair):
            return False
        if len(self.display.layers) != 2:
            self.display.show()
        return len(self.display.layers) == 2

    def isolate(self):
        if not self._ready():
            return
        if not self.appearance:
            self.appearance = {layer: (layer.colormap, layer.visible, layer.opacity)
                               for layer in self.display.layers.values()}
        self.update()

    def update(self):
        if not self.appearance:
            return
        selection = self.ui.selection
        if selection is None:
            self.restore()
            return
        self.ui.restore.setEnabled(True)
        shown = (selection.cytoplasmic_id, self.ui.include.isChecked())
        if shown == self._shown:
            return
        self._shown = shown
        nuclear = dict.fromkeys(selection.associated_ids, COLORS["MATCH"])
        if self.ui.include.isChecked():
            nuclear.update(dict.fromkeys(selection.other_candidate_ids, (1., 0., 1., 1.)))
        maps = {"nuclear": nuclear,
                "cytoplasmic": {selection.cytoplasmic_id: self.display._colors["cytoplasmic"]}}
        for role, colors in maps.items():
            layer = self.display.layers[role]
            colors.update({None: (0., 0., 0., 0.), 0: (0., 0., 0., 0.)})
            layer.colormap = DirectLabelColormap(color_dict=colors)
            layer.visible = True
            if layer.opacity == 0:
                layer.opacity = .6

    def focus(self):
        if not self._ready():
            return
        selection = self.ui.selection
        role, label = selection.focus_role, selection.focus_id
        layer = self.display.layers[role]
        if self.appearance and role == "nuclear" and label in selection.other_candidate_ids:
            self.ui.include.setChecked(True)
        if self.population != selection.population_identity:
            self.positions.clear()
            self.population = selection.population_identity
        key = role, label
        if key not in self.positions:
            owner = layer.metadata["source_revision"]
            session = self.display.state.sessions[owner.session_id]
            self.positions[key] = focus_voxel(layer.data, label, session.filtering_result.get("properties"))
        point = self.positions[key]
        if point is None:
            return
        world = layer.data_to_world(point)
        viewer = self.display.viewer
        layer.visible = True
        if layer.opacity == 0:
            layer.opacity = .6
        if viewer.dims.ndisplay == 2:
            for axis in viewer.dims.not_displayed:
                viewer.dims.set_point(axis, world[axis])
        # Camera.center is in displayed-axis order (padded to three coordinates in 2D).
        viewer.camera.center = tuple(world[axis] for axis in viewer.dims.displayed)
