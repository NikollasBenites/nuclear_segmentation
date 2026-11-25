"""Presentation-only analysis routes. Scientific parameters belong to the project."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Objective:
    stages: tuple[str, ...]
    description: str


START = ("project", "preprocessing", "segmentation", "mask_review")
FINISH = ("quality_control", "export")
OBJECTIVES = {
    "Nuclear Segmentation": Objective(START + ("matching",) + FINISH,
        "Select a nuclear session in Project to segment, measure and review nuclei."),
    "Cytoplasmic Segmentation": Objective(START + ("matching",) + FINISH,
        "Select a cytoplasmic session in Project to segment, measure and review cytoplasmic objects."),
    "Density & Quantification": Objective(START + ("quantification",) + FINISH,
        "Review the nuclear counting ROI, tissue planes and guards before inspecting density."),
    "Nuclear–Cytoplasmic Matching": Objective(START + ("matching", "quantification") + FINISH,
        "Measure both sessions on the same calibrated grid, then select the ordered pair in Associations."),
    "Custom Analysis": Objective(START + ("matching", "quantification") + FINISH,
        "Choose the stages needed for this project."),
}
# Each executable entry names an existing route. An empty route represents a deferred analysis.
DENSITY_ROUTES = {
    "Nuclear density": OBJECTIVES["Density & Quantification"].stages,
    "Matched nuclear density": OBJECTIVES["Nuclear–Cytoplasmic Matching"].stages,
    "Cytoplasmic object density": (),
    "Marker-positive fraction": (),
    "Cytoplasmic volume fraction": (),
    "Marker intensity measurements": (),
}
DESCRIPTIONS = {
    "project": "Open a calibrated image or checkpoint. Choose the session that owns this analysis.",
    "preprocessing": "Review the crop, intensity correction and normalization. Skip to use existing inputs.",
    "segmentation": "Review the input and run segmentation, or continue with the current masks.",
    "mask_review": "Calculate measurements and inspect retained objects. Review the counting ROI for density.",
    "matching": "Inspect associations, correct mask geometry or record a manual decision.",
    "quantification": "Inspect nuclear density and the effective sampling volume for the reviewed ROI.",
    "quality_control": "Inspect distributions for the current population and matching pair.",
    "export": "Choose the scientific results or prepared images to export.",
}
