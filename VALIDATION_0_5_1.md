# Validation — 0.5.1

Date: 2026-09-23. Linux, Python 3.12, offscreen Qt. Exact package versions are in `tested-environment-0.5.1.json`.

## Results

- Full suite: **42 passed, 13 subtests passed**, 19 warnings, 17.70 seconds.
- Automatic matching tests cover exact 50% and 20% boundaries, subthreshold overlap, no overlap, deterministic ties, sparse IDs up to 1,000,000, calibrated volumes, and unchanged source arrays.
- Manual decisions survive reclassification. Batch undo restores prior records and thresholds. JSON round-trip preserves automatic results and decision sources.
- Offscreen Napari tests exercise QC colors, original label IDs, group selection, best-candidate selection, manual overrides, plots, saving and reopening.
- Existing tests cover segmentation parameter forwarding, correction, export/resume, optional shells, geometry checks, and manual review. Model inference is mocked in workflow tests.
- Python wheel built successfully without installing model dependencies.

## Scope and limitations

No native macOS/Windows interaction or actual Cellpose inference was tested for this release. Synthetic masks validate the algorithm; biological suitability still depends on the user's masks and overlap thresholds. Large-volume runtime/memory was not benchmarked. Counting runs synchronously and may temporarily block the window; subsequent threshold changes reuse cached counts.

The environment uses an existing scientific Python runtime; this is not a clean installation test of every minimum version in the inherited dependency metadata. Existing working installations should follow the README update instructions. Numba was added for Napari direct-label coloring with large IDs; the test without Numba exposed that Napari limitation, and the full suite passed after installation.

Warnings came from Pydantic deprecation, an intentionally invalid TIFF fixture, and empty shell plot legends. They did not fail tests.

## Command

```sh
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg PYTHONPATH=src python -m pytest -q
```
