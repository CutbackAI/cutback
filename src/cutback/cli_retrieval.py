"""Explicit corpus preparation and source-bound retrieval review commands."""

from pathlib import Path
from typing import Annotated

import typer


def register_retrieval(app, output, request, fail, *, joint=False):
    group = typer.Typer(
        no_args_is_help=True, help="Prepare corpus retrieval search and review changes."
    )
    route = "model-retrieval" if joint else "retrieval"
    app.add_typer(group, name=route)

    def private_output(value, destination):
        from cutback.core.workspace import write_private_output

        write_private_output(destination, value)

    if joint:
        output = private_output

    def prepare_change(root, value):
        if joint:
            from cutback.joint_retrieval_changes import JointRetrievalProposal
            from cutback.joint_retrieval_changes import prepare_patch as prepare_joint

            return prepare_joint(root, JointRetrievalProposal.model_validate(value))
        from cutback.retrieval_changes import RetrievalProposal, prepare_patch

        return prepare_patch(root, RetrievalProposal.model_validate(value))

    def artifact_output(value, destination):
        from cutback.core.workspace import write_private_text_output

        output(value, destination)
        write_private_text_output(destination.with_suffix(".patch"), value["diff"])
        write_private_text_output(
            destination.with_suffix(".rollback.patch"), value["rollback_diff"]
        )

    def preflight(destination, root):
        from cutback.core.workspace import prepare_private_output

        paths = [
            destination,
            destination.with_suffix(".patch"),
            destination.with_suffix(".rollback.patch"),
        ]
        if len(set(path.absolute() for path in paths)) != 3 or any(
            path.resolve().is_relative_to(root.resolve()) for path in paths
        ):
            raise ValueError("OUTPUT_MUST_STAY_OUTSIDE_SOURCE_TREE")
        for path in paths:
            prepare_private_output(path)

    if joint:

        @group.command("inspect-producer")
        def inspect_native(
            root: Path, producer: str, destination: Annotated[Path, typer.Option("--output")]
        ):
            """Parse the exact native consumer and tracked corpus identity. Never executes."""
            from cutback.retrieval_source import inspect_producer

            try:
                preflight(destination, root)
                settings, binding, native = inspect_producer(root, producer)
                output(
                    {
                        "settings": settings.model_dump(mode="json"),
                        "configuration_binding": binding.model_dump(mode="json"),
                        "producer_binding": native.model_dump(mode="json"),
                    },
                    destination,
                )
            except (ValueError, OSError):
                fail(
                    "JOINT_RETRIEVAL_PRODUCER_NOT_QUALIFIED",
                    "Use a clean tracked qualified corpus consumer "
                    "and private output outside its tree.",
                )

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
            if joint:
                from cutback.core.workspace import prepare_private_output

                prepare_private_output(destination)
        except (OSError, ValueError):
            fail("RETRIEVAL_DRAFT_INPUT_INVALID", "Use the documented retrieval draft contract.")
        output(
            request("POST", endpoint, f"/v1/projects/{project}/{route}/draft", json=body),
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
        candidate: str | None = None,
    ):
        """Derive one selected alternative on untouched groups. Approval is still required."""
        from uuid import UUID

        from cutback.evals.experiments import Budget

        if candidate is not None and not joint:
            fail(
                "EXPLICIT_CANDIDATE_REQUIRES_JOINT_RETRIEVAL",
                "Use the joint contract to select an explicit candidate.",
            )
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
            if joint:
                from cutback.core.workspace import prepare_private_output

                prepare_private_output(destination)
        except ValueError:
            fail(
                "RETRIEVAL_CONFIRMATION_INPUT_INVALID",
                "Supply exact project/run UUIDs and a bounded budget.",
            )
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/{route}/confirmation",
                json={
                    "validation_run_id": run,
                    "budget": budget.model_dump(mode="json"),
                    **({"candidate": candidate} if joint else {}),
                },
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

        try:
            project = str(UUID(project))
            run = str(UUID(confirmation_run))
            purpose = TypeAdapter(Identifier).validate_python(purpose)
            preflight(destination, root)
            source = request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/{route}/{run}/proposal",
                json={"purpose": purpose},
                token_env="CUTBACK_ADMIN_TOKEN",
            )
            change = prepare_change(root, source)
        except (ValueError, OSError):
            fail(
                "RETRIEVAL_PROPOSAL_NOT_QUALIFIED",
                "Verify untouched confirmation, current policy and exact clean source. "
                "Write output outside that tree.",
            )
        artifact_output(change, destination)

    @group.command("patch")
    def patch(
        root: Path,
        proposal_file: Path,
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Materialize a proposal or exact agent change as diff/rollback. Never applies."""
        from cutback.cli_checks import read

        try:
            preflight(destination, root)
            downloaded = read(proposal_file)
            agent_change = downloaded.get("version") == (
                "cutback.joint-retrieval-change.v1" if joint else "cutback.retrieval-change.v1"
            )
            proposed = downloaded.get("proposal") if agent_change else downloaded
            result = prepare_change(root, proposed)
            if agent_change and downloaded != result:
                raise ValueError("RETRIEVAL_AGENT_CHANGE_MODIFIED")
        except (ValueError, OSError):
            fail(
                "RETRIEVAL_PATCH_INPUT_OR_SOURCE_CHANGED",
                "Use the exact clean application and downloaded proposal. "
                "A local JSON file is not an independent server attestation.",
            )
        artifact_output(result, destination)

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

        if joint:
            from cutback.joint_retrieval_changes import review_change
        else:
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
