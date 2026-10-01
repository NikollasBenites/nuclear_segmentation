# Delete MAP2 objects in DAPI matching

Patch for your October 1 application, including the previously supplied MAP2 merge changes. Does not replace the segmentation-mode configuration files (do_3D/stitching).

## Installation
Save your work and close the app. Back up and replace the four supplied files in src/nuclear_segmentation/, retaining the rest of your project.
For editable installations, restart the app. Otherwise activate your environment and run from the project directory:

    python -m pip install --no-deps -e .

## Use
1. Load masks or open a saved DAPI matching session; verify alignment.
2. In Review, select the MAP2 object by picking it or entering its MAP2 mask ID.
3. Click Delete MAP2 (also available under DAPI Matching > Segment).
4. Check the ID in the confirmation dialog. Confirm to delete the complete 3D object, including all disconnected pieces with that ID.
5. Inspect the result and use Save matching and comparison to export.

Undo in Review restores geometry, links, volumes, automatic results and audit history for the last operation. Undo history is session-only and is not restored when reopening saved matching.

All voxels of the deleted ID become background (zero). Other masks and DAPI voxels remain unchanged. Linked nuclei become Uncertain with a review comment and no MAP2 link, rather than being declared biologically unmatched. Their decision source becomes manual so automatic matching will not overwrite this pending review. Other manual decisions are preserved. Existing automatic overlap results are recalculated. If automatic matching has not run, deletion does not start it.

Accept or cancel a pending split preview before deleting. It is possible to delete the last MAP2 object and save/reopen that session; DAPI must still contain objects.

## Outputs
The existing map2_corrected.ome.tif contains the edited labels. Updated links are saved in dapi_matching.csv and dapi_matching.json. map2_deletions.csv records each deleted ID, removed voxel count and volume, affected DAPI IDs, and UTC timestamp.

For backward compatibility, geometry edit events (split, merge, delete) use the existing split_history JSON field. Delete events carry operation="delete" and preserve prior links and mask signatures. The backup map2_before_splits.ome.tif contains the original mask before the first geometry edit, regardless of edit type.

These changes apply to the matching workspace, not other open segmentation sessions or older exported measurements. Use corrected labels in the appropriate workflow to recalculate those measurements.

## Validation
34 automated tests passed across deletion, merge, split core behavior, configuration and provenance. Coverage includes confirmation cancellation, offscreen Qt button callbacks, save/reopen, empty MAP2 export, undo, and preservation of DAPI voxels. Native macOS rendering and GPU segmentation were not tested here.
