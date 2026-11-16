# NuCytoSeg Phase 2A.3 project and channel configuration

Phase 2A.3 completes the authorized Phase 2A desktop-import scope. It does not
connect imported arrays to segmentation, preprocessing, measurements, exports,
or the legacy scientific runtime.

## Project and calibration state

- Imported source identity, series identity, original/reported dimensions,
  standardized CZYX dimensions, dtype, time point, and acquisition metadata are
  held in application project state.
- Project state is separate from workspace-layout `QSettings` and from the
  existing analysis/export configuration schema.
- Metadata calibration and user-confirmed manual calibration remain explicitly
  identified. Applying a manual Z/Y/X override updates calibrated napari layers
  and scale bars without changing image arrays.
- New Segmentation, Resume Analysis, and Density Only are represented as project
  choices only. Execution is intentionally unavailable in Phase 2A.

## Channel configuration

Each source channel retains its immutable source index and original reader name.
The editable configuration provides a display/analysis name, role, colormap,
visibility, and one explicit segmentation-channel designation.

- Valid roles are `segmentation`, `measurement`, and `reference`.
- Arbitrary marker names are supported; DAPI and MAP2 are not hardcoded.
- Empty, duplicate, case-insensitive duplicate, and analysis-safe-name collisions
  are rejected.
- Choosing a new segmentation channel demotes the previous one to measurement.
- The final segmentation channel cannot be removed without assigning another.
- Names and visibility synchronize bidirectionally with the custom layer panel.
- Name, role, colormap, visibility, and calibration changes do not copy or alter
  pixel arrays.
- A legacy-compatible in-memory `CHANNELS` record and `SEGMENTATION_CHANNEL`
  name can be derived for later runtime integration. Existing schemas are not
  changed or written in Phase 2A.

Recent Projects remains intentionally unimplemented because reliable project
persistence is outside this milestone. No image volume is stored in QSettings.

## Native macOS Apple Silicon checklist

```bash
git fetch origin feature/nucytoseg-qt6-shell
git switch feature/nucytoseg-qt6-shell
git pull --ff-only
python3.12 -m venv .venv-nucytoseg
source .venv-nucytoseg/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[gui,lif,test]'
QT_API=pyqt6 nucytoseg
```

1. Open a real single-channel TIFF and confirm missing calibration blocks loading
   until explicit Z/Y/X values are entered.
2. Open a calibrated multichannel OME-TIFF. Verify channel order, dimensions,
   dtype, Z navigation, anisotropic scale bar, and 2D/3D display.
3. Confirm each channel appears as a separate ZYX Image layer and the viewer is
   not recreated when switching workflows.
4. Rename channels from both Channel Configuration and Layers. Verify both views
   synchronize and duplicate names are rejected.
5. Change roles and explicitly choose the segmentation channel. Try arbitrary
   nuclear, cytoplasmic, measurement, and reference marker names.
6. Change colormaps and visibility; confirm pixel values remain unchanged.
7. Apply a manual calibration override and verify the scale bar and anisotropic
   display update without resampling.
8. Open a real multiseries Leica Stellaris LIF. Confirm Image Series Browser
   lists acquisition names, C/Z/T sizes, objective, date, and calibration where
   genuinely available.
9. Select one MNTB acquisition and confirm only that series/time point loads.
10. Treat LUT names such as `Green` only as editable source labels; verify against
    acquisition metadata instead of assuming a fluorophore.
11. Confirm mosaic series are clearly blocked rather than silently stitched.
12. Open another image and cancel the replacement warning; confirm the current
    project remains intact.
13. Exercise Review selection and the Phase 1 viewer controls, then close and
    reopen the application repeatedly.

Real LIF parsing and native OpenGL behavior remain pending this checklist because
the development environment had no representative Leica file or native display.
