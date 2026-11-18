"""Shared scientific filtering rules used by legacy and desktop interfaces."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .core import (
    calculate_z_guard_properties,
    density_z_planes,
    roi_centroid_membership,
    sampling_membership,
)


def calculate_perinuclear_filter_keep(properties, settings):
    conditions = [
        properties[item["column"]].fillna(0.0)
        >= item["minimum_positive_fraction"]
        for item in settings["perinuclear_filters"].values()
    ]
    if not conditions:
        return pd.Series(True, index=properties.index)
    table = pd.concat(conditions, axis=1)
    combination = settings["perinuclear_combination"]
    if combination == "all":
        return table.all(axis=1)
    if combination == "any":
        return table.any(axis=1)
    raise ValueError('Perinuclear combination must be "any" or "all".')


def apply_filter_settings(properties, masks, roi, settings, z_spacing_um,
                          manual_review=None):
    """Apply the authoritative legacy automatic and eligibility rules."""
    result = properties.copy()
    z_properties = calculate_z_guard_properties(
        result,
        first_tissue_z=settings["first_tissue_z"],
        last_tissue_z=settings["last_tissue_z"],
        guard_um=settings["z_guard_um"],
        guard_mode=settings["z_guard_mode"],
        z_spacing_um=z_spacing_um,
        max_z_index=int(masks.shape[0] - 1),
    )
    for column in z_properties:
        result[column] = z_properties[column]

    morphology_keep = (
        (result["volume_um3"] >= settings["minimum_volume_um3"])
        & (result["sphericity"] >= settings["minimum_sphericity"])
        & (~result["excluded_by_z_guard"])
    )
    perinuclear_keep = calculate_perinuclear_filter_keep(result, settings)
    intensity_conditions = []
    for name, item in settings["intensity_filters"].items():
        if item["minimum"] > item["maximum"]:
            raise ValueError(f"{name} minimum intensity exceeds its maximum.")
        intensity_conditions.append(
            result[item["column"]].between(
                item["minimum"], item["maximum"], inclusive="both"
            )
        )
    if not intensity_conditions:
        intensity_keep = pd.Series(True, index=result.index)
    elif settings["intensity_combination"] == "all":
        intensity_keep = pd.concat(intensity_conditions, axis=1).all(axis=1)
    elif settings["intensity_combination"] == "any":
        intensity_keep = pd.concat(intensity_conditions, axis=1).any(axis=1)
    else:
        raise ValueError('Intensity combination must be "any" or "all".')

    inside, in_z, counting_z = sampling_membership(
        result, roi, settings, z_spacing_um
    )
    centroid_inside, centroid_z = roi_centroid_membership(result, roi)
    _, sampling_z = density_z_planes(settings, masks.shape[0], z_spacing_um)
    valid = (centroid_z >= 0) & (centroid_z < masks.shape[0])
    centroid_in_z = np.zeros(len(result), dtype=bool)
    centroid_in_z[valid] = sampling_z[centroid_z[valid]]
    result["centroid_in_mntb_roi"] = centroid_inside
    result["centroid_in_sampling_z"] = centroid_in_z
    result["counting_z_px"] = counting_z
    result["counting_point_in_roi"] = inside
    result["counting_point_in_sampling_z"] = in_z
    result["excluded_by_sampling_roi"] = ~(inside & in_z)
    eligible = (~result["excluded_by_z_guard"]) & inside & in_z
    automatic = morphology_keep & perinuclear_keep & intensity_keep & eligible
    result["automatic_keep"] = automatic

    reasons = []
    for label, row in result.iterrows():
        failed = []
        if row["volume_um3"] < settings["minimum_volume_um3"]:
            failed.append("volume")
        if row["sphericity"] < settings["minimum_sphericity"]:
            failed.append("sphericity")
        if not perinuclear_keep.loc[label]:
            failed.append("shell association")
        if not intensity_keep.loc[label]:
            failed.append("intensity")
        if row["excluded_by_z_guard"]:
            failed.append("Z guard")
        if row["excluded_by_sampling_roi"]:
            failed.append("ROI / counting Z")
        reasons.append("; ".join(failed))
    result["automatic_failure_reasons"] = reasons

    if manual_review is None:
        result["manual_decision"] = "automatic"
        result["manual_reason"] = ""
        result["excluded_manually"] = False
        keep = automatic
    else:
        decisions = manual_review.decisions
        result["manual_decision"] = [
            decisions.get(int(label), {}).get("decision", "automatic")
            for label in result.index
        ]
        result["manual_reason"] = [
            decisions.get(int(label), {}).get("reason", "")
            for label in result.index
        ]
        result["excluded_manually"] = result["manual_decision"] == "exclude"
        keep = manual_review.apply(automatic, eligible)
    return result, keep.astype(bool)
