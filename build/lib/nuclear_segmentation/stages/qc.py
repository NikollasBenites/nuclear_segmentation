# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
#------ DAPI INTENSITY THROUGH Z

dapi_p50_by_z = np.percentile(
    segmentation_volume,
    50,
    axis=(1, 2),
)

dapi_p99_by_z = np.percentile(
    segmentation_volume,
    99,
    axis=(1, 2),
)

plt.figure(figsize=(8, 4))
plt.plot(dapi_p50_by_z, label="DAPI median")
plt.plot(dapi_p99_by_z, label="DAPI 99th percentile")
plt.xlabel("Z plane")
plt.ylabel("Raw intensity")
plt.title("DAPI intensity through the Z-stack")
plt.legend()
plt.grid(alpha=0.2)
plt.show(block=False)
figure, axes = plt.subplots(1, 3, figsize=(17, 4.5))

axes[0].hist(
    mask_properties["volume_um3"].dropna(),
    bins=40,
    color="steelblue",
    edgecolor="black",
    alpha=0.80,
)
axes[0].set_xlabel("Mask volume (µm³)")
axes[0].set_ylabel("Number of masks")
axes[0].set_title("All raw Cellpose masks")
axes[0].grid(alpha=0.2)

axes[1].scatter(
    mask_properties["volume_um3"],
    mask_properties["sphericity"],
    s=14,
    alpha=0.45,
    color="slateblue",
)
axes[1].set_xlabel("Mask volume (µm³)")
axes[1].set_ylabel("Sphericity")
axes[1].set_title("Volume versus sphericity")
axes[1].set_ylim(0, 1.05)
axes[1].grid(alpha=0.2)

for marker_name, marker_info in PERINUCLEAR_MARKER_RESULTS.items():
    column = marker_info["fraction_column"]
    minimum = marker_info["initial_min_positive_fraction"]
    axes[2].hist(
        mask_properties[column].dropna(),
        bins=np.linspace(0, 1, 41),
        alpha=0.45,
        label=marker_name,
    )
    axes[2].axvline(minimum, linestyle="--", linewidth=1.5)

axes[2].set_xlabel("Marker-positive perinuclear-shell fraction")
axes[2].set_ylabel("Number of nuclei")
axes[2].set_title("Perinuclear marker association")
axes[2].set_xlim(0, 1)
axes[2].legend()
axes[2].grid(alpha=0.2)

figure.tight_layout()
plt.show(block=False)

if current_keep is None:
    raise RuntimeError("Run the dynamic live filtering cell first.")

retained_properties = filter_properties.loc[current_keep].copy()
figure, axes = plt.subplots(1, 2, figsize=(12, 4.5))

common_bins = np.histogram_bin_edges(
    filter_properties["volume_um3"].dropna(),
    bins=40,
)
axes[0].hist(
    filter_properties["volume_um3"].dropna(),
    bins=common_bins,
    color="gray",
    edgecolor="black",
    alpha=0.40,
    label=f"All masks (n={len(filter_properties)})",
)
axes[0].hist(
    retained_properties["volume_um3"].dropna(),
    bins=common_bins,
    color="royalblue",
    edgecolor="navy",
    alpha=0.70,
    label=f"Retained (n={len(retained_properties)})",
)
axes[0].set_xlabel("Mask volume (µm³)")
axes[0].set_ylabel("Number of masks")
axes[0].set_title("Volume before and after filtering")
axes[0].legend()
axes[0].grid(alpha=0.2)

excluded = ~current_keep
axes[1].scatter(
    filter_properties.loc[excluded, "volume_um3"],
    filter_properties.loc[excluded, "sphericity"],
    s=13,
    alpha=0.35,
    color="gray",
    label="Excluded",
)
axes[1].scatter(
    filter_properties.loc[current_keep, "volume_um3"],
    filter_properties.loc[current_keep, "sphericity"],
    s=16,
    alpha=0.60,
    color="royalblue",
    label="Retained",
)
axes[1].set_xlabel("Mask volume (µm³)")
axes[1].set_ylabel("Sphericity")
axes[1].set_ylim(0, 1.05)
axes[1].set_title("Current filter classification")
axes[1].legend()
axes[1].grid(alpha=0.2)

figure.tight_layout()
plt.show(block=False)