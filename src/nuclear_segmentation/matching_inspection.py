"""Temporary, read-only inspection of the two embedded matching layers."""
import numpy as np
from napari.utils.colormaps import DirectLabelColormap

from .automatic_matching import COLORS
from .label_geometry import measured_bounds, selected_bounds
from .mask_selection import INTERNAL_HIGHLIGHT_KEY, bounded_highlight


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
        self.highlight = self.focused_selection = None
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

    def clear_highlight(self):
        self.focused_selection = None
        layer, self.highlight = self.highlight, None
        if layer is not None and layer in self.display.viewer.layers:
            self.display.viewer.layers.remove(layer)

    def restore(self):
        self.clear_highlight()
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
        selection = self.ui.selection
        if self.highlight is not None and (selection != self.focused_selection or
                (self.appearance and selection.focus_role == "nuclear"
                 and selection.focus_id in selection.other_candidate_ids and not self.ui.include.isChecked())):
            self.clear_highlight()
        if not self.appearance:
            return
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
        owner = layer.metadata["source_revision"]
        properties = self.display.state.sessions[owner.session_id].filtering_result.get("properties")
        if key not in self.positions:
            self.positions[key] = focus_voxel(layer.data, label, properties)
        point = self.positions[key]
        if point is None:
            return
        viewer = self.display.viewer
        self.clear_highlight()
        bounds = measured_bounds(properties, label, layer.data.shape)
        if bounds is not None:
            box = tuple(slice(a, b) for a, b in zip(*bounds))
            stats = selection.source().statistics
            counts = stats.nuclear_voxels if role == "nuclear" else stats.cytoplasmic_voxels
            if np.count_nonzero(layer.data[box] == label) != counts[label]:
                bounds = None
        crop, affine = bounded_highlight(layer, label, bounds)
        crop.setflags(write=False)
        active = viewer.layers.selection.active
        self.highlight = viewer.add_labels(
            crop, affine=affine, units=layer.units, name=f"Matching focus · {role} {label}", opacity=.7,
            colormap={None: "transparent", 0: "transparent", 1: "#67ffff"},
            metadata={INTERNAL_HIGHLIGHT_KEY: True},
        )
        self.highlight.editable = False
        self.focused_selection = selection
        if active in viewer.layers:
            viewer.layers.selection.active = active
        center_on_voxel(viewer, layer, point)
