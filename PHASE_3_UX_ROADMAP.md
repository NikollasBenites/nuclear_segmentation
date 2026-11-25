# Phase 3 UX roadmap

These approved requirements describe planned work following the Phase 2 scientific workflows. They are design targets for future releases.

## 3.1 · Workflow progress and navigation

Place a horizontal workflow progress bar above the embedded viewer. Both interface profiles show the same stage status, derived from current scientific validity. Segmentation and review reflect the selected session. Matching reflects the selected nuclear–cytoplasmic pair. Reopening a project, switching sessions or invalidating a result must update the displayed progress from project state.

## 3.2 · Basic and Advanced profiles

Offer Basic and Advanced on the welcome screen and keep a profile selector available throughout the application. Basic guides the user with Previous and Continue controls. Scientific prerequisites unlock stages, and the user can explicitly skip optional steps. Advanced preserves free navigation and the current professional workspace.

Both profiles use the same ProjectState, algorithms and results. Switching profiles preserves work, parameter values and scientific validity. Both provide access to stereology and nuclear density.

## 3.3 · Orthogonal stereology workspace

Add a contextual **Define Counting Volume** mode in ROI & Counting for both profiles. Synchronize XY, XZ and YZ views, with an optional calibrated 3D view. Show the actual effective ROI shape across planes, first and last tissue-plane boundaries, guard zones, physical thickness and the effective sampling range.

Support dragging the plane boundaries and the explicit actions **Set Current Plane as First** and **Set Current Plane as Last**. Derive the visualization and effective sampling information from the existing counting and guard rules. Share rendering resources across views unless performance testing justifies multiple heavyweight viewers.

## 3.4 · Analysis presets

An interface profile controls guidance and navigation. A scientific analysis preset provides an explicit starting configuration for an analysis. Keep those choices independent. Candidate presets include nuclear segmentation, cytoplasmic segmentation, nuclear density and nuclear–cytoplasmic matching.

## Session access

Place an accessible segmentation session selector near the top of the main workspace. Show the current session name and scientific role together. Switching sessions must preserve each session's independent inputs, results, review history and display state.
