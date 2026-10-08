# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
mask_properties, background_table = calculate_mask_properties(
    masks=masks,
    channel_volumes=channel_volumes,
    channel_configuration=CHANNELS,
    voxel_spacing=VOXEL_SPACING_UM,
    background_percentile=GLOBAL_BACKGROUND_PERCENTILE,
)

if INTENSITY_MODE == "raw":
    intensity_column_prefix = "mean_intensity_"
elif INTENSITY_MODE == "global_background_subtracted":
    intensity_column_prefix = "background_corrected_mean_"
else:
    raise ValueError(
        'INTENSITY_MODE must be "raw" or '
        '"global_background_subtracted".'
    )

print(f"Measured {len(mask_properties)} masks.")
display(mask_properties.describe().T)
display(background_table)