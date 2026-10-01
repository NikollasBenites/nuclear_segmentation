# Editable 3D and stitching settings

Patch for the October 1 nuclear_segmentation_app.zip. Compatible with the MAP2 merge patch; no matching files are replaced. Four files are included with their project-relative paths.

## Install
Save your work and close the app. Back up the four corresponding files and copy the supplied src directory contents into your project's src directory, replacing only those files. Do not delete the rest of your project.

For an editable installation, restart the app. Otherwise, activate your environment and run this from the project directory containing pyproject.toml:

    python -m pip install --no-deps -e .

## Settings
Open Advanced settings and edit these existing JSON entries together. JSON uses lowercase true/false.

Default 3D:

    "CELLPOSE_DO_3D": true,
    "CELLPOSE_STITCH_THRESHOLD": 0.0,

2D per slice with stitching (starting point for comparison, not a validated optimum):

    "CELLPOSE_DO_3D": false,
    "CELLPOSE_STITCH_THRESHOLD": 0.25,

Apply with OK. Choose New segmentation and run the same corrected input volume for comparison. Resume does not rerun Cellpose. Save preset preserves both options. Existing presets without these keys use the original 3D defaults.

The advanced editor automatically displays fields from defaults.json; no UI replacement is needed.

## Behavior
- 3D: passes do_3D=True and stitch_threshold=0, regardless of the stored threshold. Existing flow3D_smooth settings remain active.
- 2D + stitching: passes do_3D=False and your positive stitching threshold. flow3D_smooth is omitted because it applies to 3D dynamics.
- Stitching threshold must be within [0,1]. A positive threshold is required for new 2D segmentation in this volumetric application; independent 2D masks are not supported as volume objects.
- The threshold is an inter-slice overlap (IoU) criterion, not a cell probability threshold. Higher values require stronger overlap; lower values allow more permissive linking. Inspect continuity and incorrect joins across Z.
- Cellpose min_size applies per XY slice in stitching mode. Small end sections may be removed. The app's subsequent minimum-volume filtering remains a separate 3D measurement.
- Existing normalization options are passed unchanged. To compare segmentation modes fairly, keep the input and normalization fixed; use norm3D=true in a custom normalization dictionary when you want common stack-wide limits in stitching mode.
- The corrected image selection and MAP2 merge workflow are unchanged.

analysis_config.json now records cellpose.do_3D and cellpose.stitch_threshold. Resume exports preserve these two parameters from the original segmentation. Legacy exports without them are treated as 3D with stitching disabled, consistent with the previous fixed app behavior.

## Validation
Configuration validation, legacy presets, effective Cellpose keyword arguments, 3D-only flow smoothing, and export provenance tested with automated tests. No real GPU segmentation or native macOS UI run was performed in this environment.
