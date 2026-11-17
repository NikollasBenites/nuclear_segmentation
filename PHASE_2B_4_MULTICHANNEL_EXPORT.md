# Phase 2B.4 — Multichannel Image Preparation and Export

Phase 2B.4 is implemented incrementally on
`feature/nucytoseg-qt6-shell`. This document records completed behavior and
must not be read as a claim that pending stages or native SyGlass validation
have been completed.

## Stage 2B.4.1 — projection lifecycle and image display

The XY Projection section now provides **Clear Projection**. It removes the
application-owned projection and any unfinished projection-dependent crop
selection or preview, releases controller references, and returns selection to
an imported source image when one is available. It does not reset or reload the
working dataset, confirmed crop, normalization state, normalization ROI, or
Z-correction state. The action is disabled when no projection exists. If an
unfinished selection or crop preview would be discarded, the interactive action
requires confirmation.

The crop controller also observes layer removal events. Removing an owned
projection through a supported application action therefore cannot leave a stale
projection reference or an enabled Clear Projection button.

The custom Layers area now includes a collapsible **Image Display** section for
the active napari Image layer. It uses public layer properties only:

- `contrast_limits`;
- `gamma`;
- `opacity`.

Minimum and maximum contrast have editable numeric controls and normalized
sliders. Auto Contrast calculates exact 1st and 99th percentiles from finite
pixels in the active layer. For a polygon-cropped project layer whose spatial
shape matches the stored mask, statistics use only the valid polygon region.
Artificial exterior fill is excluded while genuine zero-valued pixels inside
the polygon remain included. Constant images receive a finite display interval.

Reset Contrast delegates to napari's public `reset_contrast_limits()` method.
All controls are event-driven: changing the active Image layer or changing a
display property programmatically updates the panel. Removing the active layer
disconnects its callbacks. These operations never write to the layer data array,
change scientific configurations, or invalidate preprocessing previews.

## Stage 2B.4.2 — multichannel image preparation

The Preprocessing workflow now includes **Multichannel Image Preparation**.
Users can select one, several, or all channels and assign a stable-index route:

- original working image;
- Z-corrected image, when a current correction report exists;
- globally normalized image;
- Z-corrected and globally normalized image, when correction is available.

Every calculation starts from the unmodified working channel. Z correction
reuses the validated `correct_z()` implementation and current per-channel
options; it never consumes or recursively corrects a preview. Global
normalization then applies one pair of percentile limits to the complete ZYX
channel. It never normalizes individual planes. The explicit mapping is
`(input - lower_limit) / (upper_limit - lower_limit)`, with deliberate optional
clipping to `[0, 1]`. Constant and all-zero inputs produce a finite all-zero
float32 result.

The default sampling scope uses the valid working XY region through every Z
plane. Polygon exterior fill is excluded while genuine zero-valued voxels in
the polygon remain samples. An explicitly labelled bounding-box scope is also
available and warns that it includes exterior fill. The independent Cellpose
normalization ROI is not selected implicitly.

Prepared layers retain channel order, anisotropic calibration, crop translation,
colormap, and additive blending. Original-route data share the existing working
array without a full copy. Z-corrected or normalized routes allocate only their
selected channel output. Clearing or invalidating preparation removes those
layers and releases controller references without altering applied Z correction,
Cellpose normalization, crop, ROI, or source data.

Per-channel provenance records source/series identity, original and working
shapes, channel index/name, crop offset and ROI digest, calibration, processing
order, correction report, global limits, mapping, clipping, input/output ranges,
and the fact that display settings were not applied. Channel renaming updates
preview names and provenance by stable index.

### Intermittent Shapes dimensionality investigation

Both crop selection and normalization ROI are intentionally 2D Shapes layers.
Previously, the normalization controller could construct its 2D Shapes layer
while the viewer was still in 3D display, and either Shapes layer could remain
active in an editing mode during a later transition to 3D. This creates a
plausible transient mismatch between `(Y, X)` vertices and a three-axis displayed
transform, consistent with the reported broadcast from `(2,2)` into `(2,3)`.

The application now switches to 2D before constructing any XY Shapes layer,
ends Shapes editing and selects a compatible 3D Image before a 3D transition,
and validates that crop/ROI vertices are strictly `N×2`. No napari internals,
stored vertices, ROI rasterization, or crop mathematics were changed.

## Pending stages

- Stage 2B.4.3: calibrated OME/ImageJ TIFF export adapter and JSON provenance.
- Stage 2B.4.4: final regression, memory review, and complete native checklist.

No Cellpose inference, LIF reader change, segmentation-result schema change, or
SyGlass compatibility claim is part of Stages 2B.4.1–2B.4.2.

## Automated validation

Focused tests cover projection generation/clear/regeneration, preservation of
unrelated preview layers and source arrays, confirmed-crop preservation,
external projection removal synchronization, uint8/uint16/float32 display
controls, active-layer and external-property synchronization, Auto/Reset
Contrast, polygon-fill exclusion, genuine-zero inclusion, and active-layer
removal cleanup.

## Native macOS check for Stage 2B.4.1

1. Open a calibrated multichannel TIFF.
2. Generate a maximum or mean XY projection.
3. Click Clear Projection and confirm that source layers remain available.
4. Generate another projection, draw an unfinished crop, and verify the clear
   confirmation protects that selection.
5. Apply a crop, generate and clear another projection, and verify the confirmed
   crop remains unchanged.
6. Select original, normalization-preview, and Z-corrected Image layers in turn.
7. Adjust minimum/maximum contrast, gamma, and opacity.
8. Run Auto Contrast and Reset Contrast.
9. Toggle layers and Z planes and confirm no scientific preview disappears.
10. Confirm the source TIFF and processing results remain unchanged.

Stage 2B.4.1 was validated by the user on native macOS. Stage 2B.4.2 and SyGlass
validation have not been performed by the automated headless environment.

## Native macOS check for Stage 2B.4.2

1. With a polygon or rectangle crop workflow active, alternate between 2D and
   3D before and after drawing; confirm there is no Shapes dimensionality error.
2. Repeat with the independent normalization ROI.
3. Calculate Z correction for multiple channels.
4. Select all channels in Multichannel Image Preparation.
5. Assign a mixture of Original, Normalized, Z-corrected, and Z-corrected +
   normalized routes.
6. Generate previews and inspect every channel through Z in 2D and 3D.
7. Verify normalized previews use one consistent mapping across Z.
8. Change display contrast/gamma/opacity and confirm prepared pixels and reported
   ranges do not change.
9. Rename a channel and confirm its prepared preview remains associated with the
   same index.
10. Clear prepared previews and confirm Z-correction and source layers remain.
