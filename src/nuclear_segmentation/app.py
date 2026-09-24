"""Desktop entry point; all Qt and Napari operations remain on the main thread."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback


def main(argv=None):
    parser = argparse.ArgumentParser(description='Nuclear Segmentation — desktop Napari app')
    parser.add_argument('--config', type=Path, help='Load a saved JSON preset')
    parser.add_argument('--version', action='version', version='Nuclear Segmentation 0.6.0')
    args = parser.parse_args(argv)
    # Allow unsupported Apple GPU operations to fall back before torch is imported.
    if sys.platform == 'darwin':
        os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
    try:
        import napari
        from qtpy.QtWidgets import QApplication
        from .ui import Launcher
    except ImportError as exc:
        parser.exit(1, f'GUI dependency unavailable: {exc}\nInstall the gui extra in your active environment.\n')
    viewer = napari.Viewer(title='Nuclear Segmentation — M3/Windows unified')
    launcher = Launcher(viewer)
    if args.config:
        launcher.apply_config(json.loads(args.config.read_text(encoding='utf-8')))
    viewer.window.add_dock_widget(launcher, area='left', name='Analysis setup')
    viewer.window._qt_window.installEventFilter(launcher)
    # The local reference stays alive while napari.run() blocks; the Qt dock
    # also owns the launcher. Viewer forbids arbitrary Pydantic attributes.
    napari.run()
