"""Opt-in launcher for the independent NuCytoSeg Qt6 desktop interface."""
from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the NuCytoSeg Qt6 desktop shell.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Load calibrated synthetic layers for interface verification.",
    )
    args = parser.parse_args(argv)
    os.environ.setdefault("QT_API", "pyqt6")

    from qtpy import API_NAME
    from qtpy.QtWidgets import QApplication

    if API_NAME != "PyQt6":
        parser.error(f"Expected PyQt6 through qtpy, but selected {API_NAME}.")

    from .desktop_window import DesktopWindow
    from .napari_embedding import DISPLAY_NAME

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(DISPLAY_NAME)
    app.setApplicationDisplayName(DISPLAY_NAME)
    app.setOrganizationName("NuCytoSeg")
    window = DesktopWindow(load_demo=args.demo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
