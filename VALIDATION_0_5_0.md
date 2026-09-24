# Validation — 0.5.0

Full regression suite: **38 passed, 13 subtests passed**. The three matching
checks were rerun successfully after the final table refinements. A 0.5.0
wheel was built successfully. Exact environment versions are recorded in
`tested-environment-0.5.0.json`.

Matching tests verify physical DAPI volumes from known voxel counts/calibration,
Matched/Unmatched/Uncertain/Not reviewed separation, relinking/reset/undo,
many-to-one detection and one-to-one comparison filtering, export and restore,
CSV-compatible nullable integer partner IDs, PDF/PNG output, missing/invalid
labels, mask fingerprint mismatch, shape/calibration mismatch, non-integer
input rejection, calibrated 2D selection callbacks, hidden-by-default volumes,
alignment confirmation, and transformed-layer rejection.

Existing manual Keep/Exclude, ROI/guard, density, image export, normalization,
model dispatch and resume tests remain passing. Matching uses an independent
workspace and does not change those workflows.

Tests use synthetic data, real Napari layer models and offscreen Qt widgets on
Linux. Actual mouse interaction, full OpenGL rendering, the user's biological
images, macOS/Windows GUI behavior, and syGlass import remain untested. Cellpose
inference was not run; existing dispatch tests use a simulated model. Numerical
and metadata validation do not establish anatomical alignment or biological
validity of a manual association.

Warnings were non-failing: Pydantic deprecation, empty-shell plot legend, and a
TIFF warning from the intentionally invalid floating-point test fixture.
