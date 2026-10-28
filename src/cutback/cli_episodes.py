"""Explicit offline episode content operations, separate from scanning."""

from pathlib import Path
from typing import Annotated

import typer


def register_episodes(app, output, fail):
    group = typer.Typer(
        no_args_is_help=True,
        help="Assess typed synthetic trajectories and draft reviewable scenario variants offline.",
    )
    app.add_typer(group, name="episodes")

    @group.command("assess")
    def assess(
        scenario_file: Path,
        observation_file: Path,
        destination: Annotated[Path | None, typer.Option("--output")] = None,
    ):
        from cutback.cli_checks import read
        from cutback.core.episodes import EpisodeObservation, EpisodeScenario
        from cutback.evals.episode_metrics import assess_episode

        try:
            scenario = EpisodeScenario.model_validate(read(scenario_file))
            observed = EpisodeObservation.model_validate(read(observation_file))
            result = assess_episode(scenario, observed)
        except (OSError, ValueError, KeyError, TypeError):
            fail(
                "EPISODE_INPUT_INVALID",
                "Use the documented bounded scenario and observation schemas; content omitted.",
            )
        output(result, destination)

    @group.command("generate")
    def generate(source_file: Path, destination: Annotated[Path, typer.Option("--output")]):
        from cutback.cli_checks import read
        from cutback.evals.datasets import DatasetImport
        from cutback.evals.scenarios import generate_scenarios

        try:
            source = DatasetImport.model_validate(read(source_file))
            result = generate_scenarios(source)
        except (OSError, ValueError, KeyError, TypeError):
            fail(
                "EPISODE_GENERATION_INPUT_INVALID",
                "Supply owned synthetic episode cases; review generated labels and scenarios "
                "before import.",
            )
        output(result, destination)
        typer.echo(
            "Reviewable scenario draft written. No provider calls or dataset import performed."
        )
