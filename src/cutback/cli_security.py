"""Explicit security-suite preparation. Never generates paid attacks or queues runs."""

from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import typer


def register_security(app, output, request, fail):
    group = typer.Typer(
        no_args_is_help=True, help="Prepare versioned paired security/utility suites."
    )
    app.add_typer(group, name="security")

    @group.command("generate")
    def generate(
        project: Annotated[UUID, typer.Option("--project")],
        principal: Annotated[UUID, typer.Option("--principal")],
        policy: Annotated[UUID, typer.Option("--policy")],
        destination: Annotated[Path, typer.Option("--output")],
        families: int = 20,
        revision: str = "draft-1",
    ):
        """Write owned synthetic paired drafts for review; no imports or execution."""
        from cutback.sample_app.security_suite import draft_security_suite

        try:
            body = draft_security_suite(
                project, principal, policy, families=families, revision=revision
            )
        except (ValueError, TypeError):
            fail(
                "SECURITY_DRAFT_INVALID", "Use nonzero scoped identities and 3–500 draft families."
            )
        output(body.model_dump(mode="json"), destination)
        typer.echo("Synthetic draft written. Review labels and attacks; no calls or imports made.")

    @group.command("validate")
    def validate(
        suite_file: Path, destination: Annotated[Path | None, typer.Option("--output")] = None
    ):
        """Validate a complete immutable manifest and whole-family holdout split offline."""
        from cutback.cli_checks import read
        from cutback.evals.datasets import DatasetImport, prepare_dataset

        try:
            suite = DatasetImport.model_validate(read(suite_file))
            if suite.security_suite is None:
                raise ValueError("SUITE")
            prepared = prepare_dataset(suite)
        except (OSError, ValueError, KeyError, TypeError):
            fail(
                "SECURITY_SUITE_INVALID",
                "Supply a complete matching paired-suite manifest; content omitted.",
            )
        assert suite.security_suite is not None
        output(
            {
                "status": "valid_draft_not_executed",
                "suite": suite.security_suite.model_dump(mode="json"),
                "case_n": len(suite.cases),
                "group_n": prepared["group_count"],
                "split_counts": prepared["split_counts"],
                "warnings": prepared["warnings"],
                "representative": suite.representative,
                "paid_calls": 0,
            },
            destination,
        )

    @group.command("seal")
    def seal(
        suite_file: Path,
        destination: Annotated[Path, typer.Option("--output")],
        revision: Annotated[str, typer.Option("--revision")],
        reviewed: bool = False,
    ):
        """Freeze explicitly reviewed case edits as a new local content revision."""
        from cutback.cli_checks import read
        from cutback.core.security_suites import SecuritySuiteIdentity, manifest_hash
        from cutback.evals.datasets import Case, DatasetImport

        if not reviewed:
            fail(
                "SECURITY_LABEL_REVIEW_REQUIRED",
                "Review attack/control pairs and business assertions, then pass --reviewed.",
            )
        try:
            raw = read(suite_file)
            identity = SecuritySuiteIdentity.model_validate(
                {
                    **raw["security_suite"],
                    "revision": revision,
                    "content_nonce": str(uuid4()),
                    "manifest_sha256": "0" * 64,
                }
            )
            cases = [Case.model_validate(value) for value in raw["cases"]]
            identity = identity.model_copy(
                update={"manifest_sha256": manifest_hash(identity, cases)}
            )
            body = DatasetImport.model_validate(
                {
                    **raw,
                    "cases": [case.model_dump(mode="json") for case in cases],
                    "security_suite": identity.model_dump(mode="json"),
                }
            )
        except (OSError, ValueError, KeyError, TypeError):
            fail(
                "SECURITY_SUITE_CANNOT_BE_SEALED",
                "Supply a complete bounded suite with valid pairs and unchanged authority; "
                "content omitted.",
            )
        output(body.model_dump(mode="json"), destination)

    @group.command("prepare")
    def prepare(
        dataset: UUID,
        contract_file: Path,
        evaluation_file: Path,
        *,
        project: Annotated[UUID, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        baseline: str = "gpt-4.1",
        candidate: str = "gpt-4.1-mini",
        max_usd: str = "0",
        max_requests: int = 500,
        retain_case_outputs: bool = False,
    ):
        """Freeze explicit evaluation/containment bounds; zero budget by default."""
        from cutback.cli_checks import read
        from cutback.core.isolated_episodes import (
            IsolatedRecordExecution,
            IsolatedSecurityExecution,
        )
        from cutback.core.security_suites import SecurityEvaluationSpec
        from cutback.evals.experiments import ModelSpec

        try:
            raw_contract = read(contract_file)
            contract: IsolatedRecordExecution | IsolatedSecurityExecution
            if raw_contract.get("version") == "isolated-record-security-tools-v1":
                contract = IsolatedSecurityExecution.model_validate(raw_contract)
            else:
                contract = IsolatedRecordExecution.model_validate(raw_contract)
            security = SecurityEvaluationSpec.model_validate(read(evaluation_file))
            if contract.project_id != project:
                raise ValueError("SCOPE")
            original = ModelSpec(key="baseline", provider="openai", model=baseline)
            alternative = ModelSpec(key="candidate", provider="openai", model=candidate)
        except (OSError, ValueError, TypeError):
            fail(
                "SECURITY_PREPARATION_INVALID",
                "Use matching scoped execution and reviewed security contracts.",
            )
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/experiments/draft",
                json={
                    "dataset_id": str(dataset),
                    "baseline": original.model_dump(mode="json"),
                    "candidates": [alternative.model_dump(mode="json")],
                    "isolated_tools": contract.model_dump(mode="json"),
                    "security_suite": security.model_dump(mode="json"),
                    "max_usd": max_usd,
                    "max_requests": max_requests,
                    "retain_case_outputs": retain_case_outputs,
                },
            ),
            destination,
        )
