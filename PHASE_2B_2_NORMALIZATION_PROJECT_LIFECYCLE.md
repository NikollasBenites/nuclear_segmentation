# NuCytoSeg Phase 2B.2

## Scope

Phase 2B.2 integrates scientific normalization preview and configuration into
the independent Qt6 desktop and adds an application-owned Close Project action.
It does not execute Cellpose, perform Z-intensity correction, or change legacy
measurements, stereology, density, matching, exports, or analysis schemas. The
legacy launcher and optional Leica LIF reader are unchanged.

## Reused scientific implementation

The desktop adapter directly reuses these established functions from
`normalization.py`:

- `compare_percentiles()` for exact original-volume versus polygon-ROI limits,
  including genuine zero-intensity voxels inside the ROI;
- `normalize_preview()` for the unclipped affine preview transformation;
- `mask_digest()` for ROI identity and stale-result detection.

The original source and working CZYX arrays are never normalized in place. A
preview is a separate float32 layer, while an applied normalization is a
project-owned configuration for later segmentation integration. No model input
is created and no segmentation model runs in this milestone.

## Normalization controls and modes

Preprocessing contains a collapsible Normalization section with a channel
selector, mode selector, lower and upper percentile fields, Cellpose `norm3D`,
and Calculate and Preview, Apply Normalization, and Reset Normalization actions.
Channel identity is stored by integer index; user-visible names update after a
rename without changing the associated image plane.

Modes map to the existing Cellpose configuration contract:

- **Cellpose default normalization** stores `CELLPOSE_NORMALIZE = True`.
- **Percentile-based normalization** stores a dictionary containing
  `normalize`, `percentile`, and `norm3D`.
- **ROI-derived fixed limits** stores `normalize`, the calculated `lowhigh`,
  `norm3D`, and `invert=False`.
- **Disabled normalization** stores `CELLPOSE_NORMALIZE = False`.

The Cellpose-default preview is an exact selected-percentile reference (1st/99th
by default) using the legacy affine preview function. It is explicitly not presented as a replay of
Cellpose's internal sampling. Applying that mode stores `True`, not the preview
limits, so future Cellpose execution cannot accidentally normalize twice.

Percentiles must satisfy `0 <= lower < upper <= 100`. Nonfinite values are
rejected by the calculation adapter. `norm3D` records whether future Cellpose
normalization statistics should be computed across the volume; it does not
alter the desktop preview mathematics.

## Polygon ROI statistics

An applied Phase 2B.1.1 polygon is an XY mask extended through all Z planes. It
is not described as a manually segmented 3D anatomical ROI and no stereological
guard zones are applied.

For ROI-derived normalization, the boolean crop mask is mapped back to its
original Y/X bounding box on the immutable original channel volume. This gives
`compare_percentiles()` the original full stack and only genuine voxels inside
the polygon. Artificial zeros used to display the masked crop never enter ROI
statistics, while genuine zero-valued pixels inside the polygon remain included.
Empty, absent, out-of-bounds, or shape-incompatible masks are rejected.

The report records original full-volume and ROI limits, ROI voxel count, outside
fraction, genuine inside-ROI zero fraction, effective limits, relative gain,
source file identity, series, channel index and name, original and working
dimensions, spacing, crop offset, ROI digest, percentiles, mode, `norm3D`, and
calculation method.

## Preview, apply, reset, and invalidation

Only the selected channel is transformed. The preview retains Z/Y/X spacing,
physical XY crop translation, orientation, and Z alignment. For polygon crops,
the established affine mapping is calculated first and display pixels outside
the polygon are then set to zero, separately from the normalization mathematics.
The custom Layers panel groups this layer under Annotations & Overlays.

Large calculations run on a worker thread. Recalculation replaces the prior
preview instead of accumulating full-volume arrays. Reset removes the preview
and restores the default normalization configuration without changing the crop,
ROI, channels, calibration, or source data.

Changing the source channel or its identity, mode, percentiles, `norm3D`, crop,
ROI, or source dataset invalidates the preview and applied-current flag. The UI
requires recalculation before Apply Normalization is accepted.

## Close Project

File → Close Project and the Project panel button use the same application-owned
operation. The action is disabled without a loaded or pending project. Because
reliable save/dirty-state tracking is not available yet, closing uses a
conservative confirmation that offers only Close without Saving and Cancel.

Accepted closure removes every project and temporary layer, mask selection and
highlighting, projection/crop state, normalization previews and configuration,
channel state, pending import state, source-array references, ROI mask, and crop
provenance. It returns to the Project workflow with the same embedded viewer and
preserves window geometry, splitter widths, navigation state, theme, and layout
preferences. No source or result file is modified or deleted.

Close Project and application shutdown are blocked while import, projection, or
normalization workers are active because these workers do not expose a safely
validated cancellation mechanism.

## Automated validation

The Phase 2B.2 tests use synthetic integer and floating-point volumes, genuine
inside-ROI zeros, polygon fill zeros, anisotropic spacing, and nonzero offsets.
They cover mathematical parity, channel rename synchronization, configuration
mapping, preview/apply/invalidate/reset, crop invalidation, layer grouping,
array immutability, cleanup, close cancellation, active-worker blocking,
reopening, repeated close cycles, layout preservation, and filesystem safety.

Run the focused tests:

```bash
python -m pytest tests/test_normalization.py tests/test_desktop_normalization.py -q
```

Run the desktop/scientific suite:

```bash
python -m pytest -q --ignore=tests/test_workflow.py
```

The cloud napari 0.10 environment reports 114 passed tests and 13 passed
subtests for the latter command. The pre-existing legacy
`test_launcher_roundtrip` aborts in napari's native Qt-window close path under
the headless backend and is reported separately rather than changing scientific
assertions.

## macOS installation and launch

```bash
git fetch origin feature/nucytoseg-qt6-shell
git switch feature/nucytoseg-qt6-shell
git pull --ff-only
source .venv-nucytoseg/bin/activate
python -m pip install -e '.[gui,test]'
QT_API=pyqt6 nucytoseg
```

Add the `lif` extra only if optional Leica support is required. TIFF is the
validated input for this milestone.

## Native macOS verification checklist

1. Open a calibrated TIFF.
2. Select the DAPI channel.
3. Calculate full-volume normalization.
4. Preview normalized intensities.
5. Change percentile limits.
6. Verify that the preview is invalidated and Apply is unavailable.
7. Draw a polygon around the MNTB.
8. Apply the crop.
9. Select ROI-derived fixed limits and calculate normalization.
10. Compare ROI and original full-volume intensity limits.
11. Verify that black fill outside the polygon does not influence ROI statistics.
12. Inspect the normalized preview across the complete Z stack.
13. Apply normalization settings and confirm that no model execution is claimed.
14. Reset normalization.
15. Confirm that the crop, ROI, calibration, channels, and source pixels remain.
16. Choose Close Project and review the unsaved-change warning.
17. Confirm closure and verify that the persistent viewer workspace is empty.
18. Open another calibrated TIFF immediately.
19. Verify that no layers, selections, ROI, or normalization settings leaked from
    the previous project.
20. Close and restart NuCytoSeg and verify that general workspace layout settings
    remain while project data does not return.

Native macOS OpenGL behavior and large real microscopy volumes were not
available in the cloud environment and are not claimed as validated.
