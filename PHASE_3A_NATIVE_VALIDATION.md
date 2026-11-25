# Phase 3A native validation

## macOS

Record the tested commit, macOS release, Intel or Apple Silicon hardware, display scale, Python, PyQt6, Qt, napari, graphics renderer and segmentation device. Use a copy of a calibrated multichannel dataset with anisotropic Z, a nonzero crop offset and two measured sessions. Include a nucleus with multiple positive candidates, a cytoplasmic object with several accepted nuclei, a positive overlap below the scientific minimum and an unrelated session. Keep the original Phase 2 project and export for comparison.

- [ ] Launch through the installed `nucytoseg` entry point. Create a project, open an older checkpoint, cancel an open dialog and open a recent project. Check title, session name, role and menu shortcuts.
- [ ] At 1024 × 680 and a larger window size, use the keyboard to reach every stage, review mode, selection target, control and results tab. Check focus indicators, tooltip contrast, accessible names with VoiceOver, menus and scrolling. Repeat on Retina and an external display.
- [ ] Toggle Light and Dark while inspecting a nucleus. Compare channel limits, colormaps, label colors, selection, crop coordinates, zoom, slice, scale bar, pending parameters and active sessions before and after.
- [ ] Resize and hide each peripheral panel independently. Restore a saved custom layout. Try each named workspace, enter Focus Mode from a custom arrangement and leave it. Check the tab, stable IDs, sorting, scroll positions and expanded height after collapse. Move between displays, restart and reset.
- [ ] Try Basic and Advanced before and after loading the project, including during a geometry preview. Change pending settings and objective. Verify stages, prerequisites, optional skips, guidance, recovery paths and unchanged executed settings. Inspect all preset categories and deferred analyses.
- [ ] Run nuclear segmentation, filtering, ROI review, guard selection, counting and density. Change session and confirm independent channel/settings/history. Verify stage status when jobs start, finish, become obsolete or fail.
- [ ] Inspect the same pair from either population. Pick directly in 2D and rotated 3D, sort tables, use Previous/Next, Follow Selection, focus and isolate. Check white/cyan/violet boundaries and all three automatic class colors. Change candidate display filtering and compare complete intersection counts, scientific thresholds and exports.
- [ ] Switch between associations, geometry editing and manual review. Check session, role, exact IDs, read-only reference and focus. Change pair, remove a previous target and return. Verify an unavailable selection stays empty until explicitly replaced.
- [ ] Preview nuclear-guided watershed, manual split and both merge routes. Inspect proposed IDs and supports, cancel, apply, undo, redo, discard and save. Try protected paint/erase and removal with a neighboring object. Check histories, saved Undo and read-only overlays.
- [ ] Confirm, override and undo manual decisions with comments. Distinguish automatic class and reason from manual status and final target. Change thresholds, then retained membership and geometry. Verify withdrawal of stale results, recalculation and export recovery.
- [ ] Compare the bottom measurements, associations, candidates, sampling summary, history, logs and QC graphs with Phase 2 records. Open graphs separately. Check histograms and ECDFs, linked QC, exact IDs, calibrated units and both matched nuclear densities.
- [ ] Save, close and reopen a native project with edits and manual decisions. Compare arrays, ROI, executed settings, retained IDs, decision history, export values, units and provenance with the Phase 2 reference. Check unsaved-change prompts and orderly worker shutdown.

## Windows

Repeat the scientific and interaction checks above on native Windows with a supported Python 3.12/PyQt6/napari environment. Record GPU driver and OpenGL renderer. Verify 100%, 150% and mixed-monitor DPI, window restoration after monitor removal, keyboard focus and Ctrl shortcuts, file dialogs, Unicode and long project paths, checkpoint file locking, graph window ownership and shutdown after a worker completes.

Record observations against the exact commit. Linux Xorg execution exercises the embedded Qt/OpenGL canvas and cannot establish either native platform result.
