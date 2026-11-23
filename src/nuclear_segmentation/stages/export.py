# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
from datetime import datetime
from pathlib import Path
import textwrap

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

SCIENTIFIC_OUTPUTS = {
    "csv": "Scientific CSV tables",
    "tiff": "Segmentation mask TIFFs",
    "png": "Quality Control PNG figures",
    "pdf": "Scientific PDF report",
}

# Export-folder review patch: only the folder name changes; sample metadata is preserved.
def _report_value(value, unit=""):
    return "N/A" if value is None else f"{value:,.6g}{unit}"


def _report_sections(config, summary, density, settings, effective_planes, failures, exclusions):
    def short(value):
        value = str(value)
        return value if len(value) <= 64 else value[:63] + "…"

    cellpose = config["cellpose"]
    markers = config["perinuclear_markers"]
    intensity = settings["intensity_filters"]
    mode = settings["z_guard_mode"]
    counting = density["counting_rule"]
    if config.get("counting_rule") is None and settings.get("z_counting_rule") == "directional_entry_v1":
        counting = {
            "first": "Directional Z entry at minimum object Z with XY centroid tested in the ROI.",
            "last": "Directional Z entry at maximum object Z with XY centroid tested in the ROI.",
        }.get(mode, counting)
    if exclusions is not None:
        exclusions = dict(exclusions)
        if not markers:
            exclusions.pop("Perinuclear association", None)
        if not intensity:
            exclusions.pop("Nuclear intensity", None)
    else:
        exclusions = {key: failures[key] for key in ("physical volume", "sphericity", "physical Z guard")}
    spacing = config["calibration"]["voxel_spacing_zyx_um"]
    thickness = _report_value(density["nominal_section_thickness_um"], " µm")
    source = config.get("nominal_section_thickness_source", "unknown")
    marker_count = f"{summary['combined_marker_associated_nuclei']:,}" if markers else "N/A"
    sections = [
        ("Sample and Image Information", [
            f"Sample: {short(summary['sample_name'])} | Segmentation channel: {short(summary['segmentation_channel'])}",
            f"Source image: {short(Path(summary['input_file']).name)}",
            f"Working ZYX size: {summary['shape_z']} × {summary['shape_y']} × {summary['shape_x']} voxels",
            f"Voxel spacing (Z, Y, X): {', '.join(f'{v:g}' for v in spacing)} µm",
        ]),
        ("Segmentation Parameters", [
            f"Cellpose: {short(cellpose['model_name'])} | Device: {short(cellpose['device'])}",
            f"Mode: {'3D' if cellpose['do_3D'] else '2D with stitching'} | "
            f"Probability threshold: {cellpose['cellprob_threshold']} | Minimum size: {cellpose['minimum_size_voxels']} voxels",
            f"Batch size: {cellpose['batch_size']} | Resample: {cellpose['resample']} | "
            f"Stitch threshold: {cellpose['stitch_threshold']}",
        ]),
        ("Scientific Filtering Configuration", [
            f"Minimum volume: {settings['minimum_volume_um3']:g} µm³ | "
            f"Minimum sphericity: {settings['minimum_sphericity']:g}",
            f"Intensity measurement mode: {config['intensity_measurements']['mode']}",
            f"Intensity filtering: {settings['intensity_combination']} of {len(intensity)} channel(s)"
            if intensity else "Intensity filtering: Not applied",
            f"Perinuclear markers: {len(markers)} configured" if markers else "Perinuclear markers: Not configured",
            f"Perinuclear filtering: {settings['perinuclear_combination']} configured markers"
            if markers else "Perinuclear filtering: Not applied",
        ]),
        ("Scientific ROI and Tissue Thickness", [
            f"ROI: {short(summary['roi_source'])} | Effective volume: {_report_value(density['roi_effective_volume_um3'], ' µm³')}",
            f"Tissue Z (0-based): {settings['first_tissue_z']}–{settings['last_tissue_z']} | "
            f"Z guard: {mode}, {settings['z_guard_um']:g} µm | Eligible planes: {sum(effective_planes)}",
            f"Measured full tissue thickness: {_report_value(density['measured_full_tissue_thickness_um'], ' µm')}",
            f"Nominal section thickness: {thickness} ({source}) | "
            f"Density correction factor: {_report_value(density['thickness_density_correction_factor'])}",
        ]),
        ("Quantification Results", [
            f"Retained nuclei: {summary['retained_masks']:,} | Excluded nuclei: {summary['excluded_masks']:,}",
            f"Combined marker-associated nuclei: {marker_count}",
            f"Measured nuclear density: {_report_value(density['retained_nuclei_per_mm3'], ' nuclei/mm³')}",
            f"Measured nuclear density: {_report_value(density['retained_nuclei_per_100um_cube'], ' nuclei/(100 µm)³')}",
            f"Nominal-thickness-corrected density: "
            f"{_report_value(density['nominal_corrected_nuclei_per_100um_cube'], ' nuclei/(100 µm)³')}",
        ]),
        ("Exclusion Summary", [
            " | ".join(f"{label}: {count:,}" for label, count in exclusions.items()),
            "Exclusion counts can overlap.",
        ]),
        ("Scientific Interpretation", [density["interpretation"], f"Counting rule: {counting}"]),
    ]
    source_image = config["input"]
    if "series_index" in source_image and "time_index" in source_image:
        sections[0][1].append(
            f"Source series: {source_image['series_index']} | Timepoint: {source_image['time_index']} (0-based)"
        )
    details = [f"Source path: {summary['input_file']}", f"Cellpose model: {cellpose['model_name']}"]
    details.extend(f"{name}: [{item['minimum']:g}, {item['maximum']:g}] ({item['column']})"
                   for name, item in intensity.items())
    details.extend(
        f"{marker['name']}: shell {marker['ring_inner_um']:g}–{marker['ring_outer_um']:g} µm, "
        f"resolved threshold {marker['resolved_pixel_threshold']:g}, minimum fraction "
        f"{marker['minimum_positive_fraction']:g}"
        for marker in markers
    )
    details.extend(f"{label}: {count:,}" for label, count in failures.items()
                   if markers or label != "perinuclear marker association")
    return [*sections, ("Source and Filter Details", details)]


def _write_summary_pages(pdf, sections):
    """Lay out wrapped text inside fixed A4 page margins."""
    height = 11.69 * 72

    def new_page(title):
        figure = Figure(figsize=(8.27, 11.69))
        figure.text(.077, 1 - 50 / height, title, fontsize=17, weight="bold", va="top")
        return figure, height - 77

    figure, y = new_page("Scientific Analysis Report")
    for heading, lines in sections:
        wrapped_lines = [
            wrapped for line in lines
            for wrapped in textwrap.wrap(line, width=87, subsequent_indent="  ", break_on_hyphens=False)
        ]
        section_height = 26 + 13 * len(wrapped_lines)
        if y < 75 or y - 49 < section_height <= height - 126:
            pdf.savefig(figure)
            figure, y = new_page("Scientific Analysis Report · continued")
        figure.text(.077, y / height, heading, fontsize=11, weight="bold", va="top")
        y -= 17
        for wrapped in wrapped_lines:
            if y < 49:
                pdf.savefig(figure)
                figure, y = new_page("Scientific Analysis Report · continued")
            figure.text(
                .077, y / height, wrapped, fontsize=9, family="monospace", va="top", parse_math=False,
            )
            y -= 13
        y -= 9
    pdf.savefig(figure)


def _export_folder_error(parent, name):
    """Validate a single portable directory name before any files are written."""
    import re
    if not name or not name.strip():
        return "Enter a folder name."
    if name != name.strip() or name.endswith("."):
        return "Do not start/end with spaces or end with a period."
    if name in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        return "Use a folder name only, without slashes or these characters: < > : \" | ? *"
    base = name.split(".", 1)[0].upper()
    if base in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", base):
        return "This name is reserved on Windows. Choose another name."
    destination = Path(parent) / name
    if len(name.encode("utf-8")) > 180:
        return "The folder name is too long. Please shorten it."
    if len(str(destination)) >= 180:
        return "The full path is too long. Shorten the name or choose a parent folder closer to the drive root."
    if destination.exists() or destination.is_symlink():
        return "This folder already exists. Choose a different name to keep previous results safe."
    return ""


def _choose_export_destination(parent_window, sample, file_dialog=None):
    """Return a reviewed destination, or None on cancellation."""
    from qtpy.QtWidgets import (
        QDialog, QDialogButtonBox, QFileDialog, QLabel, QLineEdit, QVBoxLayout,
    )
    parent = (file_dialog or QFileDialog).getExistingDirectory(
        parent_window, "Select the parent folder for analysis results", "",
    )
    if not parent:
        return None
    dialog = QDialog(parent_window)
    dialog.setWindowTitle("Review export folder name")
    dialog.resize(580, 260)
    layout = QVBoxLayout(dialog)
    info = QLabel("Edit the suggested folder name, then confirm. The sample ID in the results is unchanged.")
    info.setWordWrap(True)
    layout.addWidget(info)
    # A short suggestion; the final text is used exactly as approved.
    suggestion = sample[:48].rstrip(" ._") or "sample"
    field = QLineEdit(suggestion + "_seg_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    layout.addWidget(field)
    path_label = QLabel()
    path_label.setWordWrap(True)
    layout.addWidget(path_label)
    error_label = QLabel()
    error_label.setWordWrap(True)
    layout.addWidget(error_label)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText("Create folder and export")
    layout.addWidget(buttons)

    def refresh(*_):
        path_label.setText("Destination:\n" + str(Path(parent) / field.text()))
        # Plain text prevents names containing markup from changing the display.
        from qtpy.QtCore import Qt
        path_label.setTextFormat(Qt.PlainText)
        error = _export_folder_error(parent, field.text())
        error_label.setText(error)
        buttons.button(QDialogButtonBox.Ok).setEnabled(not error)
        return not error

    def accept():
        if refresh():
            dialog.accept()

    field.textChanged.connect(refresh)
    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    refresh()
    field.selectAll()
    field.setFocus()
    if dialog.exec_() != QDialog.Accepted:
        return None
    return Path(parent) / field.text()


_reviewed_export_destination = None
desktop_export = globals().get("desktop_export")
if desktop_export is not None:
    sample_name = safe_filename(CUSTOM_SAMPLE_NAME or DEFAULT_INPUT.stem)
    _reviewed_export_destination = desktop_export["destination"]
    settings = desktop_export["settings"]
    final_keep = current_keep
    export_masks = filtered_masks
elif "roi_reviewed" in globals():
    if not roi_reviewed:
        raise RuntimeError("Review the MNTB ROI across Z and click Accept reviewed MNTB ROI before export.")
    if current_keep is None or filtered_masks is None:
        raise RuntimeError("Run the live filtering cell before exporting.")
    parent_window = getattr(viewer.window, "_qt_window", None)
    sample_name = safe_filename(CUSTOM_SAMPLE_NAME or DEFAULT_INPUT.stem)
    _reviewed_export_destination = _choose_export_destination(parent_window, sample_name, QFileDialog)
    if _reviewed_export_destination is None:
        print("Export cancelled. No output folder or files were created.")
    else:
        validate_roi_geometry(viewer.layers["MNTB ROI - review before density"], VOXEL_SPACING_UM, masks.shape)
        settings = current_filter_settings()
        final_keep = apply_filter_settings(settings)
        if not final_keep.equals(current_keep):
            raise RuntimeError(
                "The displayed filter state changed. Refresh the panel and rerun export."
            )
        kept_labels = filter_properties.index[final_keep].to_numpy()
        export_masks = np.where(np.isin(masks, kept_labels), masks, 0)

if _reviewed_export_destination is not None:
    sample_export_directory = _reviewed_export_destination
    outputs = desktop_export["outputs"] if desktop_export is not None else SCIENTIFIC_OUTPUTS
    marker_filters = settings["perinuclear_filters"]
    output_parent = sample_export_directory.parent
    selected_output_folder = str(output_parent)
    # Create only after live-state validation; never overwrite an existing folder.
    sample_export_directory.mkdir(parents=False, exist_ok=False)
    export_directory_created = True

    combined_perinuclear_keep = calculate_perinuclear_filter_keep(settings)
    raw_mask_path = sample_export_directory / "cellpose_raw_masks.tif"
    combined_marker_mask_path = (
        sample_export_directory / "perinuclear_marker_associated_dapi_nuclei.tif"
    )
    filtered_mask_path = sample_export_directory / "filtered_masks_syglass.tif"
    all_masks_csv_path = sample_export_directory / "all_mask_properties.csv"
    retained_csv_path = sample_export_directory / "retained_mask_properties.csv"
    excluded_csv_path = sample_export_directory / "excluded_mask_properties.csv"
    guard_csv_path = sample_export_directory / "z_guard_excluded_mask_properties.csv"
    perinuclear_csv_path = sample_export_directory / "perinuclear_marker_metrics.csv"
    background_csv_path = sample_export_directory / "channel_background_estimates.csv"
    filters_csv_path = sample_export_directory / "filter_settings.csv"
    summary_csv_path = sample_export_directory / "analysis_summary.csv"
    summary_txt_path = sample_export_directory / "analysis_summary.txt"
    config_json_path = sample_export_directory / "analysis_config.json"
    volume_figure_path = sample_export_directory / "qc_volume_distribution.png"
    scatter_figure_path = sample_export_directory / "qc_volume_sphericity.png"
    marker_figure_path = sample_export_directory / "qc_perinuclear_markers.png"
    reason_figure_path = sample_export_directory / "qc_exclusion_reasons.png"
    report_path = sample_export_directory / "analysis_report.pdf"

    if "tiff" in outputs:
        calibrated_label_tiff(raw_mask_path, original_cellpose_masks if desktop_export is not None else masks, VOXEL_SPACING_UM)
        calibrated_label_tiff(filtered_mask_path, export_masks, VOXEL_SPACING_UM, require_uint16=True)
        calibrated_label_tiff(
            sample_export_directory / "mntb_roi.tif", mntb_roi.astype(np.uint8), VOXEL_SPACING_UM
        )
        if marker_filters:
            combined_labels = filter_properties.index[combined_perinuclear_keep].to_numpy()
            combined_masks = np.where(np.isin(masks, combined_labels), masks, 0)
            calibrated_label_tiff(combined_marker_mask_path, combined_masks, VOXEL_SPACING_UM, require_uint16=True)

    individual_marker_keep = {}
    for marker_name, marker_filter in settings["perinuclear_filters"].items():
        marker_keep = (
            filter_properties[marker_filter["column"]].fillna(0.0)
            >= marker_filter["minimum_positive_fraction"]
        )
        individual_marker_keep[marker_name] = marker_keep
        if "tiff" in outputs:
            marker_labels = filter_properties.index[marker_keep].to_numpy()
            marker_masks = np.where(np.isin(masks, marker_labels), masks, 0)
            marker_path = sample_export_directory / f"{safe_filename(marker_name)}_associated_dapi_nuclei.tif"
            calibrated_label_tiff(marker_path, marker_masks, VOXEL_SPACING_UM, require_uint16=True)

    mask_results = filter_properties.copy()
    mask_results["retained"] = final_keep.astype(bool)
    mask_results["excluded_by_final_filter"] = (~final_keep).astype(bool)
    mask_results["fails_volume_filter"] = (
        mask_results["volume_um3"] < settings["minimum_volume_um3"]
    )
    mask_results["fails_sphericity_filter"] = (
        mask_results["sphericity"] < settings["minimum_sphericity"]
    ) | mask_results["sphericity"].isna()
    for marker_name, marker_keep in individual_marker_keep.items():
        mask_results[
            f"fails_perinuclear_{safe_key(marker_name).lower()}"
        ] = (~marker_keep).astype(bool)
    mask_results["fails_perinuclear_combination"] = (
        ~combined_perinuclear_keep
    ).astype(bool)

    reason_columns = {
        "excluded_manually": "manual exclusion",
        "excluded_by_sampling_roi": "outside sampling ROI or Z range",
        "fails_volume_filter": "physical volume",
        "fails_sphericity_filter": "sphericity",
        "fails_perinuclear_combination": "perinuclear marker association",
        "excluded_by_z_guard": "physical Z guard",
    }
    for name, intensity_filter in settings["intensity_filters"].items():
        reason_column = f"fails_intensity_filter_{safe_key(name)}"
        mask_results[reason_column] = ~mask_results[
            intensity_filter["column"]
        ].between(
            intensity_filter["minimum"],
            intensity_filter["maximum"],
            inclusive="both",
        )
        reason_columns[reason_column] = f"{name} nuclear intensity"


    def describe_exclusion(row):
        if bool(row["retained"]):
            return "retained"
        reasons = [
            description
            for column, description in reason_columns.items()
            if bool(row[column])
        ]
        return "; ".join(reasons) if reasons else "excluded by combined rule"


    mask_results["exclusion_reasons"] = mask_results.apply(describe_exclusion, axis=1)
    retained_results = mask_results.loc[mask_results["retained"]].copy()
    excluded_results = mask_results.loc[~mask_results["retained"]].copy()
    guard_results = mask_results.loc[mask_results["excluded_by_z_guard"]].copy()

    filter_rows = [
        {
            "criterion": "volume_um3",
            "channel": None,
            "minimum": settings["minimum_volume_um3"],
            "maximum": None,
            "enabled": True,
            "combination": "all",
        },
        {
            "criterion": "sphericity",
            "channel": None,
            "minimum": settings["minimum_sphericity"],
            "maximum": None,
            "enabled": True,
            "combination": "all",
        },
    ]
    for marker_name, marker_filter in settings["perinuclear_filters"].items():
        filter_rows.append(
            {
                "criterion": "perinuclear_positive_fraction",
                "channel": marker_name,
                "minimum": marker_filter["minimum_positive_fraction"],
                "maximum": 1.0,
                "enabled": True,
                "combination": settings["perinuclear_combination"],
            }
        )
    for name, intensity_filter in settings["intensity_filters"].items():
        filter_rows.append(
            {
                "criterion": "nuclear_mean_intensity",
                "channel": name,
                "minimum": intensity_filter["minimum"],
                "maximum": intensity_filter["maximum"],
                "enabled": True,
                "combination": settings["intensity_combination"],
            }
        )
    filter_table = pd.DataFrame(filter_rows)

    channel_records = []
    for item in CHANNELS:
        record = dict(item)
        if desktop_export is not None:
            record["filter_enabled"] = item["name"] in settings["intensity_filters"]
        if item["name"] in settings["intensity_filters"]:
            record["filter_min"] = settings["intensity_filters"][item["name"]][
                "minimum"
            ]
            record["filter_max"] = settings["intensity_filters"][item["name"]][
                "maximum"
            ]
        channel_records.append(record)

    perinuclear_records = []
    for marker_name, marker_info in PERINUCLEAR_MARKER_RESULTS.items():
        marker_filter = settings["perinuclear_filters"][marker_name]
        perinuclear_records.append(
            {
                "name": marker_name,
                "enabled": True,
                "ring_inner_um": marker_info["ring_inner_um"],
                "ring_outer_um": marker_info["ring_outer_um"],
                "configured_pixel_threshold": marker_info[
                    "configured_pixel_threshold"
                ],
                "resolved_pixel_threshold": marker_info[
                    "resolved_pixel_threshold"
                ],
                "threshold_source": marker_info["threshold_source"],
                "fraction_column": marker_info["fraction_column"],
                "minimum_positive_fraction": marker_filter[
                    "minimum_positive_fraction"
                ],
            }
        )

        if desktop_export is not None:
            perinuclear_records[-1]["channel_index"] = marker_info["channel_index"]

    configuration = {
        "schema_version": 8,
        "manual_review": manual_review.to_record(),
        "nominal_section_thickness_um": float(NOMINAL_SECTION_THICKNESS_UM),
        "mntb_roi": {"source": roi_source, "mask_file": "mntb_roi.tif" if "tiff" in outputs else None, "reviewed": True},
        "analysis_date": datetime.now().isoformat(timespec="seconds"),
        "export_corrected_images": EXPORT_CORRECTED_IMAGES,
        "z_corrections": Z_CORRECTIONS,
        "z_correction_source": Z_CORRECTION_SOURCE,
        "z_correction_reports": globals().get('z_correction_reports', {}),
        "object_interpretation": "Validate whether masks represent nuclei or somas; legacy nuclei column names are retained.",
        "input": {
            "path_at_analysis": str(DEFAULT_INPUT),
            "filename": DEFAULT_INPUT.name,
            "sha256": INPUT_SHA256,
            **image_information,
            "storage": OME_ZARR_CACHE_INFO,
        },
        "channels": channel_records,
        "segmentation_channel": SEGMENTATION_CHANNEL,
        "perinuclear_markers": perinuclear_records,
        "perinuclear_combination": settings["perinuclear_combination"],
        "calibration": {
            "source": CALIBRATION_SOURCE,
            "voxel_spacing_zyx_um": list(VOXEL_SPACING_UM),
            "anisotropy_z_over_mean_xy": ANISOTROPY,
        },
        "cellpose": {
            "model_name": MODEL_NAME,
            "do_3D": (previous_config.get("cellpose", {}).get("do_3D", True) if RUN_MODE == "resume" else CELLPOSE_DO_3D),
            "stitch_threshold": (previous_config.get("cellpose", {}).get("stitch_threshold", 0.0) if RUN_MODE == "resume" else (0.0 if CELLPOSE_DO_3D else CELLPOSE_STITCH_THRESHOLD)),
            "cellprob_threshold": CELLPROB_THRESHOLD,
            "minimum_size_voxels": CELLPOSE_MIN_SIZE_VOXELS,
            "batch_size": BATCH_SIZE,
            "flow3d_smooth": FLOW3D_SMOOTH,
            "diameter_pixels": DIAMETER_PIXELS,
            "resample": CELLPOSE_RESAMPLE,
            "rescale": CELLPOSE_RESCALE,
            "normalize": CELLPOSE_NORMALIZE,
            "roi_normalization": CELLPOSE_ROI_NORMALIZATION,
            "used_gpu": (desktop_export["used_gpu"] if desktop_export is not None else
                         previous_config.get("cellpose", {}).get("used_gpu") if RUN_MODE == "resume" else bool(DEVICE.type != "cpu")),
            "device": (desktop_export["device"] if desktop_export is not None else
                       previous_config.get("cellpose", {}).get("device", "not recorded") if RUN_MODE == "resume" else str(DEVICE)),
            "model_load_seconds": CELLPOSE_MODEL_LOAD_SECONDS,
            "evaluation_seconds": CELLPOSE_EVAL_SECONDS,
        },
        "intensity_measurements": {
            "mode": INTENSITY_MODE,
            "global_background_percentile": GLOBAL_BACKGROUND_PERCENTILE,
        },
        "filters": settings,
        "software_versions": software_versions(),
    }
    if desktop_export is not None:
        configuration.update(desktop_export["provenance"])
        configuration["cellpose"]["executed_parameters"] = desktop_export["executed_parameters"]
        configuration["mntb_roi"].update(desktop_export["roi_provenance"])
    elif RUN_MODE == "resume":
        origin = previous_config.get("nominal_section_thickness_source")
        if origin in ("default", "user-defined"):
            configuration["nominal_section_thickness_source"] = origin
    configuration["export_components"] = list(outputs)
    configuration["optional_filter_status"] = {
        "intensity_filtering_applied": bool(settings["intensity_filters"]),
        "perinuclear_markers_configured": bool(perinuclear_records),
        "perinuclear_filtering_applied": bool(marker_filters),
        "combined_marker_associated_nuclei_applicable": bool(marker_filters),
    }
    if not marker_filters:
        configuration["optional_filter_status"]["legacy_combined_count_note"] = (
            "With no active marker filters, combined_marker_associated_nuclei in analysis_summary.csv "
            "retains the legacy count of masks passing the empty filter. Marker association is unavailable."
        )
    config_json_path.write_text(json.dumps(configuration, indent=2), encoding="utf-8")

    if "csv" in outputs:
        mask_results.reset_index().to_csv(all_masks_csv_path, index=False)
        manual_table = filter_properties[['manual_decision', 'manual_reason', 'automatic_keep',
                                          'automatic_failure_reasons', 'excluded_by_z_guard',
                                          'excluded_by_sampling_roi']].copy()
        manual_table['retained'] = final_keep
        manual_table.index.name = 'mask_id'
        manual_table.reset_index().to_csv(sample_export_directory / 'manual_mask_review.csv', index=False)
        retained_results.reset_index().to_csv(retained_csv_path, index=False)
        excluded_results.reset_index().to_csv(excluded_csv_path, index=False)
        guard_results.reset_index().to_csv(guard_csv_path, index=False)
        perinuclear_columns = [column for column in mask_results if "_ring_" in column]
        mask_results[perinuclear_columns].reset_index().to_csv(
            perinuclear_csv_path,
            index=False,
        )
        background_table.to_csv(background_csv_path, index=False)
        filter_table.to_csv(filters_csv_path, index=False)

    failure_counts = {
        description: int(mask_results[column].sum())
        for column, description in reason_columns.items()
    }
    summary = {
        "analysis_date": configuration["analysis_date"],
        "input_file": str(DEFAULT_INPUT),
        "sample_name": sample_name,
        "segmentation_channel": SEGMENTATION_CHANNEL,
        "perinuclear_markers": ";".join(PERINUCLEAR_MARKER_RESULTS),
        "perinuclear_combination": settings["perinuclear_combination"],
        "configured_channels": ";".join(item["name"] for item in CHANNELS),
        "intensity_mode": INTENSITY_MODE,
        "storage_backend": OME_ZARR_CACHE_INFO["storage_backend"],
        "cellpose_model_load_seconds": CELLPOSE_MODEL_LOAD_SECONDS,
        "cellpose_evaluation_seconds": CELLPOSE_EVAL_SECONDS,
        "shape_z": int(masks.shape[0]),
        "shape_y": int(masks.shape[1]),
        "shape_x": int(masks.shape[2]),
        "voxel_spacing_z_um": float(VOXEL_SPACING_UM[0]),
        "voxel_spacing_y_um": float(VOXEL_SPACING_UM[1]),
        "voxel_spacing_x_um": float(VOXEL_SPACING_UM[2]),
        "anisotropy": ANISOTROPY,
        "first_tissue_z": settings["first_tissue_z"],
        "last_tissue_z": settings["last_tissue_z"],
        "z_guard_um": settings["z_guard_um"],
        "z_guard_mode": settings["z_guard_mode"],
        "minimum_volume_um3": settings["minimum_volume_um3"],
        "minimum_sphericity": settings["minimum_sphericity"],
        "total_cellpose_masks": int(len(mask_results)),
        "combined_marker_associated_nuclei": int(combined_perinuclear_keep.sum()),
        "retained_masks": int(len(retained_results)),
        "excluded_masks": int(len(excluded_results)),
        "excluded_by_z_guard": int(len(guard_results)),
    }
    for marker_name, marker_info in PERINUCLEAR_MARKER_RESULTS.items():
        key = safe_key(marker_name).lower()
        summary[f"{key}_resolved_pixel_threshold"] = marker_info[
            "resolved_pixel_threshold"
        ]
        summary[f"{key}_minimum_positive_fraction"] = settings[
            "perinuclear_filters"
        ][marker_name]["minimum_positive_fraction"]
        summary[f"{key}_associated_nuclei"] = int(
            individual_marker_keep[marker_name].sum()
        )
    for description, count in failure_counts.items():
        summary[f"failed_{safe_key(description).lower()}"] = count
    if desktop_export is None:
        density_summary = calculate_density_summary(
            roi_plane_counts, settings, VOXEL_SPACING_UM, int(final_keep.sum()), roi_reviewed)
        _, effective_planes = density_z_planes(settings, masks.shape[0], z_spacing_um)
    else:
        density_summary = desktop_export["density"]
        effective_planes = desktop_export["effective_planes"]
    summary.update(density_summary)
    summary["roi_source"] = roi_source
    if "csv" in outputs:
        pd.DataFrame([summary]).to_csv(summary_csv_path, index=False)
        pd.DataFrame([{**density_summary, "roi_source": roi_source}]).to_csv(
            sample_export_directory / "mntb_density_summary.csv", index=False
        )
        pd.DataFrame({
            "z_index": np.arange(masks.shape[0]), "roi_voxels": roi_plane_counts,
            "area_um2": roi_plane_counts * VOXEL_SPACING_UM[1] * VOXEL_SPACING_UM[2],
            "included_in_density": effective_planes,
        }).to_csv(sample_export_directory / "mntb_roi_by_z.csv", index=False)
        (sample_export_directory / "mntb_density_summary.txt").write_text(
            "\n".join(f"{key}: {value}" for key, value in density_summary.items()), encoding="utf-8"
        )

    if "csv" in outputs or "pdf" in outputs:
        report_sections = _report_sections(
            configuration, summary, density_summary, settings, effective_planes, failure_counts,
            desktop_export["exclusions"] if desktop_export is not None else None,
        )
        if "csv" in outputs:
            summary_txt_path.write_text(
                "\n\n".join("\n".join((heading, *lines)) for heading, lines in report_sections),
                encoding="utf-8",
            )

    if "png" in outputs or "pdf" in outputs:
        figures = []
        all_volumes = mask_results["volume_um3"].dropna()
        retained_volumes = retained_results["volume_um3"].dropna()
        volume_bins = np.histogram_bin_edges(all_volumes, bins=40)
        volume_figure = Figure(figsize=(9, 5.5))
        FigureCanvasAgg(volume_figure)
        volume_axis = volume_figure.subplots()
        volume_axis.hist(
            all_volumes,
            bins=volume_bins,
            color="gray",
            edgecolor="black",
            alpha=0.40,
            label=f"All masks (n={len(all_volumes)})",
        )
        volume_axis.hist(
            retained_volumes,
            bins=volume_bins,
            color="royalblue",
            edgecolor="navy",
            alpha=0.70,
            label=f"Retained (n={len(retained_volumes)})",
        )
        volume_axis.set_xlabel("Mask volume (µm³)")
        volume_axis.set_ylabel("Number of masks")
        volume_axis.set_title("Mask volumes before and after filtering")
        volume_axis.legend()
        volume_axis.grid(alpha=0.2)
        volume_figure.tight_layout()
        figures.append((volume_figure, volume_figure_path))

        scatter_figure = Figure(figsize=(9, 5.5))
        FigureCanvasAgg(scatter_figure)
        scatter_axis = scatter_figure.subplots()
        scatter_axis.scatter(
            excluded_results["volume_um3"],
            excluded_results["sphericity"],
            s=14,
            alpha=0.35,
            color="gray",
            label="Excluded",
        )
        scatter_axis.scatter(
            retained_results["volume_um3"],
            retained_results["sphericity"],
            s=18,
            alpha=0.65,
            color="royalblue",
            label="Retained",
        )
        scatter_axis.set_xlabel("Mask volume (µm³)")
        scatter_axis.set_ylabel("Sphericity")
        scatter_axis.set_ylim(0, 1.05)
        scatter_axis.set_title("Retained and excluded mask morphology")
        scatter_axis.legend()
        scatter_axis.grid(alpha=0.2)
        scatter_figure.tight_layout()
        figures.append((scatter_figure, scatter_figure_path))

        if marker_filters:
            marker_figure = Figure(figsize=(9, 5.5))
            FigureCanvasAgg(marker_figure)
            marker_axis = marker_figure.subplots()
            for marker_name, marker_filter in settings["perinuclear_filters"].items():
                marker_axis.hist(
                    mask_results[marker_filter["column"]].dropna(),
                    bins=np.linspace(0, 1, 41),
                    alpha=0.45,
                    label=marker_name,
                )
                marker_axis.axvline(
                    marker_filter["minimum_positive_fraction"],
                    linestyle="--",
                    linewidth=1.5,
                )
            marker_axis.set_xlabel("Marker-positive perinuclear-shell fraction")
            marker_axis.set_ylabel("Number of DAPI nuclei")
            marker_axis.set_title("Perinuclear marker association")
            marker_axis.set_xlim(0, 1)
            marker_axis.legend()
            marker_axis.grid(alpha=0.2)
            marker_figure.tight_layout()
            figures.append((marker_figure, marker_figure_path))

        reason_figure = Figure(figsize=(9, 5.5))
        FigureCanvasAgg(reason_figure)
        reason_axis = reason_figure.subplots()
        labels = [label for label in failure_counts if marker_filters or label != "perinuclear marker association"]
        counts = [failure_counts[label] for label in labels]
        reason_axis.barh(labels, counts, color="slateblue", edgecolor="navy")
        reason_axis.invert_yaxis()
        reason_axis.set_xlabel("Number of masks")
        reason_axis.set_title("Masks failing each exclusion criterion")
        reason_axis.grid(axis="x", alpha=0.2)
        reason_figure.tight_layout()
        figures.append((reason_figure, reason_figure_path))

        if "png" in outputs:
            for figure, path in figures:
                figure.savefig(path, dpi=300, bbox_inches="tight")
        if "pdf" in outputs:
            with PdfPages(report_path) as pdf:
                metadata = pdf.infodict()
                metadata["Title"] = "NuCytoSeg scientific analysis report"
                metadata["Author"] = PDF_AUTHOR
                metadata["Subject"] = "3D nuclear segmentation, filtering, and quantification"
                _write_summary_pages(pdf, report_sections)
                for figure, _ in figures:
                    pdf.savefig(figure, bbox_inches="tight")
        for figure, _ in figures:
            figure.clear()

    # Explicit per-plane provenance for corrected intensities.
    if "csv" in outputs:
        z_rows = []
        for name, report in globals().get('z_correction_reports', {}).items():
            for z, gain in enumerate(report['gain']):
                z_rows.append(dict(channel=name, z_index=z, gain=gain,
                                   reference_intensity=report['observed_profile'][z],
                                   smoothed_reference=report['smoothed_profile'][z],
                                   measurement_corrected=report['options']['measurement'],
                                   segmentation_corrected=report['options']['segmentation']))
        if z_rows:
            pd.DataFrame(z_rows).to_csv(sample_export_directory / 'z_correction_profiles.csv', index=False)

    if EXPORT_CORRECTED_IMAGES and globals().get('z_corrected_volumes'):
        from nuclear_segmentation.image_export import export_images
        corrected_names = list(z_corrected_volumes)
        corrected_image_path = export_images(
            sample_export_directory / 'corrected_channels.ome.tif',
            [z_corrected_volumes[name] for name in corrected_names],
            [name + ' — Z corrected' for name in corrected_names], VOXEL_SPACING_UM,
            [dict(channel=name, source_path=str(DEFAULT_INPUT), corrected=True,
                  correction=z_correction_reports[name]) for name in corrected_names],
            [DEFAULT_INPUT])
        print('Corrected image data saved:', corrected_image_path)

    print(f"Analysis package saved to:\n{sample_export_directory}")
    print("\nCreated files:")
    created_files = sorted(sample_export_directory.iterdir())
    for created_file in created_files:
        print(f"  {created_file.name}")
