# Validation — 0.5.2

Targeted matching suite: **8 passed**, 17 warnings, 22.76 seconds.

Command:
```sh
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg PYTHONPATH=src python -m pytest -q tests/test_automatic_matching.py tests/test_matching.py
```

The new integration test covers shared MAP2 links, original label IDs, selection from inspection layers, exclusion of unrelated objects, live removal on Unmatched or changed partner, Undo, automatic recalculation, QC group switching, restoration of normal visibility, mutual exclusion with single-object display, saved JSON reopening, and Reset review. Existing matching tests cover classification thresholds, saved provenance, geometry validation, manual preservation, plots and exports.

Wheel built successfully. No native macOS/Windows GUI or real Cellpose inference was tested. The full segmentation suite was not repeated because this change is confined to the matching UI. Earlier validation reports are historical. Exact environment versions are recorded in tested-environment-0.5.2.json. Warnings are Pydantic deprecations and an intentionally invalid TIFF fixture.
