# NuCytoSeg Phase 2A.1 TIFF and OME-TIFF import

Phase 2A.1 connects the independent desktop application to TIFF and OME-TIFF
images. It does not connect imported data to segmentation or other scientific
analysis stages.

## Scientific behavior

- Pixel reading and CZYX standardization reuse `core.load_tiff_czyx`.
- OME and ImageJ calibration reuse `core.read_voxel_spacing_um`.
- Source values and data types are preserved. No normalization, rescaling,
  interpolation, cropping, or bit-depth conversion is performed.
- A selected time point is reduced through the existing `TIME_INDEX` behavior.
- Ambiguous non-singleton axes produce an error and require an explicit axes
  override.
- Incomplete calibration blocks loading until the user enters positive finite
  Z, Y, and X values. Entered values are identified as a manual override.
- Each CZYX channel is displayed as a calibrated ZYX napari Image layer.

Project import state is held separately from workspace `QSettings` and the
existing analysis/export configuration schema. Scientific runtime execution is
not enabled from the new interface in this milestone.

## Loading and replacement safety

TIFF pixel reading occurs on a Qt worker thread. Napari layers are created on
the GUI thread after a successful read. Existing layers are retained if reading
or layer creation fails. Opening another image requires confirmation when the
viewer contains non-demo data. Concurrent imports are rejected, and application
shutdown waits for an active synchronous TIFF read to finish safely.

## Current scope boundary

Leica LIF appears in the file chooser but is intentionally deferred to the
Image Series Browser in Phase 2A.2. Channel role/name editing is implemented in
Phase 2A.3. Imported data are visualization/project inputs only and are never
silently passed into the TIFF-specific legacy analysis runtime.
