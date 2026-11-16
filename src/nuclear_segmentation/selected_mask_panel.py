"""Persistent selected-mask summary for the NuCytoSeg right sidebar."""
from __future__ import annotations

from qtpy.QtCore import QSize
from qtpy.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from .ui_icons import icon


class SelectedMaskPanel(QFrame):
    """Compact selection summary independent of the active workflow page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("selectedMaskPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(4)

        header = QHBoxLayout()
        marker = QLabel()
        marker.setPixmap(icon("select", accent=True).pixmap(QSize(20, 20)))
        marker.setToolTip("Selected mask")
        title = QLabel("Selected Mask")
        title.setObjectName("panelTitle")
        header.addWidget(marker)
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        self.mask_id_label = QLabel("Mask ID: —")
        self.mask_id_label.setObjectName("selectedMaskId")
        self.mask_layer_label = QLabel("No mask selected")
        self.mask_layer_label.setObjectName("selectedMaskLayer")
        self.mask_layer_label.setWordWrap(True)
        layout.addWidget(self.mask_id_label)
        layout.addWidget(self.mask_layer_label)

    def set_selection(self, layer, mask_id: int | None) -> None:
        if layer is None or mask_id is None:
            self.mask_id_label.setText("Mask ID: —")
            self.mask_layer_label.setText("No mask selected")
        else:
            self.mask_id_label.setText(f"Mask ID: {int(mask_id)}")
            self.mask_layer_label.setText(layer.name)
