# Validation — 0.4.1

Full synthetic suite: **32 passed, 13 subtests passed**.

New checks cover float32 four-channel OME-TIFF round-trip pixel equality, channel
order and names, physical voxel sizes and units, JSON correction provenance,
single-channel export, invalid shapes/calibration, transformed-layer rejection,
label-layer rejection, existing-file protection, selected corrected-preview
saving through the Qt control, checkbox configuration, and automatic corrected
image export in the model-dispatch/export/resume workflow.

Existing correction, density, ROI, guard, resume, zero-shell and one-channel
checks remain passing. A 0.4.1 wheel was built successfully.

Tests used Linux Python 3.12, real Napari layer models and offscreen Qt controls.
TIFF verification used tifffile and OME XML parsing. Opening files in Fiji or
Bio-Formats, interactive drag ordering on macOS/Windows, large BigTIFF files,
and full OpenGL rendering were not tested. Actual Cellpose inference was not
run; model dispatch tests use a simulated model. Existing Pydantic deprecation
and empty-shell plot legend warnings remain non-failing.

Saving and correction run synchronously; large volumes can temporarily block
the UI. TIFFs are uncompressed and use float32. Original integer channels up to
16 bits are represented exactly; arbitrary high-precision sources may round
when converted to float32. Z correction and model behavior are unchanged.
