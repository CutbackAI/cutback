"""Bounded, parse-only experiment authoring; files never execute application code."""

import json
from pathlib import Path

from pydantic import Field

from cutback.core.models import StrictModel
from cutback.evals.experiments import ExperimentPlan


class Recipe(StrictModel):
    dataset_id: str = Field(min_length=1, max_length=100)
    specification: ExperimentPlan


def read_recipe(path: Path) -> Recipe:
    if path.stat().st_size > 256000:
        raise ValueError("EXPERIMENT_FILE_TOO_LARGE")
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Recipe.model_validate(raw)


def compile_recipe(source: Path, destination: Path):
    recipe = read_recipe(source)
    if destination.exists():
        raise ValueError("EXPERIMENT_OUTPUT_EXISTS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(recipe.model_dump(mode="json"), stream, indent=2)
    return {
        "plan_file": str(destination),
        "provider_calls": 0,
        "next_action": (
            "Submit the file, review the server budget, approve its exact hash, then queue one run."
        ),
    }
