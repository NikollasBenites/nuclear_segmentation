# Validation — 0.3.0 (2026-09-21)

24 automated tests passed (plus 13 unittest subtests) in a Linux Python 3.12
headless environment. Tests cover the existing resume/export/density workflows,
new pre-segmentation worker, real Qt controls and Napari Labels editing through
a ViewerModel adapter, ROI saving, applying fixed limits, invalidation after
painting/transforms/percentile changes, stale input/ROI protection, legacy
presets, and parameter dispatch to a mock Cellpose model. The mock deliberately
modifies its input to verify that original measurement data remain unchanged.

Numerical tests verify that external zero padding changes full-stack percentiles
but not ROI percentiles, and that zero-valued tissue voxels are included.
Empty/flat ROIs, invalid ranges, nonfinite input, and shape mismatches are rejected.
All Python modules compile.

The fixed-lowhigh preview was additionally checked against normalize_img from
the downloaded Cellpose 4.2.1.1 wheel. The function was executed in isolation
using its NumPy-only lowhigh branch; arrays matched exactly for a synthetic
float32 volume and noninteger bounds. This was not an inference test or a test
of importing the full Cellpose/PyTorch stack.

No real biological stack was provided for this change. No model weights were
loaded; no GPU inference or macOS/Windows visual rendering was tested.
Improved segmentation accuracy is not established by these software tests.
The full-stack reference uses exact percentiles; Cellpose's default pipeline may
subsample large stacks. Live preview calculation is synchronous and may briefly
block the GUI for large volumes; image loading remains in the worker thread.

Sixteen upstream Pydantic deprecation warnings were reported, with no test failures.
The existing constraints file remains the prior pinned installation reference;
this test session used a separate environment, not a reinstall of those pins.
