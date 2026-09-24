# Nuclear Segmentation 0.6.0

A Napari desktop application for 3D segmentation, intensity measurements,
optional perinuclear shells, and calibrated MNTB density. This release is based
on the user-edited 0.3.0 source, including its support for no enabled shells.
The package contains Python scripts only; no notebooks are required or bundled.

## Update an existing installation

Extract this ZIP and open a terminal in the directory containing `pyproject.toml`.
Close the running application before updating, then use your working environment:

```sh
conda activate nuclearapp_latest
python -m pip install numba
python -m pip install -e . --no-deps
nuclear-segmentation --version
nuclear-segmentation
```

The version should be `0.6.0`. Editable installation uses this source directory;
keep it in place. Subsequent Python text edits appear after restarting the app.
`--no-deps` preserves the dependencies in an existing working environment.
Install `numba` first as shown: Napari needs it to display direct QC colors for
large object IDs without renumbering the masks.

For a new environment, use Python 3.12 or newer and install the GUI and model:

```sh
conda create -n nuclearapp_latest python=3.12
conda activate nuclearapp_latest
python -m pip install -e '.[gui,segmentation]'
nuclear-segmentation
```

PyTorch/Cellpose are needed for new segmentation, not for loading existing masks.
This package is not a standalone macOS `.app` or Windows `.exe`.

## Correct Z from a selected layer

1. Select **New segmentation**, the original cropped TIFF, and its channels.
2. Click **Load layers / Z correction / normalization**. This loads images and
   an estimated or saved ROI without running Cellpose.
3. In the Napari layer list select an original channel, for example
   **Original: MAP2**. Open the **Z correction** tab.
4. Adjust the reference percentile, smoothing sigma, and gain bounds if needed.
5. Click **Correct Z of selected layer**. The result is named
   **MAP2 — Z corrected**. Toggle layer visibility to compare images.
6. Optionally click **Show selected Z profile** to inspect the reference and gain.
7. Choose the intended destinations and click **Apply selected correction to analysis**:
   - **Use corrected channel for segmentation** selects that channel as the model
     input. This release uses one segmentation channel at a time.
   - **Measure this channel on the corrected image** changes that channel's
     intensity measurements, background estimates, intensity filters, and shell
     measurements to the corrected data. It is **off by default**.
8. If you edited the ROI, click **Save reviewed ROI for analysis…** to preserve
   those edits and select the saved mask for the analysis.
9. Click **Start analysis**. Correction is recomputed from the original TIFF with
   the applied settings. Review filters and accept the ROI before exporting.

The correction button also works when its corrected output is selected; it
always starts from the corresponding original image, so gains do not accumulate.
It accepts the original channel layers loaded by this preview and their corrected
outputs, not arbitrary unrelated or resampled layers. Z must be axis 0.
Changing parameters requires a new preview and another Apply. Merely creating a
preview does not change the model input. When correcting several channels,
repeat Apply for each channel; the last one applied with segmentation checked
becomes the model channel. The segmentation-channel field is also available in
Parameters. Unchecking segmentation for a previously corrected primary channel
and applying selects its original intensities again.

Correction preserves shape and voxel coordinates. Preview layers copy scale,
translation, rotation, shear, affine transform, and units. Analysis requires the
original calibrated grid; transformed layers must be restored before applying.
Original pixel arrays are not overwritten. Do not manually edit raw image values
or corrected preview data: the analysis regenerates correction from the TIFF.

## What the correction does

For each Z plane, calculate the selected percentile of strictly positive pixels
(default p99). Interpolate missing reference values through empty planes, using
the nearest valid reference at the ends. Smooth that profile with a Gaussian
(default sigma 3 **planes**, SciPy's reflected boundary mode). Set the target to
the median smoothed reference, then compute:

```text
gain[z] = clip(target / smoothed_reference[z], minimum_gain, maximum_gain)
corrected[z, y, x] = original[z, y, x] * gain[z]
```

Default gain bounds are 0.5 and 3.0. Output is float32, without intensity clipping.
Zero pixels remain zero. Entirely nonpositive inputs and nonfinite data produce
a clear error. All positive pixels in the cropped image contribute; this method
does not use the reviewed ROI or the Z guards to estimate its reference.

This compensates a brightness profile, not optical resolution loss. Tissue
composition changes can also change p99, so review the profile before applying.
Intensity thresholds may need adjustment when measurements use corrected data.
A MAP2 segmentation may represent somas or other labeled structures rather than
nuclei. Legacy output filenames and density columns still say `nuclei`; interpret
them as counts of the segmented objects and validate their biological identity.

## Correction versus Cellpose normalization

Multiplicative Z correction and Cellpose normalization are separate operations.
The corrected volume enters `model.eval`; `CELLPOSE_NORMALIZE` remains unchanged.
The default Cellpose normalization can therefore still run after Z correction.
Model settings including normalize, resample, and rescale remain available in
**Advanced settings**.

The **ROI percentile normalization** tab preserves the previous global ROI-limit
workflow. Its `lowhigh` limits refer to the original image. This release prevents
combining those saved limits with Z correction: reset ROI normalization before
applying Z correction, or reset all Z corrections before calculating ROI limits.
It does not silently reuse limits calculated on another input.

## Masks, measurements, and reopening

One mask grid is used with both original and corrected volumes; no registration,
resampling, or second segmentation is needed. By default, model input can be
corrected while measurements use the original channels. The optional corrected
measurement choice is per channel and applies when the analysis is prepared.
It does not dynamically remeasure an already running session.

Export records settings and full gain profiles in `analysis_config.json` and
`z_correction_profiles.csv`. **Resume analysis / adjust filters** loads the saved
raw masks, restores correction settings, recomputes the corrected volumes, and
remeasures the selected intensity source without running Cellpose. Original
images are required. Old exports without correction metadata remain supported.
**Previous analysis: density only** retains the prior counts and measurements.

The viewer shows original images and additional corrected layers. To change the
measurement source after export, start a new analysis with the desired settings;
resume intentionally restores the source recorded in that export.

## Density

Density uses object centroids within the reviewed ROI and the effective Z range.
ROI tissue volume describes the anatomical mask. Effective volume is the ROI
volume in the counting range after tissue limits and guards. The live first and
last tissue planes determine full measured thickness before guards.

Counts per 100 × 100 × 100 µm cube use a volume of 1,000,000 µm³. The nominal
thickness correction remains based on the chosen nominal thickness (default
60 µm), assuming uniform Z compression and unchanged XY area. Z intensity
correction does not alter voxel size, tissue geometry, or this thickness rule.
Results are in `mntb_density_summary.csv` and `analysis_summary.csv`.

## Source layout and validation

- `z_correction.py`: correction algorithm and data routing.
- `z_correction_ui.py`: selected-layer controls and previews.
- `normalization_ui.py`: pre-segmentation image/ROI viewer.
- `runtime.py`: model, measurement, resume, and viewer orchestration.
- `stages/shells.py`: optional shell measurements, including zero markers.
- `ui.py`: launcher and text labels.
- `tests/`: synthetic numerical, UI, export, and resume checks.

See `VALIDATION_0_4_0.md` for what was tested and remaining limitations.


## Image export (0.4.1)

The **Z correction** panel provides two immediate export buttons:

- **Save corrected TIFF…** saves the last computed correction for the selected
  original channel or its corrected output. Applying it to analysis is not
  required. The currently displayed preview data are saved.
- **Export selected image layers as multichannel TIFF…** exports the original
  and/or corrected channels selected in Napari. Use Command-click on macOS or
  Ctrl-click on Windows to select multiple layers. A dialog lists channel order;
  drag rows to reorder them, with channel 1 at the top. For example: original
  DAPI, corrected MAP2, original VGLUT, original CC3.

Only images loaded by this preview and its corrected outputs are supported.
ROI and segmentation labels remain separate. All channels must share the same
ZYX shape, calibration, and unmodified grid transforms; otherwise export stops
with an explanation. Colors, contrast limits, and visibility do not change the
exported pixels.

**Include corrected images in analysis export** is available both in the preview
and in the launcher's Parameters tab. It defaults to off. When enabled, normal
analysis export writes all applied corrected channels to `corrected_channels.ome.tif`
and their provenance to `corrected_channels.ome.tif.json`. It works with new
segmentation and resume; density-only does not regenerate corrected images.
The checkbox can be changed in Parameters after segmentation, before export.
It controls files only and does not change the measurement source.

Exports use float32 OME-TIFF, physical X/Y/Z sizes in micrometers, explicit
channel names, and a companion JSON with correction parameters and Z gains.
Raw channels are converted to float32 when combined with corrected channels.
No intensity scaling, clipping, or display color rendering is applied. Output
is uncompressed; allow approximately four bytes per voxel per channel, plus
metadata. Large outputs use BigTIFF automatically. Save operations are synchronous
and can temporarily block the interface on large volumes.

Choose a new filename for each export; existing files and the original TIFF
are protected from replacement. The filename suffix is `.ome.tif` (added if
needed). To inspect multichannel metadata in Fiji, use its Bio-Formats importer.
A reopened corrected TIFF already contains the Z correction: do not apply it
again unless that is intentional. The JSON is provenance, not an automatic
instruction to correct the saved TIFF a second time.

Validation for these additions is recorded in `VALIDATION_0_4_1.md`.


## Manual mask review (0.4.2)

After a new segmentation or **Resume analysis / adjust filters**, a scrollable
**Manual mask review** panel opens alongside the live filters.

1. Review and accept the MNTB ROI. Set the tissue Z limits and guards.
2. Click **Select from all masks**. The app switches to 2D slice view, activates
   the original Cellpose labels, and shows all objects, including filtered ones.
3. Navigate to a slice and click an object. A single left click selects its
   complete 3D label; it does not paint or change the mask. You can also enter
   its ID directly in **Mask ID**.
4. Inspect its measurements, automatic filter failures, current status, and
   manual decision. Enter an optional reason.
5. Use **Keep selected mask**, **Exclude selected mask**, or
   **Use automatic filters**. **Undo last decision** restores the preceding
   manual state. The undo history covers this session only.
6. **Show selected mask only** isolates the ID on raw/live mask layers.
   **Show retained masks** switches back to the final filtered result. The raw
   layer intentionally continues to contain excluded objects for review.
7. Export results to save decisions. Exporting supports zero retained objects.

Keep overrides volume, sphericity, intensity, and shell association criteria.
It never overrides the Z guard, centroid membership in the reviewed ROI, or the
counting Z interval. A prior Keep remains recorded when later boundary changes
make an object ineligible, but the object is excluded until eligible again.
Manual exclusions persist when sliders change. ROI edits require acceptance
again before new decisions can be recorded or a valid density exported.

Decisions update the live retained labels, associated shell layers, count, and
density. Raw Cellpose labels and the live result are protected against painting;
geometry editing is outside this feature. Click selection is supported in 2D
slice views; use the ID field when inspecting in 3D.

Exports include `manual_mask_review.csv` (one row per original mask, decision,
reason, automatic criteria, boundary exclusions, and final retained state).
`all_mask_properties.csv` includes the review fields, and
`analysis_config.json` stores decisions plus a fingerprint of raw label geometry.
`filtered_masks_syglass.tif` reflects the final reviewed selection without
renumbering IDs. Raw masks remain unchanged. Morphology and intensity failure
columns report automatic criteria even when a manual Keep overrides them.

Resume restores decisions and remeasures the original masks without rerunning
Cellpose. A mask fingerprint mismatch stops restoration rather than applying
IDs to a different segmentation. Old analyses without manual metadata open with
all objects under automatic control. Density-only mode uses the exported retained
masks; switch to Resume for reviewing excluded objects. A newly started
segmentation begins with no manual decisions.

Save by exporting before closing or replacing an analysis. Decisions are not
autosaved; the existing view-replacement prompt now reminds you to export them.
This release adds manual review only; the image exporter retains the float32
OME-TIFF behavior of 0.4.1.

## DAPI matching (0.5.0)

Click **Open DAPI matching** in the launcher. A separate Napari window opens;
matching does not modify the active segmentation, Keep/Exclude decisions, shells,
or density. No Cellpose run is needed. Only whole-object associations are edited.

1. Browse to the existing DAPI and MAP2 integer label TIFFs, then **Load masks**.
   Use the same original voxel grid, crop, axis order and physical calibration.
   If metadata are missing, enter known fallback spacing as Z,Y,X in micrometers.
   Existing calibration metadata take precedence. Shape or spacing mismatches
   are rejected. This feature does not register images.
2. Inspect alignment across Z and check the alignment confirmation box. Equal
   dimensions alone do not prove registration. **Add reference image TIFF…**
   loads a calibrated intensity stack with the same grid below the masks.
   Use Napari visibility/contrast controls to choose reference channels.
3. Enter a sample ID and, if available, animal ID.
4. Click **Pick DAPI object**, then click its mask in a 2D slice. Click
   **Pick MAP2 object**, then its corresponding mask. You may enter IDs directly.
   Selection does not paint or change geometry.
5. Click **Link selected objects**. Each DAPI ID has at most one MAP2 partner;
   relinking replaces its previous partner. Several DAPI IDs may share one MAP2
   object, and the panel flags this. Inspect possible merged MAP2 segmentations.
6. **Mark DAPI as unmatched** means reviewed without a partner; **Mark DAPI as
   uncertain** records an ambiguous case; **Remove link / Reset review** returns
   it to **Not reviewed**. Optional comments are saved with decisions.
   **Undo last decision** restores the previous record; history is session-local.
7. **Show selected objects only** isolates the selected pair. Selecting a reviewed
   DAPI ID restores its recorded MAP2 partner. Selected MAP2 candidates and recorded
   links are identified separately. Volumes are hidden by default; **Show volumes
   during matching** reveals them.
8. **Compare DAPI volumes** shows a histogram with shared bin edges, cumulative
   distributions, n, median and IQR. Histogram mode can show counts or fraction
   of each group. The measured variable is DAPI nuclear mask volume, not MAP2 volume.
9. **Compare one-to-one matches only** excludes matched DAPI objects sharing a MAP2
   partner. Unmatched remains unchanged. Uncertain and Not reviewed never enter
   either comparison group.
10. **Save matching and comparison…** creates a new timestamped folder. Use
    **Open saved matching…** with its JSON to resume. Original masks are required;
    moved files can be located manually. Geometry fingerprints and calibration
    are checked before restoring associations.

Outputs: `dapi_matching.json` stores sources, decisions and settings;
`dapi_matching.csv` contains one row per DAPI object with both IDs, status,
physical volumes, multiplicity, sample/animal IDs and comment;
`dapi_volume_summary.csv` contains n, median, quartiles and IQR;
`dapi_volume_distributions.png` and `.pdf` contain the plots.

Volume equals mask voxel count times calibrated voxel volume. The population is
exactly the objects in the loaded TIFF: using a filtered DAPI export excludes
previously rejected nuclei. Load raw masks if you intend to review all nuclei.
Supply instance labels; all voxels sharing an ID count as one object, even if
they form disconnected components. Binary images cannot identify individual
objects that share the same label.

Matching is a manual annotation, not automatic neuronal classification. Check
source channels when MAP2 segmentation is incomplete. Reviewing only selected
sizes can bias distributions; keeping volumes hidden can help. Comparisons are
descriptive for one sample, not inferential tests that pool nuclei from animals
as independent biological replicates. Sample and animal IDs support downstream
analysis at the appropriate biological level.

Save before closing; unsaved changes trigger a discard confirmation. Loading,
fingerprinting, statistics and export are synchronous and may briefly block the
UI for large masks. No geometry editing or automatic registration is included.

## Automatic DAPI/MAP2 matching (0.5.1)

This implements the voxel-overlap console workflow from September 22, 2026.
Meshes, centroid tests, proximity searches, dilation, and hole filling are not
used. It operates on the two pre-existing label volumes in the same grid.

In **Open DAPI matching**, load both TIFF masks and confirm alignment. The
original intensity TIFF is optional visual context. Set:

- **Minimum best DAPI overlap fraction**: default `0.50`.
- **Maximum runner-up DAPI fraction**: default `0.20`.

Click **Run automatic matching**. All loaded nuclei are classified in one run;
individual confirmation is not required. For each DAPI ID, intersections with
all MAP2 IDs are counted. The best and second-best MAP2 objects are ranked by
intersection voxel count. Both fractions use the **entire DAPI mask** as their
denominator, not MAP2 volume and not the sum of overlapping voxels.

| Automatic class | Rule | QC color |
| --- | --- | --- |
| MATCH | Best fraction >= 0.50 and runner-up <= 0.20 | Lime green |
| AMBIGUOUS | Some overlap, but either condition above fails | Yellow |
| NO_MATCH | No overlap with any MAP2 label | Red |

Thresholds are adjustable. Equal-overlap ties are ordered by smallest MAP2 ID;
with default settings a 50/50 split is ambiguous. IoU and Dice are exported for
information only and do not determine the class. Distances and centroids are
not used, preserving the console method. No ROI/guard or intensity filters are
added here: the population is exactly the DAPI objects in the loaded file.

A read-only **DAPI_MATCH_QC** layer preserves original DAPI label IDs with
transparent background. **Show automatic group** displays all nuclei or only
MATCH, AMBIGUOUS, or NO_MATCH. Original label arrays remain unchanged. Original
DAPI/MAP2 label layers are hidden when QC is shown to avoid mixed overlay colors;
they can be toggled on for inspection.

**Automatic proportions and volumes** shows group proportions (denominator:
all loaded DAPI objects), shared-bin volume histograms in µm³, and cumulative
volume distributions for the three automatic classes. These figures always
show automatic results before manual corrections and include ambiguous objects
as their own group. The existing **Compare DAPI volumes** button compares the
final Matched/Unmatched records, after manual corrections and its optional
one-to-one filter. Its Uncertain/Not reviewed objects remain excluded.

Automatic results also populate the matching table: MATCH becomes Matched with
the best partner, AMBIGUOUS becomes Uncertain, and NO_MATCH becomes Unmatched.
These are automatic classifications, not claims of visual confirmation.
`decision_source` explicitly records automatic versus manual decisions.
Existing manual decisions are preserved on every automatic run. Automatic
records are recalculated if thresholds change and you run again. Editing a
threshold alone does not update results; the panel displays the last used
thresholds and export records those settings.

For ambiguous objects, select the DAPI ID and click **Select best overlap
candidate** to highlight the best available pair. Then use the existing manual
buttons to confirm, reject, or mark it uncertain. Selecting a candidate alone
does not create a link. Zero-overlap objects have no candidate. **Undo last
decision** can undo an entire automatic run, restoring both previous decisions
and the prior automatic results. Resetting a decision to Not reviewed allows
it to be classified on the next automatic run.

Save with **Save matching and comparison…**. In addition to existing review
outputs, the export contains:

- `automatic_matching.csv`: best/runner-up IDs, overlap voxels and µm³, best,
  runner-up and total DAPI overlap fractions, number of overlapping MAP2
  objects, IoU/Dice, DAPI volume and automatic class.
- `automatic_group_proportions.csv`: class counts and fractions.
- `automatic_volume_summary.csv`: counts, medians and volume ranges by class.
- `automatic_matching.png` and `.pdf`: automatic proportion/volume plots.
- `dapi_matching.json`: automatic settings and results plus final review records,
  calibration, and geometry fingerprints. Older manual-only JSON files remain
  supported. Open this JSON to restore the QC layer without another matching run.

Results remain geometrical associations, not automatic neuronal identity.
A MAP2 mask that surrounds a nucleus but contains a central hole may have little
or zero voxel overlap. Such a nucleus can be manually linked after inspection.
Do not silently interpret NO_MATCH as non-neuronal. Use consistent thresholds
across samples and account for any previous filtering of the loaded masks.

Overlap counting is plane-wise with sparse label pairs, avoiding allocation of
a dense max-label-ID matrix. Counts are cached for threshold adjustments within
the same workspace. The first calculation is synchronous and may temporarily
block the interface on large stacks; original masks are never rewritten.


## Live multiple-association inspection (0.5.2)

Save your current matching before closing the old app and installing the update.
Open the saved `dapi_matching.json` in the new version to continue reviewing.

In **DAPI matching**, enable **Show multiple associations**. Two read-only label
layers show every DAPI object currently linked to a MAP2 object with at least two
DAPI partners, and the complete shared MAP2 objects. Original IDs and calibration
are preserved. This uses recorded Matched decisions, both automatic and manual;
ambiguous overlap candidates without confirmed links are not included.

The count and layers update after Link, Unmatched, Uncertain, Reset review, Undo,
and automatic reclassification. Resolving a shared association removes that
entire resolved group from this view. No source masks or decisions are changed by
toggling this option. With no multiple associations, both inspection layers are
empty and the panel says so.

Click an inspection layer in the layer list and click an object in a 2D slice to
select its original ID for the normal decision buttons. You can also use the ID
fields. Colors identify labels, not automatic match status. The same color across
the two layers does not establish a link; inspect the recorded MAP2 ID in the
panel. Image reference layers remain available.

This mode shows all multiple associations together and disables **Show selected
objects only**. Enabling single-object isolation, Pick DAPI, Pick MAP2, or Select
best overlap candidate exits the multiple-association view. Unchecking the option
restores the previous matching-layer visibility. **Show automatic group** applies
to the separate automatic QC layer; the multiple view continues to use current
recorded links.

Use **Save matching and comparison…** to preserve your decisions. Existing CSV
columns `dapi_per_map2` and `multiple_association` record the shared links. Reopen
saved matching and enable the checkbox again; the inspection layers are rebuilt
from the saved decisions. The checkbox itself is a temporary display preference.


## Preview and accept a 3D MAP2 split (0.6.0)

Save your matching in the previous version, close the application, install this
version from its new folder, and reopen `dapi_matching.json`. Existing matching
files remain readable. No new dependency is required beyond version 0.5.2.

1. Confirm mask alignment. Add an original MAP2 reference image if available.
2. Identify a shared MAP2 with **Show multiple associations**. Select a linked
   DAPI or enter the parent MAP2 ID in **MAP2 mask ID**. The parent must contain at least two valid DAPI seeds. Like the console script,
   seeds are selected by overlap, not by recorded match status. The minimum
   fraction of each DAPI inside MAP2 defaults to 0.50 and is adjustable in the panel.
3. Click **Preview MAP2 split from DAPI**. A separate editable Labels
   layer appears, with the proposed child IDs listed in the panel. Nothing in
   the analysis is changed yet. The original DAPI layer can be made visible with
   its eye icon while inspecting the preview and reference image across Z.
4. Optionally paint between the listed child IDs in the preview. Keep the exact
   original MAP2 footprint and all children; do not erase voxels, paint outside
   the parent, introduce other IDs, or change the DAPI overlap seeds. Do not move,
   rotate or rescale the preview layer. Violations prevent acceptance.
5. Click **Accept MAP2 split**, or **Cancel split preview**. Acceptance replaces
   only the selected MAP2 object, assigning fresh IDs. DAPI geometry and IDs are
   unchanged. Previously Matched seed partners are linked to their respective children
   and recorded as manual decisions. Other manual decisions are preserved.
   A previously linked DAPI that fails the seed criterion becomes Uncertain
   with no partner and a review-required comment, because its old MAP2 ID no
   longer exists. Unreviewed nuclei remain unreviewed; automatic decisions are
   refreshed if automatic matching has previously been run. Volumes and automatic overlap metrics are
   recalculated; unrelated manual decisions are preserved. Existing automatic
   decisions are refreshed using the last thresholds.
6. Use **Undo last decision** to reverse the accepted split, including its
   geometry, IDs, links, automatic results and provenance. Undo is session-only;
   reopening restores saved state without the in-memory Undo history.
7. **Save matching and comparison…** exports the updated analysis. A pending
   preview must be accepted or cancelled first.

Changing decisions, running automatic matching, Undo, selecting a best candidate,
using Pick, or enabling the multiple-association view cancels a pending preview.
Generate it again after changing links. Closing/replacing a workspace with an
unsaved preview or unsaved decisions asks before discarding it.

### What the split computes

This uses the console method: marker-controlled 3D watershed on the negative
MAP2 distance transform, calculated locally in the selected object's bounding
box. `distance_transform_edt(local_map2_mask, sampling=spacing)` supplies the
surface, then `watershed(-distance, markers=markers, mask=local_map2_mask)` divides
it. Each marker is the full intersection of a valid DAPI nucleus with the MAP2.
A valid seed has at least 50% (adjustable) of its total DAPI volume inside that
MAP2. No centroid requirement or minimum DAPI volume filter is applied.

The union of the children is exactly the parent. Unchanged MAP2 objects retain
their IDs; children receive IDs above the current maximum. This does not use the
original MAP2 intensity or run Cellpose. It does not guarantee a biological cell
boundary or connected children, so inspect the result across Z before accepting.

An extra safeguard rejects any disconnected MAP2 component without a valid seed,
rather than silently losing voxels. Multiple association alone is not evidence
of two real cells. Splits are proposed one selected MAP2 at a time, never accepted
in bulk. DAPI IDs and volumes used for comparisons remain unchanged.

### Files saved after a split

The matching export folder contains:

- `dapi_masks.ome.tif`: unchanged DAPI labels.
- `map2_corrected.ome.tif`: complete MAP2 volume with accepted splits, uint32 labels.
- `map2_before_splits.ome.tif`: MAP2 before the first accepted split in the lineage.
- `dapi_matching.json`: current links, calibration, mask hashes, relative mask paths
  and `split_history` (parent/child IDs, seed DAPI IDs, timestamp, method and whether
  the proposed partition was edited before acceptance).
- `map2_splits.csv` and `split_overlaps.csv`: parent/child IDs and seed overlap fractions.
- The usual matching CSV tables and comparison plots, based on the current masks.

Original input TIFFs are never overwritten. Keep this folder together when moving
it; Open saved matching prefers these relative paths. Reopened split sessions can
be edited and saved again, preserving the original backup and accepted split log.
The split export uses 32-bit integer labels to avoid ID overflow; support in other
viewers should be checked before converting masks to a smaller integer type.

The QC layer still represents automatic classification; manual Matched decisions
need not have the same color as their automatic class. Splitting recalculates the
automatic class on the corrected geometry but does not change that distinction.
Operations run synchronously and hold preview/Undo arrays in memory; large masks
may temporarily block the viewer and require additional RAM.
