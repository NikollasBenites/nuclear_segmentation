# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
# --------------------------------------------------------
# Select the processing device
# --------------------------------------------------------

if PROCESSING_DEVICE == "cpu":
    DEVICE = torch.device("cpu")
elif torch.cuda.is_available():

    DEVICE = torch.device("cuda")

    print("Acceleration backend: NVIDIA CUDA")
    print("GPU:", torch.cuda.get_device_name(0))


elif (
    hasattr(torch.backends, "mps")
    and torch.backends.mps.is_built()
    and torch.backends.mps.is_available()
):

    DEVICE = torch.device("mps")

    print("Acceleration backend: Apple MPS")
    print("GPU: Apple Silicon integrated GPU")


else:

    DEVICE = torch.device("cpu")

    print("Acceleration backend: CPU")
    print("No compatible GPU acceleration was detected.")


USING_ACCELERATOR = DEVICE.type in {
    "cuda",
    "mps",
}

print("Selected PyTorch device:", DEVICE)


def synchronize_selected_device():

    if DEVICE.type == "cuda":

        torch.cuda.synchronize()

    elif (
        DEVICE.type == "mps"
        and hasattr(torch, "mps")
        and hasattr(torch.mps, "synchronize")
    ):

        torch.mps.synchronize()


raw_3d_probs = None
CELLPOSE_MODEL_LOAD_SECONDS = None
CELLPOSE_EVAL_SECONDS = None


# --------------------------------------------------------
# Resume previous segmentation
# --------------------------------------------------------

if RUN_MODE in {"resume", "density_only"}:

    resume_start = perf_counter()

    masks = tifffile.imread(
        previous_result_folder
        / "cellpose_raw_masks.tif"
    )

    if masks.ndim != 3:

        raise ValueError(
            f"Expected a ZYX raw mask; "
            f"received {masks.shape}."
        )

    if masks.shape != segmentation_volume.shape:

        raise ValueError(
            f"Raw mask shape {masks.shape} "
            f"does not match image shape "
            f"{segmentation_volume.shape}."
        )

    print(
        "Loaded previous raw Cellpose masks "
        "without rerunning the model in "
        f"{perf_counter() - resume_start:.2f} s."
    )


# --------------------------------------------------------
# Run a new segmentation
# --------------------------------------------------------

else:

    # Cellpose file logging is optional.
    if not globals().get(
        "_CELLPOSE_LOGGER_INITIALIZED",
        False,
    ):

        try:

            io.logger_setup()

            print(
                "Cellpose logging initialized.",
                flush=True,
            )

        except PermissionError:

            print(
                "Cellpose run.log is currently in use. "
                "Continuing without resetting it.",
                flush=True,
            )

        _CELLPOSE_LOGGER_INITIALIZED = True


    # Clear unused Apple GPU memory before loading the model.
    if DEVICE.type == "mps":

        torch.mps.empty_cache()


    # ----------------------------------------------------
    # Load the model
    # ----------------------------------------------------

    print(
        f"Loading Cellpose model: "
        f"{MODEL_NAME}...",
        flush=True,
    )

    model_start = perf_counter()

    model = models.CellposeModel(
        device=DEVICE,
        pretrained_model=MODEL_NAME,
    )

    synchronize_selected_device()

    CELLPOSE_MODEL_LOAD_SECONDS = float(
        perf_counter() - model_start
    )

    print(
        "Cellpose model loaded successfully "
        f"in {CELLPOSE_MODEL_LOAD_SECONDS:.2f} s.",
        flush=True,
    )


    # ----------------------------------------------------
    # Cellpose parameters
    # ----------------------------------------------------

    eval_parameters = {
        "z_axis": 0,
        "channel_axis": None,
        "do_3D": True,
        "anisotropy": ANISOTROPY,
        "batch_size": BATCH_SIZE,
        "cellprob_threshold": CELLPROB_THRESHOLD,
        "min_size": CELLPOSE_MIN_SIZE_VOXELS,
        "progress": True,
    }


    if FLOW3D_SMOOTH is not None:

        eval_parameters[
            "flow3D_smooth"
        ] = FLOW3D_SMOOTH


    if DIAMETER_PIXELS is not None:

        eval_parameters[
            "diameter"
        ] = DIAMETER_PIXELS


    # ----------------------------------------------------
    # Run 3D segmentation
    # ----------------------------------------------------

    print(
        "Starting full-resolution 3D "
        f"segmentation of "
        f"{segmentation_volume.shape}...",
        flush=True,
    )

    print(
        f"Device: {DEVICE}",
        flush=True,
    )

    print(
        f"Batch size: {BATCH_SIZE}",
        flush=True,
    )

    print(
        f"Anisotropy: {ANISOTROPY:.4f}",
        flush=True,
    )

    synchronize_selected_device()

    evaluation_start = perf_counter()

    masks, flows, styles = model.eval(
        segmentation_volume,
        **eval_parameters,
    )

    synchronize_selected_device()

    CELLPOSE_EVAL_SECONDS = float(
        perf_counter() - evaluation_start
    )

    print(
        "Cellpose evaluation completed in "
        f"{CELLPOSE_EVAL_SECONDS / 60:.2f} min.",
        flush=True,
    )


    # ----------------------------------------------------
    # Recover the probability volume when available
    # ----------------------------------------------------

    try:

        candidate_probs = np.asarray(
            flows[2]
        )

        candidate_probs = np.squeeze(
            candidate_probs
        )

        if candidate_probs.shape == masks.shape:

            raw_3d_probs = candidate_probs

        else:

            print(
                "The Cellpose probability output "
                f"has shape {candidate_probs.shape}, "
                f"but the masks have shape "
                f"{masks.shape}. The probability "
                "layer will not be added."
            )

    except (
        IndexError,
        TypeError,
        ValueError,
    ):

        raw_3d_probs = None

        print(
            "The Cellpose probability volume "
            "could not be recovered. The masks "
            "remain available."
        )


# --------------------------------------------------------
# Validate the segmentation
# --------------------------------------------------------

masks = np.asarray(
    masks
)


if masks.shape != segmentation_volume.shape:

    raise ValueError(
        f"Mask shape {masks.shape} "
        f"does not match segmentation volume "
        f"{segmentation_volume.shape}."
    )


if int(masks.max()) == 0:

    raise RuntimeError(
        "Cellpose produced zero masks."
    )


detected_labels = np.unique(
    masks
)

detected_labels = detected_labels[
    detected_labels != 0
]


print(
    "Mask shape:",
    masks.shape,
)

print(
    "Maximum label ID:",
    int(masks.max()),
)

print(
    "Detected labels:",
    len(detected_labels),
)