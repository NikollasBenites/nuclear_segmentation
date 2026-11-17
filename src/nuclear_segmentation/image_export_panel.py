"""Application-owned controls for calibrated prepared-channel TIFF export."""
from __future__ import annotations

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .collapsible_section import CollapsibleSection
from .results_panel import ScientificResultsSection


class ImageExportPanel(QWidget):
    export_requested = Signal(object, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        section = CollapsibleSection("multichannel_tiff_export", "Multichannel TIFF Export")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        note = QLabel(
            "Export current prepared channels in the order below. Display contrast, "
            "gamma, opacity, and colormaps are never applied to TIFF pixels."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.channels = QListWidget()
        self.channels.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.channels.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.channels.setMinimumHeight(150)
        layout.addWidget(self.channels)
        channel_actions = QGridLayout()
        self.select_all_button = QPushButton("Select All")
        self.clear_selection_button = QPushButton("Clear Selection")
        self.move_up_button = QPushButton("Move Up")
        self.move_down_button = QPushButton("Move Down")
        for position, button in enumerate(
            (
                self.select_all_button,
                self.clear_selection_button,
                self.move_up_button,
                self.move_down_button,
            )
        ):
            channel_actions.addWidget(button, position // 2, position % 2)
        layout.addLayout(channel_actions)
        self.format_combo = QComboBox()
        self.format_combo.addItem("OME-TIFF", "ome")
        self.format_combo.addItem("ImageJ TIFF", "imagej")
        self.pixel_type_combo = QComboBox()
        self.pixel_type_combo.addItem("Uint16 — SyGlass/proofreading", "uint16")
        self.pixel_type_combo.addItem("Float32 — prepared intensity scale", "float32")
        layout.addWidget(QLabel("Output format"))
        layout.addWidget(self.format_combo)
        layout.addWidget(QLabel("Pixel type"))
        layout.addWidget(self.pixel_type_combo)
        self.export_button = QPushButton("Export TIFF…")
        self.export_button.setEnabled(False)
        layout.addWidget(self.export_button)
        self.status = QLabel("Generate prepared channel previews before exporting.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        section.set_content_layout(layout)
        outer.addWidget(section)
        self.results = ScientificResultsSection("image_export_results", "Export Results")
        outer.addWidget(self.results)
        outer.addStretch(1)

        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.clear_selection_button.clicked.connect(lambda: self._set_all_checked(False))
        self.move_up_button.clicked.connect(lambda: self._move_current(-1))
        self.move_down_button.clicked.connect(lambda: self._move_current(1))
        self.export_button.clicked.connect(self._request_export)

    def set_prepared_channels(self, channels, provenance) -> None:
        previous_order = [
            int(self.channels.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.channels.count())
        ]
        checked = {
            int(self.channels.item(row).data(Qt.ItemDataRole.UserRole)): (
                self.channels.item(row).checkState() == Qt.CheckState.Checked
            )
            for row in range(self.channels.count())
        }
        by_index = {channel.index: channel for channel in channels}
        available = set(provenance)
        order = [index for index in previous_order if index in available]
        order.extend(index for index in provenance if index not in order)
        self.channels.clear()
        for index in order:
            channel = by_index.get(index)
            report = provenance[index]
            name = report.get("channel_name", channel.name if channel else str(index))
            item = QListWidgetItem(f"{name} — {report['route_label']}")
            item.setData(Qt.ItemDataRole.UserRole, int(index))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(
                Qt.CheckState.Checked if checked.get(index, True) else Qt.CheckState.Unchecked
            )
            self.channels.addItem(item)
        available_now = bool(order)
        self.export_button.setEnabled(available_now)
        self.status.setText(
            "Prepared channels ready. Drag rows or use Move Up/Down to set output order."
            if available_now
            else "Generate prepared channel previews before exporting."
        )

    def selected_channel_order(self) -> list[int]:
        return [
            int(item.data(Qt.ItemDataRole.UserRole))
            for row in range(self.channels.count())
            if (item := self.channels.item(row)).checkState() == Qt.CheckState.Checked
        ]

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.channels.count()):
            self.channels.item(row).setCheckState(state)

    def _move_current(self, offset: int) -> None:
        row = self.channels.currentRow()
        target = row + int(offset)
        if row < 0 or target < 0 or target >= self.channels.count():
            return
        item = self.channels.takeItem(row)
        self.channels.insertItem(target, item)
        self.channels.setCurrentRow(target)

    def _request_export(self) -> None:
        order = self.selected_channel_order()
        if not order:
            self.status.setText("Select at least one prepared channel to export.")
            return
        self.export_requested.emit(
            order,
            str(self.format_combo.currentData()),
            str(self.pixel_type_combo.currentData()),
        )

    def set_busy(self, busy: bool) -> None:
        self.export_button.setEnabled(not busy and self.channels.count() > 0)
        self.format_combo.setEnabled(not busy)
        self.pixel_type_combo.setEnabled(not busy)
        self.channels.setEnabled(not busy)

    def set_status(self, message: str) -> None:
        self.status.setText(str(message).splitlines()[0])

    def set_results(self, text: str) -> None:
        self.results.set_results(text)

    def reset(self) -> None:
        self.channels.clear()
        self.format_combo.setCurrentIndex(0)
        self.pixel_type_combo.setCurrentIndex(0)
        self.export_button.setEnabled(False)
        self.status.setText("Generate prepared channel previews before exporting.")
        self.results.set_results("No image export completed.")
