# ROI smoothing menu

Open **Nuclear Segmentation → ROI → Smooth ROI…** after loading the tissue ROI.
The dialog previews Gaussian smoothing on a temporary layer. Change XY sigma
(default 2 pixels) and Z sigma (default 0 planes). Z=0 smooths each XY plane
independently. Each preview starts from the original ROI, not the previous preview.
The displayed percentage reports the change in ROI voxel count (and volume).

Apply updates the original ROI; Cancel discards the preview. Use
**Nuclear Segmentation → ROI → Undo last ROI smoothing** to restore the ROI
before the latest application. Undo refuses to overwrite subsequent manual edits.
After applying or undoing, accept the reviewed ROI again to recalculate density.
Save the ROI / export results through the existing app commands.

Only the app's tissue ROI layers are targeted, never individual cell masks.
One nonzero label is required and its value is preserved. Default smoothing
operates on the full stack in XY without mixing Z planes. Positive Z sigma
also smooths between planes; sigma values are voxel units, not micrometers.
Smoothing can alter ROI volume and remove small features.

## Installation

Close the app. Extract the updated ZIP. From the extracted app directory, in
your existing nuclearapp environment, run:

```bash
conda activate nuclearapp
python -m pip install --no-deps -e .
nuclear-segmentation
```

No new runtime dependency is introduced. Existing dependencies and app version
are unchanged. Modified: src/nuclear_segmentation/ui_layout.py.
Added: roi_smoothing.py, roi_smoothing_ui.py, tests/test_roi_smoothing.py,
and this guide. Existing credits and other application features are preserved.
