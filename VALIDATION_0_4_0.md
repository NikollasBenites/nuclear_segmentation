# Validation — 0.4.0

Based on the user-supplied `nuclear_segmentation_app_v0_3_0.zip`. Comparison with
the previous distribution found the user's Python changes in `stages/shells.py`.
Those zero-shell changes are retained; the remaining DAPI-only restriction was
removed to allow MAP2 and other configured segmentation channels.

## Executed checks

- Full synthetic suite: **29 passed, 13 subtests passed**.
- Positive-pixel percentile reference, gain bounds, zero preservation, empty-plane
  interpolation, invalid-input rejection, and unchanged source arrays.
- Real Napari layer models and Qt controls: selected-layer correction, exact
  output name, repeated correction from the original, apply and stale-parameter
  rejection, independent model and measurement choices.
- Preview values equal the volume dispatched to a simulated Cellpose model.
- Simulated model mutation cannot overwrite original or corrected measurement data.
- One configured MAP2 channel and no shells: measurement, viewer attachment,
  reviewed ROI, export, correction CSV and JSON provenance, and resume.
- Original and corrected measurement routes both tested through export/resume.
- Existing density, guard, thickness, ROI, legacy normalization, launcher, and
  prior-analysis tests remain passing.
- Python wheel successfully built (0.4.0).

The obsolete test reading bundled notebooks was removed along with the notebooks.
Independent density numerical checks remain in the suite.

## Limits

Tests ran on Linux with Python 3.12; exact package versions are recorded in
`tested-environment-0.4.0.json`. Qt tests use offscreen rendering and a real
Napari layer model with a window adapter. Full interactive OpenGL visualization,
macOS/Windows execution, GPU inference, trained Cellpose inference, biological
segmentation quality, and large-volume performance were not validated here.
Do not interpret a successful mocked model dispatch as an inference benchmark.

Known non-failing warnings: Pydantic deprecation and an empty plot legend when
no shell markers are enabled. Correction currently runs synchronously in the
preview; a large image may briefly block the UI.
