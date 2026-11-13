"""Versioned QSettings persistence isolated from scientific configuration."""
from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtCore import QByteArray, QSettings
from qtpy.QtGui import QGuiApplication


SETTINGS_VERSION = 1
SETTINGS_GROUP = f"workspace/v{SETTINGS_VERSION}"


@dataclass(frozen=True)
class WorkspaceSnapshot:
    geometry: QByteArray
    window_state: QByteArray
    splitter_sizes: tuple[int, ...]
    navigation_collapsed: bool
    navigation_visible: bool
    context_visible: bool
    workflow: str


class WorkspaceSettings:
    """Read and write desktop layout only; never analysis configuration."""

    def __init__(self, settings: QSettings | None = None):
        self.settings = settings or QSettings("NuCytoSeg", "NuCytoSeg")

    def _key(self, slot: str, name: str) -> str:
        return f"{SETTINGS_GROUP}/{slot}/{name}"

    def read(self, slot: str = "current") -> WorkspaceSnapshot | None:
        geometry = self.settings.value(self._key(slot, "geometry"), QByteArray())
        if not isinstance(geometry, QByteArray) or geometry.isEmpty():
            return None
        state = self.settings.value(self._key(slot, "window_state"), QByteArray())
        raw_sizes = self.settings.value(self._key(slot, "splitter_sizes"), [])
        try:
            sizes = tuple(max(0, int(value)) for value in raw_sizes)
        except (TypeError, ValueError):
            sizes = ()
        return WorkspaceSnapshot(
            geometry=geometry,
            window_state=state if isinstance(state, QByteArray) else QByteArray(),
            splitter_sizes=sizes,
            navigation_collapsed=self.settings.value(
                self._key(slot, "navigation_collapsed"), False, type=bool
            ),
            navigation_visible=self.settings.value(
                self._key(slot, "navigation_visible"), True, type=bool
            ),
            context_visible=self.settings.value(
                self._key(slot, "context_visible"), True, type=bool
            ),
            workflow=str(self.settings.value(self._key(slot, "workflow"), "project")),
        )

    def write(self, snapshot: WorkspaceSnapshot, slot: str = "current") -> None:
        values = {
            "geometry": snapshot.geometry,
            "window_state": snapshot.window_state,
            "splitter_sizes": list(snapshot.splitter_sizes),
            "navigation_collapsed": snapshot.navigation_collapsed,
            "navigation_visible": snapshot.navigation_visible,
            "context_visible": snapshot.context_visible,
            "workflow": snapshot.workflow,
        }
        for name, value in values.items():
            self.settings.setValue(self._key(slot, name), value)
        self.settings.sync()

    def save_with_history(self, snapshot: WorkspaceSnapshot) -> None:
        current = self.read("current")
        if current is not None:
            self.write(current, "previous")
        self.write(snapshot, "current")

    def clear(self) -> None:
        self.settings.remove(SETTINGS_GROUP)
        self.settings.sync()


def geometry_intersects_screen(window) -> bool:
    """Return whether the restored frame reaches any currently available screen."""
    frame = window.frameGeometry()
    screens = QGuiApplication.screens()
    return not screens or any(frame.intersects(screen.availableGeometry()) for screen in screens)


def move_to_available_screen(window) -> None:
    """Move a recovered window onto the primary screen after monitor changes."""
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        width = min(window.width(), available.width())
        height = min(window.height(), available.height())
        window.resize(max(window.minimumWidth(), width), max(window.minimumHeight(), height))
        window.move(available.left() + 24, available.top() + 24)
