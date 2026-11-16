"""Pure non-destructive XY projection and rectangular CZYX cropping helpers."""
from __future__ import annotations

import numpy as np
from skimage.draw import polygon2mask


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


def _segments_intersect(a, b, c, d) -> bool:
    def orientation(p, q, r):
        qp = q - p
        rp = r - p
        return qp[0] * rp[1] - qp[1] * rp[0]

    values = np.asarray(
        (
            orientation(a, b, c),
            orientation(a, b, d),
            orientation(c, d, a),
            orientation(c, d, b),
        ),
        dtype=float,
    )
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True

    def on_segment(p, q, r):
        return (
            min(p[0], r[0]) <= q[0] <= max(p[0], r[0])
            and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])
        )

    return bool(
        (np.isclose(values[0], 0.0) and on_segment(a, c, b))
        or (np.isclose(values[1], 0.0) and on_segment(a, d, b))
        or (np.isclose(values[2], 0.0) and on_segment(c, a, d))
        or (np.isclose(values[3], 0.0) and on_segment(c, b, d))
    )


def polygon_roi_mask(vertices_yx, image_shape_yx):
    vertices = np.asarray(vertices_yx, dtype=float)
    if vertices.ndim != 2 or vertices.shape[0] < 3 or vertices.shape[1] != 2:
        raise ValueError("A polygon crop requires at least three Y/X vertices.")
    if not np.isfinite(vertices).all():
        raise ValueError("Polygon vertices must contain finite Y/X coordinates.")
    unique = np.unique(vertices, axis=0)
    if len(unique) < 3:
        raise ValueError("The polygon is degenerate and has fewer than three points.")
    if np.any(np.all(np.isclose(vertices, np.roll(vertices, 1, axis=0)), axis=1)):
        raise ValueError("The polygon contains a zero-length edge.")
    count = len(vertices)
    for first in range(count):
        a, b = vertices[first], vertices[(first + 1) % count]
        for second in range(first + 1, count):
            if second in {first, (first + 1) % count}:
                continue
            if (second + 1) % count in {first, (first + 1) % count}:
                continue
            c, d = vertices[second], vertices[(second + 1) % count]
            if _segments_intersect(a, b, c, d):
                raise ValueError("Self-intersecting polygons are not supported.")
    area = 0.5 * abs(
        np.dot(vertices[:, 1], np.roll(vertices[:, 0], 1))
        - np.dot(vertices[:, 0], np.roll(vertices[:, 1], 1))
    )
    if not np.isfinite(area) or area < 0.5:
        raise ValueError("The polygon is too small or degenerate.")
    bounding_box = selection_bounding_box(vertices, image_shape_yx)
    y0, y1, x0, x1 = bounding_box
    local_vertices = vertices - np.asarray((y0, x0), dtype=float)
    mask = polygon2mask((y1 - y0, x1 - x0), local_vertices).astype(bool, copy=False)
    if not mask.any():
        raise ValueError("The polygon contains no image pixel centers.")
    return bounding_box, mask


def masked_crop_czyx(data_czyx, bounding_box, roi_mask_yx):
    cropped = crop_czyx(data_czyx, bounding_box)
    mask = np.asarray(roi_mask_yx, dtype=bool)
    if mask.shape != cropped.shape[-2:]:
        raise ValueError(
            f"ROI mask shape {mask.shape} does not match crop {cropped.shape[-2:]}."
        )
    result = np.array(cropped, copy=True)
    result *= mask[np.newaxis, np.newaxis, :, :]
    return result
