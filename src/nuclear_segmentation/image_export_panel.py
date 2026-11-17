"""Application-owned controls for calibrated prepared-channel TIFF export."""
from __future__ import annotations

from qtpy.QtCore import QLineF, QRectF, QSize, Qt, Signal
from qtpy.QtGui import QColor, QIcon, QIconEngine, QPainter, QPen, QPixmap
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


class _ExportCheckIconEngine(QIconEngine):
    """High-contrast vector checkbox that remains visible on selected rows."""

    def __init__(self, checked: bool):
        super().__init__()
        self.checked = bool(checked)

    def clone(self):
        return _ExportCheckIconEngine(self.checked)

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap

    def paint(self, painter: QPainter, rect, mode, state) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(rect.width(), rect.height()) - 4
        box = QRectF(
            rect.x() + (rect.width() - side) / 2,
            rect.y() + (rect.height() - side) / 2,
            side,
            side,
        )
        border = QColor("#63d9d0" if self.checked else "#a8bbcc")
        painter.setPen(QPen(border, max(1.5, side / 11)))
        painter.setBrush(QColor("#2aa6b5") if self.checked else QColor("#08141e"))
        painter.drawRoundedRect(box, side / 6, side / 6)
        if self.checked:
            painter.setPen(
                QPen(
                    QColor("#06131c"),
                    max(2.0, side / 7),
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawLine(QLineF(
                box.left() + side * 0.22,
                box.top() + side * 0.53,
                box.left() + side * 0.43,
                box.top() + side * 0.74,
            ))
            painter.drawLine(QLineF(
                box.left() + side * 0.43,
                box.top() + side * 0.74,
                box.left() + side * 0.80,
                box.top() + side * 0.27,
            ))
        painter.restore()


def _check_icon(checked: bool) -> QIcon:
    return QIcon(_ExportCheckIconEngine(checked))


class ImageExportPanel(QWidget):
    export_requested = Signal(object, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        section = CollapsibleSection("multichannel_tiff_export", "Multichannel TIFF Export")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        note = QLabel(
            "Export current prepared channels in the order below. Display contrast, "
            "gamma, opacity, and colormaps are never applied to TIFF pixels. "
            "Checkboxes control export; row highlighting is only for reordering."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.channels = QListWidget()
        self.channels.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.channels.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.channels.setIconSize(QSize(22, 22))
        self.channels.setMinimumHeight(150)
        layout.addWidget(self.channels)
        self.selection_counter = QLabel("0 of 0 channels selected")
        self.selection_counter.setObjectName("exportSelectionCounter")
        self.selection_counter.setToolTip(
            "Checked channels will be exported; the highlighted row is only used for ordering."
        )
        layout.addWidget(self.selection_counter)
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
        self.channels.itemChanged.connect(self._update_selection_state)

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
        self.channels.blockSignals(True)
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
        self.channels.blockSignals(False)
        available_now = bool(order)
        self.status.setText(
            "Prepared channels ready. Drag rows or use Move Up/Down to set output order."
            if available_now
            else "Generate prepared channel previews before exporting."
        )
        self._update_selection_state()

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
        self._update_selection_state()

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
        self._busy = bool(busy)
        self.format_combo.setEnabled(not busy)
        self.pixel_type_combo.setEnabled(not busy)
        self.channels.setEnabled(not busy)
        self._update_selection_state()

    def _update_selection_state(self, item=None) -> None:
        checked = 0
        total = self.channels.count()
        signals_were_blocked = self.channels.signalsBlocked()
        self.channels.blockSignals(True)
        for row in range(total):
            current = self.channels.item(row)
            included = current.checkState() == Qt.CheckState.Checked
            checked += int(included)
            current.setIcon(_check_icon(included))
            current.setToolTip(
                "Included in TIFF export" if included else "Excluded from TIFF export"
            )
        self.channels.blockSignals(signals_were_blocked)
        self.selection_counter.setText(f"{checked} of {total} channels selected")
        self.export_button.setEnabled(not self._busy and checked > 0)

    def set_status(self, message: str) -> None:
        self.status.setText(str(message).splitlines()[0])

    def set_results(self, text: str) -> None:
        self.results.set_results(text)

    def reset(self) -> None:
        self.channels.clear()
        self._busy = False
        self.format_combo.setCurrentIndex(0)
        self.pixel_type_combo.setCurrentIndex(0)
        self.export_button.setEnabled(False)
        self.status.setText("Generate prepared channel previews before exporting.")
        self.results.set_results("No image export completed.")
        self._update_selection_state()
