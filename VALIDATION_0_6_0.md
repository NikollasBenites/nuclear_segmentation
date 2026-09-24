# Validation — 0.6.0

Date: 2026-09-24. Linux, Python 3.12, offscreen Qt.

Full suite: **48 passed, 13 subtests passed**, 19 warnings, 50.45 seconds.
The final wheel was rebuilt after the uint32 label-display fix.

New tests cover watershed equivalence to the retrieved console algorithm,
calibrated distance-transform input, seed fraction eligibility, original ID and
volume preservation, unchanged DAPI, optional preview edits, invalid edits,
stale links, seedless components, manual decision preservation, IDs crossing
65535, undo of geometry and links, corrected TIFF export, moved-folder reopen,
backup preservation on resave, and GUI preview/cancel/accept/undo/reopen.

During development a uint16 inspection layer rejected child IDs above 65535.
MAP2 display and multiple-association layers now use uint32, while source
files remain unchanged. The full suite above passed with this fix.

The split is marker-controlled watershed on the negative physical MAP2 distance
transform in its bounding box, with whole DAPI/MAP2 intersections as markers.
Default seed eligibility is at least 50% of the total DAPI volume. A separate
post-suite test additionally checks inclusion at the exact 50% boundary.

No native macOS/Windows GUI, biological boundary accuracy, large-volume memory
benchmark, or real Cellpose inference was tested. Inference is mocked in workflow
tests. The existing environment is not a clean installation test of all declared
minimum dependency versions. Exact runtime versions are recorded alongside this
report. Warnings were Pydantic deprecation, an intentionally invalid TIFF fixture,
and empty-shell plot legends.

```sh
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg PYTHONPATH=src python -m pytest -q
```
