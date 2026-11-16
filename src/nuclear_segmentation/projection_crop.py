"""Pure non-destructive XY projection and rectangular CZYX cropping helpers."""
from __future__ import annotations

import numpy as np


def generate_xy_projection(volume_zyx, method: str):
    data = np.asarray(volume_zyx)
    if data.ndim != 3:
        raise ValueError(f"Projection input must be ZYX; received {data.shape}.")
    if data.shape[0] < 1:
        raise ValueError("Projection input contains no Z planes.")
    method = str(method).lower()
    if method == "maximum":
        return np.max(data, axis=0)
    if method == "mean":
        dtype = np.float64 if np.issubdtype(data.dtype, np.integer) else None
        return np.mean(data, axis=0, dtype=dtype)
    raise ValueError("Projection method must be 'maximum' or 'mean'.")


def selection_bounding_box(vertices_yx, image_shape_yx):
    vertices = np.asarray(vertices_yx, dtype=float)
    if vertices.ndim != 2 or vertices.shape[0] < 2 or vertices.shape[1] != 2:
        raise ValueError("Draw a non-empty rectangle or polygon before previewing.")
    if not np.isfinite(vertices).all():
        raise ValueError("Crop vertices must contain finite Y/X coordinates.")
    height, width = (int(value) for value in image_shape_yx)
    if height < 1 or width < 1:
        raise ValueError("The source image has invalid Y/X dimensions.")
    y0 = max(0, int(np.floor(vertices[:, 0].min())))
    y1 = min(height, int(np.ceil(vertices[:, 0].max())))
    x0 = max(0, int(np.floor(vertices[:, 1].min())))
    x1 = min(width, int(np.ceil(vertices[:, 1].max())))
    if y1 <= y0 or x1 <= x0:
        raise ValueError("The crop selection is empty or outside the source image.")
    return y0, y1, x0, x1


def crop_czyx(data_czyx, bounding_box):
    data = np.asarray(data_czyx)
    if data.ndim != 4:
        raise ValueError(f"Crop input must be CZYX; received {data.shape}.")
    y0, y1, x0, x1 = (int(value) for value in bounding_box)
    if not (0 <= y0 < y1 <= data.shape[2] and 0 <= x0 < x1 <= data.shape[3]):
        raise ValueError(
            f"Crop bounds {(y0, y1, x0, x1)} are invalid for {data.shape}."
        )
    return data[:, :, y0:y1, x0:x1]
