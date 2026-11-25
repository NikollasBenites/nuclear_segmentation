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
