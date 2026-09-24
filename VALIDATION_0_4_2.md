# Validation — 0.4.2

Full suite: **35 passed, 13 subtests passed** on Linux Python 3.12.
A 0.4.2 wheel was built successfully. Environment versions are recorded in
`tested-environment-0.4.2.json`.

New tests exercise canonical label fingerprints across uint16/uint32 storage,
invalid label rejection, keep/exclude/automatic/undo state, calibrated 2D click
coordinate selection through the callback, read-only mask layers, persistent
manual decisions across filter changes, guard and ROI enforcement, unchanged
raw geometry, updated shell selection, CSV/JSON and final mask export, resume,
and the zero-retained-object case. Existing density, normalization, TIFF export,
model-dispatch, and legacy resume tests remain passing.

GUI tests use real Napari layer models and offscreen Qt widgets. Click callback
arguments are simulated; actual mouse interaction and full OpenGL display on
macOS/Windows remain to be checked by the user. Cellpose inference tests use a
simulated model. No actual model accuracy or syGlass import was tested.

Known warnings: Pydantic deprecation and empty plot legend when no shell markers
are enabled. Undo history is session-local; exported decisions are restored on
resume, but the undo history is not. Decisions are saved by Export results, not
automatically on close. Geometry editing is intentionally excluded.
