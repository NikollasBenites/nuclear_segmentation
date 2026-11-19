# NuCytoSeg Phase 2C.3.3.1 — Quality Control and Z-preview lifecycle

## Quality Control integration

The independent Quality Control workflow embeds the existing `LivePlots`
widget. A narrow `ProjectLivePlots` adapter in the same module maps current
`ProjectState` fields to the legacy namespace keys; it does not implement a
second plotting system.

The single persistent Matplotlib canvas provides the existing volume,
volume-versus-sphericity, channel-intensity, perinuclear positive-fraction, and
Z-profile views. It retains the existing 300 ms render debounce and incremental
per-plane Z-profile timer. Hidden workflow pages remain dirty without rendering.

The adapter uses cached filtering properties and retained IDs. It never reloads
an image or recalculates morphology, intensity, perinuclear metrics, filtering,
or segmentation. The exact Cellpose model-input volume is retained by reference
after execution for the Z profile. No copy is made. Older corrected results that
do not contain this reference report the profile unavailable rather than
repeating Z correction. Uncorrected legacy results can use the corresponding
working-channel view safely.

Outdated measurements are not plotted as current. Project replacement resets
the Z-profile cache, while ordinary filter changes reuse it. Closing the desktop
stops both plot timers and disconnects state signals.

## Z-correction preview lifecycle

Editing a channel's pending correction fields now preserves its previous preview
layer and calculated report. The layer is labelled and tagged as outdated, and
the existing report options remain available as its provenance. Pending edits do
not alter an already applied scientific correction or dependent normalization.
Apply continues to reject settings that do not match the calculated report.

`Calculate and Preview` continues to use the existing worker and `correct_z()`.
The old layer remains present during processing and on failure. After success,
its data and calibration are updated in place on the Qt thread; visibility,
colormap, opacity, and blending are retained. Each channel is replaced
independently, so unrelated previews remain untouched and duplicate layers are
not created.

Applying a valid correction retains the visualization layer and marks it as an
applied corrected preview. Explicit reset, crop/input invalidation, project
closure, and image replacement retain their existing cleanup behavior.

No Z-correction mathematics, normalization behavior, segmentation routing, or
scientific export contract was changed.

## Native macOS verification

1. Complete segmentation and measurements, then open Quality Control.
2. Switch among all five graph types and exercise the Matplotlib toolbar.
3. Select each available intensity channel and perinuclear marker.
4. Adjust filters in Mask Review and confirm retained populations update without
   a measurement or Cellpose rerun.
5. Confirm the Z profile is calculated incrementally and reused after filter
   changes.
6. Edit Z-correction fields and confirm the existing preview remains visible and
   is identified as outdated.
7. Confirm Apply rejects mismatched pending settings.
8. Recalculate successfully and confirm the same layer is updated without losing
   visibility, colormap, or opacity.
9. Force or observe a calculation failure and confirm the prior layer remains.
10. Apply a current correction and confirm its visual layer remains and is marked
    applied.
11. Repeat with multiple channels and confirm independent preview lifecycles.
12. Reset selected/all corrections and close the project; confirm layers and
    plotting resources are cleaned up.

Automated tests use Qt's offscreen platform. Native Retina rendering and OpenGL
interaction must be verified on macOS.
