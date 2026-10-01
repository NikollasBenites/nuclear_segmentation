# MAP2 merge update

Patch for the nuclear_segmentation_app.zip supplied on October 1, 2026.
Only three existing application files are replaced; no dependency changes.

## Install

1. Save your work and close the application.
2. Copy the three files in `src/nuclear_segmentation/` into the same directory in your app, replacing the existing files. Keep a backup first.
3. If installed in editable mode, restart the app. Otherwise activate your app environment, open a terminal in the project directory containing pyproject.toml, and run:

   python -m pip install --no-deps -e .

4. Launch with `nuclear-segmentation`.

## Workflow

Open DAPI matching and load the masks or a saved matching session. Confirm alignment.
In Review, pick a MAP2 object, then open Merge and click Add selected MAP2 to merge.
Repeat for each fragment. Alternatively type comma-separated IDs directly in the Merge tab.
Keep MAP2 ID must be one of the listed IDs; the first added ID is selected automatically.
Click Merge MAP2 objects and confirm the listed IDs. Inspect the result across Z.
Use Undo in Review to reverse the operation. Save matching and comparison to export.

The operation combines each selected object throughout the complete 3D stack, including
its disconnected pieces. It preserves the selected voxels and does not fill background
or change other labels. A hole occupied by a selected fragment becomes part of the same
object. A background hole remains empty. Verify that all selected IDs belong to one cell.

Existing DAPI links to any merged object point to the retained ID and become manual
associations. Other manual decisions remain unchanged. Automatic overlaps are recalculated
when automatic matching has previously run. Multiple DAPI partners remain visible as
multiple associations; no nuclei are deleted or duplicated. MAP2 volumes are updated.

## Saved outputs

- map2_corrected.ome.tif: corrected MAP2 labels, compatible with the existing matching export.
- map2_merges.csv: one row per input object for each merge event, retained ID and volume.
- dapi_matching.json: merge audit events, updated links, signatures and automatic results.
- map2_before_splits.ome.tif: original labels before the first geometry edit (split OR merge).

For compatibility, both split and merge audit events are stored in the existing
split_history JSON field. Merge events carry operation="merge". Historical events remain
in the audit trail even if later edits change their output IDs. Undo is available in the
current session; reopening a saved session does not restore the interactive undo stack.

These edits affect the DAPI matching workspace. They do not automatically modify another
open segmentation/filter session or its previously exported measurements. Reopen the
corrected labels in the relevant workflow if those measurements need recalculation.

## Validation

11 automated tests passed: voxel conservation, updated links/volumes/automatic overlaps,
invalid selections, save/reopen, merge/split sequences, undo, existing split regression
checks, and an offscreen Qt panel test. Native macOS rendering was not tested here.
