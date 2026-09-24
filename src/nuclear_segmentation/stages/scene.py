# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
for item in CHANNELS:
    viewer.add_image(
        globals().get('original_channel_volumes', channel_volumes)[item["name"]],
        name=f"channel {item['index']}: {item['name']}",
        scale=VOXEL_SPACING_UM,
        colormap=item.get("colormap", "gray"),
        visible=(item["name"] == SEGMENTATION_CHANNEL),
    )

if raw_3d_probs is not None:
    viewer.add_image(
        raw_3d_probs,
        name="Cellpose probability",
        scale=VOXEL_SPACING_UM,
        visible=False,
    )

viewer.add_labels(
    masks,
    name="raw Cellpose masks",
    scale=VOXEL_SPACING_UM,
    opacity=0.70,
)
mntb_roi_layer = viewer.add_labels(
    mntb_roi.astype(np.uint8), name="MNTB ROI - review before density",
    scale=VOXEL_SPACING_UM, opacity=0.25, visible=True,
)
