# NuCytoSeg Phase 3A

## Entry regression gate

The development branch resolved to `0a5aa3d595fb573f9950bebcf6e80541c847676c` before implementation. The remote main reference was `2fed532706aa56122fe7091586722d643fc189b7`. The Phase 2 completion handoff records coverage of 450 collected tests and 13 scientific subtests at entry. It records one intermittent preprocessing worker timeout.

The local comparison uses Python 3.12.14, PyQt6 6.11.0 (Qt 6.11.2), qtpy 2.4.3, napari 0.10.0 and pytest 9.2.0 with Xorg, xcb and Mesa software OpenGL. The broad invocation is `python -m pytest -q --ignore=tests/test_live_plots.py --deselect=tests/test_workflow.py::test_launcher_preview_worker_does_not_run_cellpose`. Those five cases run in separate processes, following the Phase 2 handoff. The suite includes scientific unit tests, Qt tests using a QWidget canvas stand-in and explicit embedded QtViewer/VisPy tests.

## Desktop technology decision

| Option | Decision |
| --- | --- |
| Native Qt widgets and scoped Qt Style Sheets | Keep the Qt event loop, accessibility names, focus order, keyboard shortcuts and OS font selection. Use device-independent dimensions and native vector painting for scaling. Palette roles cover controls, tables, popups and disabled text. |
| PyQt6 and qtpy | Keep the existing binding, abstraction and distribution terms. PyQt6 is GPL or commercially licensed. Packaging on macOS and Windows still requires the matching Qt platform and OpenGL plugins. |
| Embedded napari 0.10 | Keep the existing ViewerModel, QtViewer and scientific layer owners. Application appearance belongs to window chrome. Classification colors, calibrated coordinates and label rendering belong to the scientific controllers. Napari uses BSD-3-Clause. |
| QtAwesome | Existing QIconEngine artwork covers the required actions. Adding an MIT-licensed wrapper and bundled icon fonts would add font deployment and glyph mapping work. |
| superqt | Existing transitive dependency of napari (BSD-3-Clause). Native controls already cover the interaction requirements. No new direct dependency. |
| Native docks or third-party docking | Nested QSplitters support the approved fixed layout and the existing QSettings snapshots. Free docking would add drop targets and another persistence format. External docking libraries would also add bindings, licensing review and platform packaging work. |

Checkpoint readers, scientific defaults, geometry transaction owners and export algorithms remain the Phase 2 implementations.

### Observed entry results

The broad run passed 445 tests and 13 subtests. The isolated launcher worker passed. Three live-plot cases passed on the first invocation. The project Z-profile test saw two of three planes at its fixed 600 ms sample, then passed in a fresh process. All 450 distinct cases passed across the runs. The actual embedded canvas also returned a 760 × 756 screenshot at a 1364 × 900 window size. Baseline native checkpoints and the six files from the exact-ID export fixture were retained for cross-revision comparison.

## 3.0 · Design system

The desktop window applies named semantic colors, native point-sized typography, section headers, control states and keyboard focus through `design_system.py`. The appearance preference lives in QSettings. Switching applies a palette and stylesheet in place. The design-system gate covers both themes, unchanged rendered pixels, camera and parameters, desktop controls at both supported sizes and the Phase 2D.8 classification cases.

## 3.1 · Scientific workspace

`desktop_window.py` owns a horizontal panel splitter inside a vertical results splitter. `workflow_navigation.py` mounts the stage bar above them and the selected subpages in the parameter panel. The active session selector calls the existing guarded session-switch handler. `workflow_progress.py` reads the active session's validity flags and the selected pair's current result. Visiting a page cannot complete a scientific stage.

`results_workspace.py` hosts the original sorted association tables, candidate views and their selection models. Measurements use a read-only Qt adapter over the published DataFrame and its object index. Sampling reads the quantification controller's formatted values, history uses the geometry history model and logs share the controller's text documents. QC reparents the same plot widget when detached. Panel collapse hides the live widgets. QSettings snapshots add optional results visibility, height and tab fields, with defaults for Phase 2 layouts. Native scientific checkpoints require no layout migration.

The approved placement changes required updating desktop layout assertions for the horizontal bar, sidebar ownership, QC docking and visible candidates tab. Scientific assertions for classification, calibrated highlights, camera, sorting and transaction history remain in the regression gate.

The 3.0 gate passed 45 tests. At 3.1, 83 of 84 passed in the workspace/matching/checkpoint group on the first run. The remaining case attempted to scroll the now-hidden Candidates tab. Opening that tab before scrolling restored its intended contract, and all five matching navigation cases passed together. The three result-workspace tests plus import and segmentation workers passed together (24 tests). The protected brush canvas and toolbar cases passed together (2 tests). The appearance/canvas comparison also passed under QT_SCALE_FACTOR=1.5.

## 3.2 · Review & Proofreading

One stage selects Associations, Geometry Editing or Manual Association Review. The two projections of Association Explorer share the pair-owned result, display-only minimum intersection fraction, renderer, explicit picking target and wide results area. The cytoplasmic projection filters accepted counts. The nuclear projection filters candidate multiplicity, class, accepted count and manual status. The optional display threshold uses each intersection divided by the complete nuclear voxel count. The matching controller still receives only its three scientific thresholds. All sparse intersections remain available to scientific export and guided correction.

Matching navigation retains display filters with each ordered pair's inspection location. Direct picking can reveal an object excluded by a row filter. Changing filters clears an unavailable prior selection. Automatic class/reason and the manual/final outcome use separate readouts. Role-aware handoffs reuse the existing guided watershed, merge and ordinary geometry transactions, including their validation, protected brushes, saved Undo and read-only references. The transaction lock covers stage and session controls while the layer tree remains usable.

The focused runs exposed an outdated shutdown signal connection, placement-dependent transaction lock, an assertion tied to the previous accepted-count checkbox and a combined label that obscured the previously separate automatic readout. These were corrected before the next phase.
