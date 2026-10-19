# GUI/plots revision dated October 19, 2026 — version remains 1.0.1

65 tests and 13 subtests passed; one real-window/OpenGL test was excluded.
New tests cover label height after text and font changes, debouncing,
empty/invalid selections, channel selection, cached Z profiles, hidden tabs
and panel disposal. Visual inspection used offscreen Qt, 10/14-point fonts and
320 × 540 / 360 × 650 pixel panels. Files, Parameters, Matching, Filters,
Density, Graphs and ROI normalization panels were checked.

Narrow forms use vertical scrolling. At extreme widths/font sizes, some controls
may still require horizontal scrolling. These tests do not replace native
Windows/macOS execution or testing on real HiDPI displays. The external test
wrapper adapts only psutil queries to the environment's PID namespace; it is
not distributed as part of the application.

---

## Previous revision record — October 16, 2026

63 tests and 13 subtests passed; one test was not run.
Environment: Linux, Python 3.12, offscreen Qt, Napari 0.9.2 and PyQt6 6.11.0.
NumPy 2.3.5, SciPy 1.17.0, pandas 2.2.3, scikit-image 0.26.0.
The full matrix of dependency versions declared in `pyproject.toml` was not validated.

Coverage: mirrored first/last rules, boundary contact with a zero guard,
fractional guards, both/none modes, invalid spacing, UTF-8 with simulated cp1252
default reading, idempotent combobox setup, Qt controls, manual review,
Resume/export/density-only workflows, automatic matching, splitting,
normalization, Z correction and image export.

Integration test: a valid `min_z` with a centroid beyond the last plane remains
counted after export and reopening. Invalid limits remove the stale visible selection.

Not run: `test_cli_main_with_real_napari_viewer` (real window/OpenGL).
Also not run: native Windows or macOS tests, or new real Cellpose inference on
a GPU. The GUI was exercised using real Qt widgets and the Napari layer model
offscreen. This does not establish identical appearance on both operating systems.

The external runner needed to adapt only psutil PID/affinity queries to the
virtualized test environment. That adaptation is not included in the app.
Observed warnings concerned dependency deprecations, TIFF and empty plot legends.
