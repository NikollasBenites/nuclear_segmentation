Additional revision dated October 19, 2026: layout fixes and an automatically
updated Graphs tab. The version remains 1.0.1. See GUI_AND_PLOTS_1_0_1.md for details.

# Update 1.0.1 — Z selection and GUI

## Selection rules

- `first`: excludes masks touching or crossing the first boundary or its guard.
  The first appearance (`min_z`) must occur at or before the last plane,
  inclusive. A mask may extend beyond that last plane; its Z centroid does not
  cause an additional exclusion.
- `last`: mirrors this rule using `max_z`; the first boundary is the inclusion face.
- `both`: preserves exclusion by contact with either face (whole-object selection).
- `none`: preserves centroid-based selection within the interval.
- A zero guard still excludes contact with the active face. Z indices are zero-based.
- In `first`/`last`, ROI membership is evaluated at the centroid's XY coordinates
  in the appearance plane. This is not an XY stereological counting frame or
  an optical fractionator.
- Morphology/intensity filters and manual decisions remain active. A manual
  Keep decision does not override spatial exclusions.
- Volume remains the sum of ROI voxels in the effective planes. The nominal
  thickness correction still uses the `first..last` thickness before guards.
  This does not automatically estimate the total neuron count of the entire MNTB.

## GUI and compatibility

Core code, stages, defaults and saved matching files are explicitly read as UTF-8.
This prevents UTF-8 bytes from being interpreted as Windows-1252, which corrupted
µ, ³ and arrows and could also affect unit recognition. Changing fonts or removing
symbols is not necessary.

Dropdowns use a scrollable Qt view with up to 16 visible rows. Invalid Z limits
clear the filtered masks and counters instead of displaying a stale selection.
Correcting the limits restores eligible masks. The guard can now span the full
stack extent, without the artificial half-thickness limit in single-face modes.

## Updating

Close the app and back up your current folder. Extract this ZIP into a separate
folder. Copy the files listed in CHANGED_FILES.txt to the corresponding paths
in your current project. Do not replace the entire folder in Finder: preserve
your `.git` folder. Legacy files ending in ` 2.py` are preserved but are not executed.

In a terminal, activate the app's environment, navigate to the folder containing
`pyproject.toml`, and run:

```bash
python -m pip install -e . --no-deps
nuclear-segmentation
```

`--no-deps` preserves the versions installed in your working environment.
This command is not intended to set up a new environment without dependencies.

Use **Resume analysis / adjust filters** to recalculate previous results from
raw masks using the corrected rule, without rerunning Cellpose. New exports
record `z_counting_rule=directional_entry_v1` and schema version 8.
**Density only** preserves the original criterion for legacy exports; it cannot
recover masks previously excluded. New exports retain the directional rule
when reopened in Density only.

See VALIDATION_1_0_1.md for the actual scope of testing.
