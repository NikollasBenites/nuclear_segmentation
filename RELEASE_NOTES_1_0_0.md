# Nuclear Segmentation 1.0.0

## UI scope

Dedicated native Qt menu-bar menus, compact setup panels, contextual hover help,
standard icons, scrollable tabs, a pinned density/count summary and maximized
startup. DAPI matching has five task tabs with its original callbacks and review
state. The image-processing menu is removed when its preview is closed.

## Preserved behavior

Segmentation, intensity correction, ROI eligibility, density, shells and matching
algorithms are unchanged. The latest separate MAP2 split, intensity-profile and
export-folder patches are included. Export folder names can be reviewed; sample
metadata remain unchanged. Previous results are not modified by installing this
release. No notebooks are bundled.

## Installation

In your working environment, from this folder:

```sh
python -m pip install -e . --no-deps
nuclear-segmentation --version
nuclear-segmentation
```

Expected version: 1.0.0. Editable installation requires keeping this directory.
On macOS the application menus live in the system menu bar, not inside the window.
