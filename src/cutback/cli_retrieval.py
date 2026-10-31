"""Explicit corpus preparation and source-bound retrieval review commands."""

from pathlib import Path
from typing import Annotated

import typer


def register_retrieval(app, output, request, fail):
    group = typer.Typer(
        no_args_is_help=True, help="Prepare corpus retrieval search and review changes."
    )
    app.add_typer(group, name="retrieval")

    @group.command("inspect-config")
    def inspect(
        root: Path, configuration: str, destination: Annotated[Path, typer.Option("--output")]
    ):
        """Inspect one settings-only JSON file in a clean Git checkout. No source execution."""
        from cutback.retrieval_changes import inspect_configuration

        try:
            settings, binding = inspect_configuration(root, configuration)
        except (ValueError, OSError):
            fail(
                "RETRIEVAL_CONFIGURATION_NOT_QUALIFIED",
                "Select a tracked settings-only JSON file in a clean Git root.",
            )
        if destination.resolve().is_relative_to(root.resolve()):
            fail(
                "OUTPUT_MUST_STAY_OUTSIDE_SOURCE_TREE",
                "Write inspection output outside the clean application root.",
            )
        output(
            {
                "settings": settings.model_dump(mode="json"),
                "configuration_binding": binding.model_dump(mode="json"),
            },
            destination,
        )

    @group.command("draft")
    def draft(
        request_file: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        authorize_corpus_upload: bool = False,
    ):
        """Upload an explicitly authorized corpus to encrypted storage; makes no inference calls."""
        from uuid import UUID

        from cutback.cli_checks import read

        if not authorize_corpus_upload:
            fail(
                "EXPLICIT_CORPUS_UPLOAD_AUTHORIZATION_REQUIRED",
                "Review content rights and capture policy, then pass --authorize-corpus-upload.",
            )
        try:
            body = read(request_file)
            project = str(UUID(project))
        except (OSError, ValueError):
            fail("RETRIEVAL_DRAFT_INPUT_INVALID", "Use the documented retrieval draft contract.")
        output(
            request("POST", endpoint, f"/v1/projects/{project}/retrieval/draft", json=body),
            destination,
        )

    @group.command("confirm")
    def confirm(
        validation_run: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        max_usd: Annotated[str, typer.Option("--max-usd")],
        max_requests: int = 100,
        max_wall_seconds: int = 300,
    ):
        """Derive one selected alternative on untouched groups. Approval is still required."""
        from uuid import UUID

        from cutback.evals.experiments import Budget

        try:
            project = str(UUID(project))
            run = str(UUID(validation_run))
            budget = Budget.model_validate(
                {
                    "max_usd": max_usd,
                    "max_requests": max_requests,
                    "max_wall_seconds": max_wall_seconds,
                }
            )
        except ValueError:
            fail(
                "RETRIEVAL_CONFIRMATION_INPUT_INVALID",
                "Supply exact project/run UUIDs and a bounded budget.",
            )
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/retrieval/confirmation",
                json={"validation_run_id": run, "budget": budget.model_dump(mode="json")},
            ),
            destination,
        )

    @group.command("proposal")
    def proposal(
        root: Path,
        confirmation_run: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        purpose: Annotated[str, typer.Option("--purpose")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Prepare an exact diff plus rollback from scoped confirmation. Never applies."""
        from uuid import UUID

        from pydantic import TypeAdapter

        from cutback.core.models import Identifier
        from cutback.retrieval_changes import RetrievalProposal, prepare_patch

        try:
            project = str(UUID(project))
            run = str(UUID(confirmation_run))
            purpose = TypeAdapter(Identifier).validate_python(purpose)
            if destination.resolve().is_relative_to(root.resolve()):
                raise ValueError("output")
            source = request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/retrieval/{run}/proposal",
                json={"purpose": purpose},
                token_env="CUTBACK_ADMIN_TOKEN",
            )
            change = prepare_patch(root, RetrievalProposal.model_validate(source))
        except (ValueError, OSError):
            fail(
                "RETRIEVAL_PROPOSAL_NOT_QUALIFIED",
                "Verify untouched confirmation, current policy and exact clean source. "
                "Write output outside that tree.",
            )
        output(change, destination)
        destination.with_suffix(".patch").write_text(change["diff"], encoding="utf-8", newline="")
        destination.with_suffix(".rollback.patch").write_text(
            change["rollback_diff"], encoding="utf-8", newline=""
        )

    @group.command("patch")
    def patch(
        root: Path,
        proposal_file: Path,
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Materialize a proposal or exact agent change as diff/rollback. Never applies."""
        from cutback.cli_checks import read
        from cutback.retrieval_changes import RetrievalProposal, prepare_patch

        try:
            if destination.resolve().is_relative_to(root.resolve()):
                raise ValueError("output")
            downloaded = read(proposal_file)
            agent_change = downloaded.get("version") == "cutback.retrieval-change.v1"
            proposed = downloaded.get("proposal") if agent_change else downloaded
            result = prepare_patch(root, RetrievalProposal.model_validate(proposed))
            if agent_change and downloaded != result:
                raise ValueError("RETRIEVAL_AGENT_CHANGE_MODIFIED")
        except (ValueError, OSError):
            fail(
                "RETRIEVAL_PATCH_INPUT_OR_SOURCE_CHANGED",
                "Use the exact clean application and downloaded proposal. "
                "A local JSON file is not an independent server attestation.",
            )
        output(result, destination)
        destination.with_suffix(".patch").write_text(result["diff"], encoding="utf-8", newline="")
        destination.with_suffix(".rollback.patch").write_text(
            result["rollback_diff"], encoding="utf-8", newline=""
        )

    @group.command("review")
    def review(
        artifact_file: Path,
        contract_file: Path,
        receipt_file: Path,
        base_root: Path,
        proposed_root: Path,
        coverage: Annotated[Path, typer.Option()],
        destination: Annotated[Path, typer.Option("--output")],
        branch: str | None = None,
    ):
        """Verify exact local checks and optionally create a review ref. Never pushes."""
        from cutback.cli_checks import read
        from cutback.retrieval_changes import review_change

        try:
            if any(
                destination.resolve().is_relative_to(root.resolve())
                for root in (base_root, proposed_root)
            ):
                raise ValueError("output")
            result = review_change(
                read(artifact_file),
                read(contract_file),
                read(receipt_file),
                base_root,
                proposed_root,
                read(coverage),
                branch=branch,
            )
        except (ValueError, OSError, KeyError, TypeError):
            fail(
                "RETRIEVAL_REVIEW_NOT_QUALIFIED",
                "Verify both exact clean trees, their checks "
                "and declared settings consumers. Never reuse a different branch ref.",
            )
        output(result, destination)

    @group.command("query")
    def query(
        corpus_file: Path,
        settings_file: Path,
        question: str,
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Read local corpus text and write retrieved content. No network or inference."""
        from cutback.cli_checks import read
        from cutback.core.retrieval import LocalRetrievalIndex, RetrievalCorpus, RetrievalSettings

        try:
            index = LocalRetrievalIndex(
                RetrievalCorpus.model_validate(read(corpus_file)),
                RetrievalSettings.model_validate(read(settings_file)),
            )
            result = index.query(question)
        except (ValueError, OSError):
            fail("RETRIEVAL_QUERY_INPUT_INVALID", "Use bounded owned corpus and settings JSON.")
        output(
            {
                "documents": [p.wire() for p in result.passages],
                "context_bytes": result.context_bytes,
                "scored_chunks": result.scored_chunks,
                "rendered_input": result.render(question),
            },
            destination,
        )
