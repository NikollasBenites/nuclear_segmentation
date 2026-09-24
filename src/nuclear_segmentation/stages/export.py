# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
if not roi_reviewed:
    raise RuntimeError("Review the MNTB ROI across Z and click Accept reviewed MNTB ROI before export.")
if current_keep is None or filtered_masks is None:
    raise RuntimeError("Run the live filtering cell before exporting.")

parent_window = getattr(viewer.window, "_qt_window", None)
selected_output_folder = QFileDialog.getExistingDirectory(
    parent_window,
    "Select the parent folder for the generalized segmentation result",
    "",
)
if not selected_output_folder:
    raise RuntimeError("No export folder was selected.")

sample_name = safe_filename(CUSTOM_SAMPLE_NAME or DEFAULT_INPUT.stem)
output_parent = Path(selected_output_folder)
sample_export_directory = output_parent / (f"{sample_name}_nuclei_segmentation_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
sample_export_directory.mkdir(parents=True, exist_ok=True)
if len(str(sample_export_directory)) >= 220:
    raise ValueError(
        "The output path is too long for a safe Windows workflow. "
        "Choose a folder closer to the drive root or shorten CUSTOM_SAMPLE_NAME."
    )

validate_roi_geometry(viewer.layers["MNTB ROI - review before density"], VOXEL_SPACING_UM, masks.shape)
settings = current_filter_settings()
final_keep = apply_filter_settings(settings)
if not final_keep.equals(current_keep):
    raise RuntimeError(
        "The displayed filter state changed. Refresh the panel and rerun export."
    )

kept_labels = filter_properties.index[final_keep].to_numpy()
export_masks = np.where(np.isin(masks, kept_labels), masks, 0)
combined_perinuclear_keep = calculate_perinuclear_filter_keep(settings)
combined_perinuclear_labels = filter_properties.index[
    combined_perinuclear_keep
].to_numpy()
combined_perinuclear_masks = np.where(
    np.isin(masks, combined_perinuclear_labels),
    masks,
    0,
)

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

calibrated_label_tiff(raw_mask_path, masks, VOXEL_SPACING_UM, require_uint16=False)
calibrated_label_tiff(
    combined_marker_mask_path,
    combined_perinuclear_masks,
    VOXEL_SPACING_UM,
    require_uint16=True,
)
calibrated_label_tiff(
    filtered_mask_path,
    export_masks,
    VOXEL_SPACING_UM,
    require_uint16=True,
)

individual_marker_mask_paths = []
individual_marker_keep = {}
for marker_name, marker_filter in settings["perinuclear_filters"].items():
    marker_keep = (
        filter_properties[marker_filter["column"]].fillna(0.0)
        >= marker_filter["minimum_positive_fraction"]
    )
    individual_marker_keep[marker_name] = marker_keep
    marker_labels = filter_properties.index[marker_keep].to_numpy()
    marker_masks = np.where(np.isin(masks, marker_labels), masks, 0)
    marker_path = sample_export_directory / (
        f"{safe_filename(marker_name)}_associated_dapi_nuclei.tif"
    )
    calibrated_label_tiff(
        marker_path,
        marker_masks,
        VOXEL_SPACING_UM,
        require_uint16=True,
    )
    individual_marker_mask_paths.append(marker_path)

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

configuration = {
    "schema_version": 7,
    "manual_review": manual_review.to_record(),
    "nominal_section_thickness_um": float(NOMINAL_SECTION_THICKNESS_UM),
    "mntb_roi": {"source": roi_source, "mask_file": "mntb_roi.tif", "reviewed": True},
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
        "cellprob_threshold": CELLPROB_THRESHOLD,
        "minimum_size_voxels": CELLPOSE_MIN_SIZE_VOXELS,
        "batch_size": BATCH_SIZE,
        "flow3d_smooth": FLOW3D_SMOOTH,
        "diameter_pixels": DIAMETER_PIXELS,
        "resample": CELLPOSE_RESAMPLE,
        "rescale": CELLPOSE_RESCALE,
        "normalize": CELLPOSE_NORMALIZE,
        "roi_normalization": CELLPOSE_ROI_NORMALIZATION,
        "used_gpu": (previous_config.get("cellpose", {}).get("used_gpu") if RUN_MODE == "resume" else bool(DEVICE.type != "cpu")),
        "device": (previous_config.get("cellpose", {}).get("device", "not recorded") if RUN_MODE == "resume" else str(DEVICE)),
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
config_json_path.write_text(json.dumps(configuration, indent=2), encoding="utf-8")

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
density_summary = calculate_density_summary(
    roi_plane_counts, settings, VOXEL_SPACING_UM, int(final_keep.sum()), roi_reviewed)
summary.update(density_summary)
summary["roi_source"] = roi_source
pd.DataFrame([summary]).to_csv(summary_csv_path, index=False)
roi_export_path = sample_export_directory / "mntb_roi.tif"
calibrated_label_tiff(roi_export_path, mntb_roi.astype(np.uint8), VOXEL_SPACING_UM)
density_csv_path = sample_export_directory / "mntb_density_summary.csv"
pd.DataFrame([{**density_summary, "roi_source": roi_source}]).to_csv(density_csv_path, index=False)
_, effective_planes = density_z_planes(settings, masks.shape[0], z_spacing_um)
roi_planes_path = sample_export_directory / "mntb_roi_by_z.csv"
pd.DataFrame({"z_index": np.arange(masks.shape[0]), "roi_voxels": roi_plane_counts,
              "area_um2": roi_plane_counts * VOXEL_SPACING_UM[1] * VOXEL_SPACING_UM[2],
              "included_in_density": effective_planes}).to_csv(roi_planes_path, index=False)

summary_lines = [
    "GENERAL CELLPPOSE-SAM 3D DAPI NUCLEI SEGMENTATION",
    "",
    f"Input: {DEFAULT_INPUT}",
    f"Segmentation channel: {SEGMENTATION_CHANNEL}",
    f"Perinuclear combination: {settings['perinuclear_combination']}",
    f"Voxel spacing ZYX: {VOXEL_SPACING_UM[0]:.4f}, {VOXEL_SPACING_UM[1]:.4f}, {VOXEL_SPACING_UM[2]:.4f} µm",
    "",
    "PERINUCLEAR MARKERS",
]
for marker_record in perinuclear_records:
    summary_lines.append(
        f"{marker_record['name']}: shell {marker_record['ring_inner_um']:.2f}–"
        f"{marker_record['ring_outer_um']:.2f} µm; pixel threshold "
        f"{marker_record['resolved_pixel_threshold']:.3f}; minimum fraction "
        f"{marker_record['minimum_positive_fraction']:.3f}"
    )
summary_lines.extend(
    [
        "",
        "CURRENT FILTERS",
        f"Minimum volume: {settings['minimum_volume_um3']:.2f} µm³",
        f"Minimum sphericity: {settings['minimum_sphericity']:.3f}",
        f"Tissue Z range: {settings['first_tissue_z']} to {settings['last_tissue_z']}",
        f"Z guard: {settings['z_guard_mode']} at {settings['z_guard_um']:.2f} µm",
        f"Nuclear intensity mode: {INTENSITY_MODE}",
        "",
        "RESULTS",
        f"Total Cellpose masks: {len(mask_results)}",
        f"Combined marker-associated nuclei: {int(combined_perinuclear_keep.sum())}",
        f"Retained masks: {len(retained_results)}",
        f"Excluded masks: {len(excluded_results)}",
        f"Excluded by Z guard: {len(guard_results)}",
    ]
)
summary_txt_path.write_text("\n".join(summary_lines), encoding="utf-8")

all_volumes = mask_results["volume_um3"].dropna()
retained_volumes = retained_results["volume_um3"].dropna()
volume_bins = np.histogram_bin_edges(all_volumes, bins=40)
volume_figure, volume_axis = plt.subplots(figsize=(9, 5.5))
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
volume_figure.savefig(volume_figure_path, dpi=300, bbox_inches="tight")

scatter_figure, scatter_axis = plt.subplots(figsize=(9, 5.5))
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
scatter_figure.savefig(scatter_figure_path, dpi=300, bbox_inches="tight")

marker_figure, marker_axis = plt.subplots(figsize=(9, 5.5))
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
marker_figure.savefig(marker_figure_path, dpi=300, bbox_inches="tight")

reason_figure, reason_axis = plt.subplots(figsize=(9, 5.5))
labels = list(failure_counts)
counts = [failure_counts[label] for label in labels]
reason_axis.barh(labels, counts, color="slateblue", edgecolor="navy")
reason_axis.invert_yaxis()
reason_axis.set_xlabel("Number of masks")
reason_axis.set_title("Masks failing each exclusion criterion")
reason_axis.grid(axis="x", alpha=0.2)
reason_figure.tight_layout()
reason_figure.savefig(reason_figure_path, dpi=300, bbox_inches="tight")

with PdfPages(report_path) as pdf:
    metadata = pdf.infodict()
    metadata["Title"] = "General Cellpose-SAM DAPI nuclei report"
    metadata["Author"] = PDF_AUTHOR
    metadata["Subject"] = "3D nuclei and configurable perinuclear markers"
    text_figure = plt.figure(figsize=(8.27, 11.69))
    text_axis = text_figure.add_subplot(111)
    text_axis.axis("off")
    wrapped_text = "\n".join(
        textwrap.fill(line, width=95) if len(line) > 95 else line
        for line in summary_lines
    )
    text_axis.text(
        0.03,
        0.98,
        wrapped_text,
        transform=text_axis.transAxes,
        va="top",
        ha="left",
        fontsize=9.5,
        family="monospace",
    )
    pdf.savefig(text_figure, bbox_inches="tight")
    plt.close(text_figure)
    pdf.savefig(volume_figure, bbox_inches="tight")
    pdf.savefig(scatter_figure, bbox_inches="tight")
    pdf.savefig(marker_figure, bbox_inches="tight")
    pdf.savefig(reason_figure, bbox_inches="tight")

plt.close(volume_figure)
plt.close(scatter_figure)
plt.close(marker_figure)
plt.close(reason_figure)

density_txt_path = sample_export_directory / "mntb_density_summary.txt"
density_txt_path.write_text("\n".join(f"{k}: {v}" for k, v in density_summary.items()), encoding="utf-8")
created_files = [
    roi_export_path, density_csv_path, roi_planes_path, density_txt_path,
    raw_mask_path,
    *individual_marker_mask_paths,
    combined_marker_mask_path,
    filtered_mask_path,
    all_masks_csv_path,
    retained_csv_path,
    excluded_csv_path,
    guard_csv_path,
    perinuclear_csv_path,
    background_csv_path,
    filters_csv_path,
    summary_csv_path,
    summary_txt_path,
    config_json_path,
    volume_figure_path,
    scatter_figure_path,
    marker_figure_path,
    reason_figure_path,
    report_path,
]

print(f"Analysis package saved to:\n{sample_export_directory}")
print("\nCreated files:")
for created_file in created_files:
    print(f"  {created_file.name}")

# Explicit per-plane provenance for corrected intensities.
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
