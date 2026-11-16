# NuCytoSeg Phase 2B.1 projection-based XY cropping

Phase 2B.1 adds an application-owned XY projection and non-destructive crop
workspace. It does not perform normalization, Z correction, segmentation,
matching, stereology, density calculation, or scientific export.

## Project interface

Image Provenance uses a responsive grid with separate original-axes and
original-dimensions fields. Long values wrap, remain selectable, and the full
source path is available as a tooltip. Project sections use the reusable
`CollapsibleSection` component with vector chevrons. Expansion state is stored
only in workspace QSettings and is restored independently of project data.

## Projection and crop semantics

- Maximum and mean projections operate on the selected channel of the current
  standardized CZYX working dataset.
- Integer mean projections accumulate in float64 to avoid overflow. Maximum
  projections preserve source dtype.
- Projection computation runs outside the GUI thread; layer creation remains on
  the GUI thread.
- Rectangle and polygon drawing use an application-owned napari Shapes layer in
  calibrated XY coordinates.
- The crop is the clipped integer bounding box enclosing the selection. Pixels
  outside a polygon are not zeroed, interpolated, or resampled.
- The crop is applied to every channel and every Z plane. Channel order, values,
  dtype, and Z/Y/X spacing are preserved.
- Preview layers and source layers coexist; cancelling removes preview layers
  and restores source visibility.
- Confirming creates a separate working dataset. The imported original remains
  available in memory and Reset Crop restores it without rereading the file.
- Working layers retain the physical translation of their original XY bounding
  box, so the displayed origin is not silently reset.
- Provenance records original and cropped CZYX shapes, integer Y/X bounds,
  selection vertices, spacing, source identity, and physical XY offset.

A polygon drawn on a projection is a crop-selection aid. It is not a validated
3D MNTB ROI and is never used automatically for stereological density.

## Native macOS Apple Silicon validation

```bash
git fetch origin feature/nucytoseg-qt6-shell
git switch feature/nucytoseg-qt6-shell
git pull --ff-only
source .venv-nucytoseg/bin/activate
python -m pip install -e '.[gui,lif,test]'
QT_API=pyqt6 nucytoseg
```

1. Open a real calibrated TIFF and inspect Image Provenance at narrow and wide
   sidebar widths. Confirm long paths do not overlap axes or dimensions.
2. Collapse Image Provenance, Calibration and Axes, and Channel Configuration
   independently. Switch workflows, restart, and confirm the states restore.
3. Open a multiseries Leica LIF, choose one series, and repeat the provenance
   and collapsible-section checks.
4. Open Preprocessing, select each source channel, and generate both Maximum and
   Mean projections. Confirm orientation and calibrated XY scale bars.
5. Draw a rectangle, edit it, clear it, then draw a polygon around the MNTB.
6. Include vertices partly outside the image and confirm the preview clips
   safely to the image boundary.
7. Preview the crop. Inspect every channel and confirm the source layers and
   original volume remain recoverable.
8. Cancel the preview, modify the shape, and preview again.
9. Confirm the crop. Navigate the entire Z stack in 2D and inspect it in 3D.
10. Confirm channel ordering, dtype-dependent appearance, voxel scale, and XY
    spatial offset remain correct.
11. Exercise Review, Paint, and Erase on a Labels layer and confirm crop drawing
    does not invoke mask callbacks.
12. Reset Crop and confirm the complete original CZYX image returns without a
    file reread or changed pixel values.
13. Repeat projection/preview/cancel/apply/reset, then close and reopen the
    application several times without crashes or temporary layers persisting.

Headless tests validate numerical projections, clipping, crop provenance,
transforms, lifecycle, and immutability. Native OpenGL rendering, mouse drawing,
large-volume responsiveness, and real Leica LIF behavior require this checklist.
