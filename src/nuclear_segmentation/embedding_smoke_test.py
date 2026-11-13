"""Local real-rendering smoke test for the NuCytoSeg napari embedding."""
from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the NuCytoSeg napari 0.10 embedded-viewer smoke test."
    )
    parser.add_argument(
        "--auto-close-ms",
        type=int,
        default=None,
        help="Close automatically after this many milliseconds (automation only).",
    )
    args = parser.parse_args(argv)
    os.environ.setdefault("QT_API", "pyqt6")

    from qtpy import API_NAME
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

    if API_NAME != "PyQt6":
        parser.error(f"Expected PyQt6 through qtpy, but selected {API_NAME}.")

    from .napari_embedding import (
        DISPLAY_DESCRIPTION,
        DISPLAY_NAME,
        EmbeddedNapariWidget,
    )

    class SmokeWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"{DISPLAY_NAME} — napari 0.10 embedding smoke test")
            self.resize(1380, 860)
            root = QWidget()
            layout = QVBoxLayout(root)
            layout.setContentsMargins(0, 0, 0, 0)
            heading = QLabel(
                f"{DISPLAY_NAME} · {DISPLAY_DESCRIPTION}\n"
                "Phase 1 proof of concept — synthetic data only"
            )
            heading.setObjectName("smokeTestHeading")
            heading.setStyleSheet("font-size: 15px; font-weight: 600; padding: 8px 12px;")
            layout.addWidget(heading)
            self.embedding = EmbeddedNapariWidget()
            self.embedding.status_changed.connect(self.statusBar().showMessage)
            layout.addWidget(self.embedding, 1)
            self.setCentralWidget(root)
            self.statusBar().showMessage("Ready for the local interaction checklist")

        def closeEvent(self, event):
            self.embedding.shutdown()
            event.accept()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(DISPLAY_NAME)
    app.setOrganizationName("NuCytoSeg")
    window = SmokeWindow()
    window.show()
    if args.auto_close_ms is not None:
        if args.auto_close_ms < 1:
            parser.error("--auto-close-ms must be positive.")
        QTimer.singleShot(args.auto_close_ms, window.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
