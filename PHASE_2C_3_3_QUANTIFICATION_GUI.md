# NuCytoSeg Phase 2C.3.3 — Scientific Quantification GUI

## Scientific integration

The Quantification workflow is a read-only presentation of the current desktop
segmentation, measurement, filtering, and reviewed scientific ROI state. It does
not reload image data, rerun Cellpose, or calculate morphology again.

The integration directly reuses the authoritative legacy functions:

- `apply_filter_settings()` supplies retained IDs, eligibility columns, Z-guard
  exclusions, sampling exclusions, and overlapping failure reasons.
- `density_z_planes()` supplies the exact eligible-plane definition, including
  active-boundary exclusion when the configured guard thickness is zero.
- `calculate_density_summary()` supplies calibrated ROI volumes, descriptive
  densities, and nominal-thickness correction.
- `sampling_membership()` and `calculate_z_guard_properties()` remain
  authoritative upstream through `apply_filter_settings()`.

No density, guard, directional-entry, ROI-membership, or thickness-correction
equation was added or changed.

## Interface

The Quantification context page contains compact collapsible sections for:

- total, retained, and excluded objects;
- reviewed scientific ROI status, calibrated volumes, tissue boundaries,
  physical guards, and eligible planes;
- measured and nominal-thickness-corrected nuclear density;
- morphology, channel-intensity, perinuclear, Z-guard, and ROI/sampling
  exclusions.

Individual exclusion counts intentionally remain overlapping. The interface
does not imply that their sum equals the total excluded count. Density is
described as a retained-nucleus density estimate, not as an unbiased optical
fractionator estimate.

## Validity and lifecycle

Final density is current only when segmentation and filtering measurements are
current, calibration is valid, the scientific ROI geometry matches the current
filtering grid, ROI provenance is available, the ROI is reviewed, and effective
sampling volume is positive. Preliminary counts and volumes may be shown before
ROI acceptance, while density remains unavailable.

Filter changes reuse cached measurement tables. Perinuclear definition changes
mark quantification pending until measurements are recalculated while preserving
a compatible reviewed ROI. Scientific input replacement, segmentation
replacement, incompatible calibration/grid changes, or project closure clear or
invalidate quantification through `ProjectState`.

The stored quantification record contains source/measurement provenance,
executed segmentation configuration, channel, spacing, reviewed ROI digest and
provenance, filter settings, Z configuration, retained IDs, and density status.
It does not duplicate image, mask, or ROI arrays.

## Native macOS verification

1. Open a calibrated TIFF and complete segmentation and measurements.
2. Open Quantification before accepting the scientific ROI; verify counts are
   visible but density is pending review.
3. Review and accept the MNTB ROI; verify calibrated ROI volumes and density.
4. Compare total, retained, and excluded counts with Mask Review.
5. Test `none`, `first`, `last`, and `both` Z-guard modes, including zero guard.
6. Confirm measured and nominal-thickness-corrected densities are separately
   labelled.
7. Adjust a filter threshold and verify live counts/density update without a new
   measurement or Cellpose run.
8. Change a perinuclear measurement definition and verify density becomes
   pending while the compatible reviewed ROI remains preserved.
9. Recalculate measurements and verify current quantification returns.
10. Close the project and verify all quantification values clear.

Automated tests use Qt's offscreen platform. Native font layout and interaction
must be confirmed on macOS.
