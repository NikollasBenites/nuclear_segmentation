"""Application-owned Leica image-series selection dialog."""
from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


def _available(value, suffix="") -> str:
    return "Not available" if value is None else f"{value}{suffix}"


class ImageSeriesBrowser(QDialog):
    """Choose one LIF acquisition without reading every series volume."""

    def __init__(self, source_path, series, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image Series Browser")
        self.resize(1050, 440)
        self.series = tuple(series)
        layout = QVBoxLayout(self)
        source = QLabel(f"Leica LIF: {source_path}")
        source.setWordWrap(True)
        layout.addWidget(source)
        self.table = QTableWidget(len(self.series), 9)
        self.table.setHorizontalHeaderLabels(
            (
                "Index",
                "Series name",
                "Dimensions",
                "Channels",
                "Z planes",
                "Time points",
                "Objective",
                "Calibration Z/Y/X (µm)",
                "Acquisition date",
            )
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, item in enumerate(self.series):
            spacing = (
                "Not available"
                if item.voxel_spacing_zyx_um is None
                else ", ".join(f"{value:g}" for value in item.voxel_spacing_zyx_um)
            )
            values = (
                item.index,
                item.name,
                f"{item.dimensions} {item.shape}",
                item.channel_count,
                item.z_planes,
                item.time_points,
                _available(item.objective_magnification, "×"),
                spacing,
                _available(item.acquisition_date),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if not item.supported:
                    cell.setToolTip(item.limitation or "Unsupported series layout")
                    cell.setForeground(Qt.GlobalColor.gray)
                self.table.setItem(row, column, cell)
        self.table.resizeColumnsToContents()
        if self.series:
            self.table.selectRow(0)
        layout.addWidget(self.table)
        self.note = QLabel(
            "Select one acquisition. Pixel volumes are loaded only after confirmation."
        )
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.load_button = buttons.addButton(
            "Load Selected Image", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.load_button.clicked.connect(self._accept_supported)
        buttons.rejected.connect(self.reject)
        self.table.itemSelectionChanged.connect(self._update_selection)
        layout.addWidget(buttons)
        self._update_selection()

    @property
    def selected_series(self):
        row = self.table.currentRow()
        return self.series[row] if 0 <= row < len(self.series) else None

    def _update_selection(self):
        selected = self.selected_series
        self.load_button.setEnabled(selected is not None and selected.supported)
        if selected is not None and not selected.supported:
            self.note.setText(selected.limitation or "This series is unsupported.")
        else:
            self.note.setText(
                "Select one acquisition. Pixel volumes are loaded only after confirmation."
            )

    def _accept_supported(self):
        if self.selected_series is not None and self.selected_series.supported:
            self.accept()
