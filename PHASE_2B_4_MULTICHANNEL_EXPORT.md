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

## Pending stages

- Stage 2B.4.2: multichannel image preparation and processing provenance.
- Stage 2B.4.3: calibrated OME/ImageJ TIFF export adapter and JSON provenance.
- Stage 2B.4.4: final regression, memory review, and complete native checklist.

No Cellpose inference, LIF reader change, segmentation-result schema change, or
SyGlass compatibility claim is part of Stage 2B.4.1.

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

Native macOS and SyGlass validation have not been performed by the automated
headless environment.
