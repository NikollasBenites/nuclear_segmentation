"""Present workflow evidence from the active session and ordered matching pair."""

def validity(current, result):
    if current:
        return "Complete"
    return "Outdated" if result is not None else "Pending"


def statuses(window):
    s = window.project_state
    pair = s.matching_pair
    preparation = "Optional"
    if s.crop_provenance or s.z_correction_applied or s.normalization_is_current:
        preparation = "Complete"
    normalization_mode = (s.normalization_configuration or {}).get("mode", "cellpose_default")
    if normalization_mode not in {"cellpose_default", "disabled"} and not s.normalization_is_current:
        preparation = "Outdated"
    values = {
        "project": "Complete" if s.dataset_ready else "Pending",
        "preprocessing": preparation,
        "segmentation": validity(s.segmentation_is_current, s.segmentation_result),
        "mask_review": validity(s.filtering_is_current, s.filtering_result),
        "matching": validity(pair.current_result is not None, pair.executed_settings or pair.historical_result),
        "quantification": validity(s.quantification_is_current and (s.quantification_result or {}).get("density_status") == "valid",
                                   s.quantification_result if not s.quantification_is_current else None),
        "quality_control": "Available" if s.filtering_is_current else "Pending",
        "export": "Available" if s.dataset_ready else "Pending",
    }
    for key, busy in (
        ("project", window._import_thread is not None),
        ("preprocessing", any(c.busy for c in (window.normalization, window.z_correction, window.crop_workspace))),
        ("segmentation", window.segmentation.busy), ("mask_review", window.filtering.busy),
        ("matching", window.matching_display.execution.busy or pair.job is not None),
        ("export", window.image_export.busy),
    ):
        if busy:
            values[key] = "Running"
    return values
