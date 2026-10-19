# GUI and plots — October 19, 2026 revision, version 1.0.1

The application version remains 1.0.1. This revision includes the earlier Z/UTF-8
fixes, corrects the layout and adds plots embedded in the analysis panel.

## Usage

Start a new analysis or use Resume. In the **Filters and density** panel:

- The top section displays retained/total masks, measured density and Accept reviewed ROI.
- **Filters** contains the controls, with vertical scrolling.
- **Density** contains guard exclusions, filter status and the complete report,
  including volumes and density corrected to the nominal section thickness.
- **Graphs** offers Volume, Sphericity, Intensity, Perinuclear and Z profile.
  For Intensity/Perinuclear, select the channel/marker in the second dropdown.

You do not need to click Show/QC again to update a plot. Changes to filters,
manual decisions or acceptance of the ROI mark the plot for an update. Rendering
occurs after 300 ms without further changes. Hidden tabs do not redraw; opening
the tab displays the latest state. **Measure > Quality-control plots** now opens
the Graphs tab.

Histograms use the same bins for all masks and retained masks; the sphericity
plot shows retained and excluded objects. Empty selections are supported.
Invalid settings clear the previous plot and display a message. The Matplotlib
toolbar provides zoom, navigation and figure saving. In narrow panels, overflow
actions are available through the toolbar's expansion menu (`>>`).

**Z profile** describes the entire volume used for segmentation: median and P99
per plane, including zeros and ignoring non-finite values. It does not represent
only retained masks. It is calculated once per session, one plane per event-loop
iteration. Filter changes reuse the cached profile and update the first/last
boundary lines. This does not rerun Cellpose or remeasure masks.

The Graphs tab applies to segmentation/Resume analyses. DAPI Matching comparison
plots and preprocessing diagnostics remain available through their existing
actions; they were not converted to automatic updates in this revision.
Density only retains its retrospective workflow, now in a scrollable panel.

## Layout fixes

- Text height adapts to content, font and available width.
- Narrow forms stack labels above fields; wider panels allow columns.
- Long Matching details have a dedicated scrollable area.
- The density report no longer competes with sliders for vertical space.
- **Apply ROI limits** remains visible in ROI normalization; as before, it becomes
  enabled after the limits have been calculated.
- Some long option labels were shortened; tooltips retain their descriptions.
- Measurement methods, Z exclusion rules and density calculations are unchanged
  from the previously delivered version 1.0.1.

## Installation

Close the app. Extract the ZIP into a separate folder and copy the files listed
in CHANGED_FILES.txt to the corresponding paths in your current project.
Do not replace the entire folder in Finder; preserve your `.git` folder.

In the app's existing working environment, from the folder containing `pyproject.toml`:

```bash
python -m pip install -e . --no-deps
nuclear-segmentation
```

This command preserves installed dependencies; it does not set up an empty environment.

## Validation

See VALIDATION_1_0_1.md. Testing used offscreen Qt and visual inspection of panels
with 10/14-point fonts on Linux. This does not constitute native macOS/Windows
testing, real OpenGL testing or Cellpose/GPU inference testing.
