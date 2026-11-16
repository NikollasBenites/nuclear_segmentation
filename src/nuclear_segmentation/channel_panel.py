"""Editable channel configuration synchronized through ProjectState."""
from __future__ import annotations

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


COLORMAPS = ("gray", "cyan", "magenta", "green", "red", "yellow", "blue")
ROLES = ("segmentation", "measurement", "reference")


class ChannelConfigurationPanel(QWidget):
    change_requested = Signal(int, str, object)

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self._syncing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ("Index", "Name", "Role", "Colormap", "Visible")
        )
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.setMinimumHeight(150)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table)
        project_state.channels_changed.connect(self.set_channels)

    def set_channels(self, channels) -> None:
        self._syncing = True
        self.table.setRowCount(len(channels))
        for row, channel in enumerate(channels):
            index_item = QTableWidgetItem(str(channel.index))
            index_item.setFlags(index_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item = QTableWidgetItem(channel.name)
            visible_item = QTableWidgetItem()
            visible_item.setFlags(
                (visible_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            visible_item.setCheckState(
                Qt.CheckState.Checked if channel.visible else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, index_item)
            self.table.setItem(row, 1, name_item)
            role = QComboBox()
            role.addItems(ROLES)
            role.setCurrentText(channel.role)
            role.currentTextChanged.connect(
                lambda value, channel_index=channel.index: self._combo_changed(
                    channel_index, "role", value
                )
            )
            self.table.setCellWidget(row, 2, role)
            colormap = QComboBox()
            colormap.addItems(COLORMAPS)
            if channel.colormap not in COLORMAPS:
                colormap.addItem(channel.colormap)
            colormap.setCurrentText(channel.colormap)
            colormap.currentTextChanged.connect(
                lambda value, channel_index=channel.index: self._combo_changed(
                    channel_index, "colormap", value
                )
            )
            self.table.setCellWidget(row, 3, colormap)
            self.table.setItem(row, 4, visible_item)
        self._syncing = False

    def _combo_changed(self, index: int, field: str, value) -> None:
        if not self._syncing:
            self.change_requested.emit(index, field, value)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._syncing:
            return
        index = int(self.table.item(item.row(), 0).text())
        if item.column() == 1:
            self.change_requested.emit(index, "name", item.text())
        elif item.column() == 4:
            self.change_requested.emit(
                index,
                "visible",
                item.checkState() == Qt.CheckState.Checked,
            )

    def dispose(self) -> None:
        self.project_state.channels_changed.disconnect(self.set_channels)
