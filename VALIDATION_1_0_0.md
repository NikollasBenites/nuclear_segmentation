# Validation - 1.0.0

- 49 regression/UI tests passed, plus 13 parametrized subtests.
- One full CLI GUI test was excluded from the automated suite because this host
  lacks a working OpenGL rendering context. Actual Cellpose inference was not rerun.
- Real Qt setup/matching panels constructed and reviewed at 1100 x 700.
- Real napari 0.9.2 window tested at 1280 x 720, with synthetic dimension ranges:
  Z slider within window bounds; density/count widgets visible; filter controls scroll.
  The image canvas itself could not be rendered with OpenGL here.
- Wheel build and CLI version check passed for version 1.0.0.
- Tests run on Linux/Python 3.12 with QtPy/PyQt6. macOS/Windows native appearance,
  display scaling and multi-monitor behavior require local verification.
- A test-only workaround bypassed a /proc PID visibility issue while Dask imported.
  It is not part of the application code.
- Existing scientific code was retained. Latest previously delivered split,
  intensity-profile and export-folder patches were carried forward.

Tests now approve the export-name dialog automatically and check the new expected
behavior for seedless MAP2 components (preserve and flag, rather than reject).
