"""Display-only label boundaries and scientific colors shared by Matching and Review."""
import numpy as np
from scipy import ndimage
from napari.utils.colormaps import CyclicLabelColormap, DirectLabelColormap
from qtpy.QtWidgets import QComboBox, QLabel

from .automatic_matching import COLORS
from .label_geometry import measured_bounds
from .mask_selection import INTERNAL_HIGHLIGHT_KEY, bounded_highlight

ROLE_COLORS = {"nuclear": (.16, .8, 1., 1.), "cytoplasmic": (1., .52, .16, 1.)}
STYLES = ("Filled masks", "Outlines only", "Filled masks with outlines")
LEGEND = ("Automatic classification · Green MATCH · Yellow AMBIGUOUS · Red NO_MATCH\n"
          "Boundaries · White selected · Cyan accepted · Violet other candidate")
EDGE_COLORS = {"selected": "white", "accepted": "cyan", "candidate": "#be70ff"}


def appearance_control(layout, role):
    title = f"{role.title()} appearance"
    label = QLabel(title)
    box = QComboBox()
    box.setAccessibleName(title)
    box.addItems(STYLES)
    label.setBuddy(box)
    layout.addWidget(label)
    layout.addWidget(box)
    return box


def relationships(index, role, label):
    marks = {(role, label): "selected"}
    if role == "nuclear":
        accepted = index.nuclei[label].accepted_cytoplasmic_id
        for cyto in index.by_nucleus[label]:
            marks["cytoplasmic", cyto] = "accepted" if cyto == accepted else "candidate"
    else:
        for nucleus, _ in index.overlaps[label]:
            accepted = index.nuclei[nucleus].accepted_cytoplasmic_id == label
            marks["nuclear", nucleus] = "accepted" if accepted else "candidate"
    return marks


def footprint(axes):
    shape = tuple(3 if axis in axes else 1 for axis in range(3))
    return np.ones(shape, dtype=bool)


def population_edges(data, axes):
    # Compare exact integer IDs, including adjacent objects and the volume border.
    edge = np.zeros(data.shape, dtype=bool)
    for axis in axes:
        planes = np.moveaxis(data, axis, 0)
        out = np.moveaxis(edge, axis, 0)
        out[0] = out[-1] = True
        change = planes[:-1] != planes[1:]
        out[:-1] |= change
        out[1:] |= change
    return np.where(edge, data, 0)


def lock_labels(layer):
    layer.editable = False
    # napari may reevaluate editability after dimension changes.
    def protect(event):
        if layer.editable:
            layer.editable = False
    layer.events.editable.connect(protect)


class MaskAppearance:
    def __init__(self, viewer, state):
        self.viewer = viewer
        self.state = state
        self.edges = {}
        self.marks = {}
        self.maps = {}
        self.colormaps = {}
        self.saved_text = None

    def _remove(self, layer):
        if layer in self.viewer.layers:
            self.viewer.layers.remove(layer)

    def clear(self):
        if self.saved_text is not None:
            text = self.viewer.canvas.overlays.text
            text.text, text.visible = self.saved_text
            self.saved_text = None
        for _, layer in [*self.edges.values(), *self.marks.values()]:
            self._remove(layer)
        self.edges.clear()
        self.marks.clear()
        self.maps.clear()
        self.colormaps.clear()

    def _overlay(self, source, data, title, colors, **transform):
        active = self.viewer.layers.selection.active
        data.setflags(write=False)
        layer = self.viewer.add_labels(
            data, name=title, colormap=colors, units=source.units, blending="translucent",
            visible=source.visible, opacity=source.opacity,
            metadata={INTERNAL_HIGHLIGHT_KEY: True}, **transform)
        lock_labels(layer)
        if active in self.viewer.layers:
            self.viewer.layers.selection.active = active
        return layer

    def legend(self, message):
        text = self.viewer.canvas.overlays.text
        if self.saved_text is None:
            self.saved_text = text.text, text.visible
        text.text = LEGEND + ("\n" + message if message else "")
        text.visible = True

    @staticmethod
    def _colors(role, index, ids, *, outline=False):
        def shade(color):
            # Keep the classification hue on small objects made entirely of edge voxels.
            return tuple(.65 * v for v in color[:3]) + (1.,) if outline else color

        color = shade(ROLE_COLORS[role])
        if index is None and ids is None:
            return CyclicLabelColormap(colors=[(0, 0, 0, 0), color])
        colors = dict.fromkeys(ids or (), color)
        if index is not None:
            colors = {i: shade(COLORS[index.nuclei[i].auto_class]) for i in (index.nuclei if ids is None else ids)}
        return DirectLabelColormap(color_dict={**colors, None: "transparent", 0: "transparent"})

    def _population(self, source, role, style, index, ids, axes):
        key = source, index, style, ids
        changed = self.maps.get(role) != key
        if changed:
            colors = self._colors(role, index, ids)
            source.colormap = {None: "transparent"} if style == 1 else colors
            self.maps[role] = key
            if style:
                self.colormaps[role] = colors if style == 1 else self._colors(role, index, ids, outline=True)
        edge_key = source, axes
        if role in self.edges and (not style or self.edges[role][0] != edge_key):
            self._remove(self.edges.pop(role)[1])
        if style:
            if role not in self.edges:
                edge = self._overlay(source, population_edges(source.data, axes), f"{role.title()} outlines",
                                     self.colormaps[role], scale=source.scale, translate=source.translate)
                self.edges[role] = edge_key, edge
            elif changed:
                self.edges[role][1].colormap = self.colormaps[role]
            self._sync(self.edges[role][1], source)

    def render(self, layers, styles, index, classified, marks, isolated=None):
        axes = tuple(self.viewer.dims.displayed)
        for role, source in layers.items():
            ids = None if isolated is None else frozenset(isolated.get(role, ()))
            class_index = index if classified and role == "nuclear" else None
            self._population(source, role, styles[role].currentIndex(), class_index, ids, axes)
        for item in set(self.marks) - marks.keys():
            self._remove(self.marks.pop(item)[1])
        for item, kind in marks.items():
            role, label = item
            source = layers[role]
            key = source, axes, kind
            if item not in self.marks or self.marks[item][0] != key:
                if item in self.marks:
                    self._remove(self.marks.pop(item)[1])
                owner = source.metadata["source_revision"]
                props = self.state.sessions[owner.session_id].filtering_result.get("properties")
                bounds = measured_bounds(props, label, source.data.shape)
                padding = 2 if kind == "selected" else 1
                geom = bounded_highlight(source, label, bounds, padding=padding)
                counts = getattr(index.result.statistics, f"{role}_voxels")
                if bounds is not None and geom[0].sum() != counts[label]:
                    geom = bounded_highlight(source, label, padding=padding)
                if geom is None:
                    continue
                crop, affine = geom
                structure = footprint(axes)
                inner = ndimage.binary_dilation(crop, structure=structure) if kind == "selected" else crop
                ring = ndimage.binary_dilation(inner, structure=structure) & (inner == 0)
                layer = self._overlay(source, ring.astype(np.uint8), f"{kind.title()} · {role} {label}",
                                      {None: "transparent", 1: EDGE_COLORS[kind]}, affine=affine)
                self.marks[item] = key, layer
            # A closed 3D shell covers the front surface. Keep it translucent
            # even when the user inspects a fully opaque classified population.
            self._sync(self.marks[item][1], source, .4 if len(axes) == 3 else 1.)

    @staticmethod
    def _sync(layer, source, opacity_factor=1.):
        layer.visible = source.visible
        layer.opacity = source.opacity * opacity_factor
