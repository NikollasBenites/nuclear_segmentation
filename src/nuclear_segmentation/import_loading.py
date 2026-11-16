"""Responsive indeterminate feedback for long microscopy imports."""
from __future__ import annotations

from time import monotonic

from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout


class ImportLoadingDialog(QDialog):
    def __init__(self, filename: str, series_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("importLoadingDialog")
        self.setWindowTitle("Loading Leica LIF")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        title = QLabel("Loading Leica LIF")
        title.setObjectName("loadingTitle")
        self.file_label = QLabel(f"File: {filename}")
        self.series_label = QLabel(f"Series: {series_name}")
        self.stage_label = QLabel("Reading image volume…")
        self.elapsed_label = QLabel("Elapsed time: 00:00")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        note = QLabel("Please wait. Large microscopy volumes may take several minutes.")
        note.setWordWrap(True)
        for widget in (
            title,
            self.file_label,
            self.series_label,
            self.stage_label,
            self.elapsed_label,
            self.progress,
            note,
        ):
            layout.addWidget(widget)
        self._started = monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._update_elapsed)
        self.timer.start()

    def _update_elapsed(self) -> None:
        seconds = max(0, int(monotonic() - self._started))
        self.elapsed_label.setText(
            f"Elapsed time: {seconds // 60:02d}:{seconds % 60:02d}"
        )

    def update_stage(self, message: str) -> None:
        stage = str(message).split(":", 1)[0].replace("_", " ").strip().capitalize()
        self.stage_label.setText(stage + "…")

    def finish(self) -> None:
        self.timer.stop()
        self.close()
        self.deleteLater()
