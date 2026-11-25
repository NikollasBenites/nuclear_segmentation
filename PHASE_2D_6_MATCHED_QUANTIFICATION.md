# Phase 2D.6 · Matched nuclear quantification and export

Select the **nuclear session used for Matching**, accept its scientific ROI, and obtain current measurements and nuclear quantification. Run Matching on a compatible measured pair. Quantification displays separate automatic and manual summaries with the eligible nuclear count and the effective sampling volume.

| Metric | Population and calculation |
| --- | --- |
| Eligible nuclei | Exact retained IDs used for the current nuclear density calculation. The matching nuclear population must agree. |
| Automatic matched | Nuclei classified MATCH with an accepted cytoplasmic target. AMBIGUOUS and NO_MATCH are reported separately. |
| Reviewed matched | Nuclei with an explicit, compatible Matched decision. Unmatched, Uncertain, Not Reviewed and Requires Review each have their own count. |
| Matched fraction | Matched nuclei in the indicated analysis divided by all eligible nuclei. An empty denominator yields an undefined fraction. |
| Matched density | Matched nuclei in the indicated analysis divided by the published positive effective nuclear sampling volume in mm³. An empty eligible population yields zero density. |
| Multiple associations | Distinct retained cytoplasmic objects with two or more accepted automatic links, or two or more compatible manual Matched links, counted separately. Each matched nucleus contributes once to its corresponding density. |

The established directional counting, XY ROI membership, counting planes, Z guards and effective-volume calculation supply the sampling population and volume. These descriptive densities use the measured volume before any optional nominal-thickness adjustment. A missing link alone cannot establish absence of biological marker expression.

## Scientific export

Select **Matched quantification** in Scientific Export Options. The existing scientific export worker validates the reviewed ROI and executed filters, takes a snapshot of the current sources and writes to a new results directory. The matched option can be used by itself or with the existing scientific outputs. An unavailable pair, outdated source measurement, unreconciled eligible population or invalid effective volume blocks matched export.

| File | Scientific content |
| --- | --- |
| automatic_associations.csv | One row per retained nucleus with classifier evidence, ranking, volumes, class and accepted target. |
| association_intersections.csv | Every positive nuclear–cytoplasmic intersection in voxels. |
| final_associations.csv | Separate automatic and manual target IDs, review status, multiple flags, final overlap, comment, decision origin and timestamp. Only a current explicit Matched decision supplies a final target. |
| nuclear_association_measurements.csv and cytoplasmic_association_measurements.csv | Current retained measurement tables linked by role, source session and exact scientific ID. |
| matched_quantification.json | Both sets of counts, fractions and densities, acquisition and grid, source sessions and revisions, executed thresholds, filters, ROI signature, effective sampling planes and complete selected-pair manual decision/undo provenance. |

Each exported review event includes its original project history index. An Undo refers to that index even when the project contains other session pairs. Empty CSV tables retain their headers. Nullable targets use empty CSV fields and JSON null. Load scientific IDs as integers or strings when importing into spreadsheets that round large numbers. The snapshot timestamp identifies the scientific state used for the export. Geometry corrections and subsequent decisions require a new export.

## Validation

Automated coverage includes unequal manual and automatic populations, unresolved decisions, multiple links, sparse 64-bit IDs, zero eligible nuclei, invalid sampling volume, the Qt Quantification display, an actual scientific export and stale-export refusal. Native macOS and Windows checks are listed in the Phase 2 completion handoff.

The final Linux gate passed **413 distinct tests and 13 subtests** with PyQt6 6.11.0 and napari 0.10.0 on Xorg. The broad run passed 408 tests and 13 subtests. The four live-plot tests and one launcher worker test passed in separate processes. Early runs caught an export option that exceeded the sidebar width and an incomplete segmentation fixture. Both were corrected. One intermediate run also observed a 96-pixel viewport at the smallest window size. Both supported sizes passed in the targeted rerun and the final broad run.
