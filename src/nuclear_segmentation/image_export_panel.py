"""Application-owned controls for scientific results and prepared images."""
from __future__ import annotations

from qtpy.QtCore import QEvent, QLineF, QRect, QRectF, QSize, Qt, Signal
from qtpy.QtGui import QColor, QPainter, QPen
from qtpy.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .collapsible_section import CollapsibleSection
from .results_panel import ScientificResultsSection


class _ExportChannelDelegate(QStyledItemDelegate):
    """Draw and operate one high-contrast checkbox per export channel."""

    _indicator_size = 18
    _indicator_margin = 6

    def _indicator_rect(self, rect: QRect) -> QRect:
        return QRect(
            rect.left() + self._indicator_margin,
            rect.top() + (rect.height() - self._indicator_size) // 2,
            self._indicator_size,
            self._indicator_size,
        )

    def paint(self, painter: QPainter, option, index) -> None:
        item_option = QStyleOptionViewItem(option)
        self.initStyleOption(item_option, index)
        item_option.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        item_option.rect = item_option.rect.adjusted(
            self._indicator_size + 2 * self._indicator_margin, 0, 0, 0
        )
        super().paint(painter, item_option, index)
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
        box = QRectF(self._indicator_rect(option.rect)).adjusted(1.5, 1.5, -1.5, -1.5)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = QColor("#63d9d0" if checked else "#b8c8d6")
        painter.setPen(QPen(border, 2.0))
        painter.setBrush(QColor("#2aa6b5") if checked else QColor("#08141e"))
        painter.drawRoundedRect(box, 3.0, 3.0)
        if checked:
            painter.setPen(
                QPen(
                    QColor("#06131c"),
                    2.2,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawLine(QLineF(
                box.left() + box.width() * 0.22,
                box.top() + box.height() * 0.53,
                box.left() + box.width() * 0.43,
                box.top() + box.height() * 0.74,
            ))
            painter.drawLine(QLineF(
                box.left() + box.width() * 0.43,
                box.top() + box.height() * 0.74,
                box.left() + box.width() * 0.80,
                box.top() + box.height() * 0.27,
            ))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if not (index.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return False
        toggle = (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and self._indicator_rect(option.rect).contains(event.position().toPoint())
        ) or (
            event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select)
        )
        if not toggle:
            return False
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
        return model.setData(
            index,
            Qt.CheckState.Unchecked.value if checked else Qt.CheckState.Checked.value,
            Qt.ItemDataRole.CheckStateRole,
        )

    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), self._indicator_size + 8))
        return size


class ImageExportPanel(QWidget):
    export_requested = Signal(object, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        self.scientific_button = QPushButton("Export Scientific Results")
        self.scientific_button.setEnabled(False)
        outer.addWidget(self.scientific_button)
        self.scientific_status = QLabel()
        self.scientific_status.setTextFormat(Qt.TextFormat.PlainText)
        self.scientific_status.setWordWrap(True)
        self.scientific_status.hide()
        outer.addWidget(self.scientific_status)
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
        self.channels.setObjectName("exportChannelList")
        self.channels.setItemDelegate(_ExportChannelDelegate(self.channels))
        self.channels.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.channels.setDefaultDropAction(Qt.DropAction.MoveAction)
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
        self.scientific_status.clear()
        self.scientific_status.hide()
        self._update_selection_state()
