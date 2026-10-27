"""Operational CLI surfaces; network and payload actions always require explicit commands."""

import functools
import json
import os
import time
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import typer


def read_json(path):
    if path.is_symlink() or path.stat().st_size > 1048576:
        raise ValueError("INPUT_TOO_LARGE_OR_UNSAFE")
    return json.loads(path.read_text(encoding="utf-8"))


def register(app, output, request, fail):
    from cutback.cli_advanced import register as register_advanced

    advanced_groups = register_advanced(app, output, request)

    @app.command("compile-experiment")
    def compile_experiment(source: Path, destination: Path):
        """Validate a YAML experiment and write a submission file offline; never calls a model."""
        from cutback.evals.recipes import compile_recipe

        try:
            output(compile_recipe(source, destination))
        except (ValueError, OSError):
            fail(
                "INVALID_EXPERIMENT_FILE",
                "Check the YAML schema and use a new destination file; no calls were made.",
            )

    scans = typer.Typer(no_args_is_help=True, help="Register and compare offline inventories.")
    prices = typer.Typer(no_args_is_help=True, help="Inspect or import immutable price catalogs.")
    datasets = typer.Typer(
        no_args_is_help=True, help="Discover, import and inspect evaluation cases."
    )
    experiments = typer.Typer(
        no_args_is_help=True, help="Plan, approve and inspect bounded evaluations."
    )
    recommendations = typer.Typer(
        no_args_is_help=True, help="Review evidence and export change plans."
    )
    worker = typer.Typer(no_args_is_help=True, help="Execute approved queued experiments.")
    for name, group in (
        ("scans", scans),
        ("prices", prices),
        ("datasets", datasets),
        ("experiments", experiments),
        ("recommendations", recommendations),
        ("worker", worker),
    ):
        app.add_typer(group, name=name)

    @scans.command("upload")
    def upload(project: UUID, file: Path, endpoint: str = "http://127.0.0.1:8080"):
        """Upload using the configured scoped credentials."""
        from cutback.core.inventory import Inventory

        body = Inventory.model_validate(read_json(file))
        output(
            request(
                "POST", endpoint, f"/v1/projects/{project}/scans", json=body.model_dump(mode="json")
            )
        )

    @prices.command("show")
    def price_show():
        """Price show using the configured scoped credentials."""
        from cutback.pricing import Catalog

        output(Catalog().data)

    @prices.command("update")
    def price_update(file: Path, sha256: str, destination: Path = Path(".cutback/prices")):
        """Price update using the configured scoped credentials."""
        from cutback.pricing.importer import import_snapshot

        if file.stat().st_size > 2097152:
            fail("CATALOG_TOO_LARGE", "use a vetted catalog below 2 MiB")
        try:
            output(import_snapshot(file.read_bytes(), sha256, destination))
        except ValueError:
            fail("CATALOG_REJECTED", "verify checksum, schema, aliases and immutable version")

    @datasets.command("discover")
    def discover(target: Path = Path(".")):
        """Discover using the configured scoped credentials."""
        from cutback.evals.datasets import discover_fixtures

        output({"fixtures": discover_fixtures(target), "content_read": False, "uploaded": False})

    @datasets.command("preview-import")
    def preview_import(file: Path, profile: Annotated[str, typer.Option()]):
        """Inspect a qualified format offline. No content upload or evaluator execution."""
        from cutback.evals.importers import import_profile, preview_file

        try:
            selected = import_profile(profile)
            output(preview_file(file, selected).preview())
        except (ValueError, OSError):
            fail(
                "IMPORT_PREVIEW_REJECTED",
                "Check the explicit versioned profile and bounded UTF-8 file; "
                "nothing was uploaded.",
            )

    @datasets.command("prepare")
    def prepare_records(
        file: Path,
        workflow: str,
        policy_id: UUID,
        provenance_ref: str,
        destination: Annotated[Path, typer.Option("--output")],
        source_type: Literal[
            "synthetic_fixture", "human_labeled", "application_records"
        ] = "synthetic_fixture",
        environment: str = "staging",
        independent_labels: bool = False,
        representative: bool = False,
    ):
        """Prepare records using the configured scoped credentials."""
        from cutback.evals.datasets import DatasetImport
        from cutback.evals.records import read_records

        body = DatasetImport(
            workflow=workflow,
            environment=environment,
            policy_id=str(policy_id),
            source_type=source_type,
            provenance_ref=provenance_ref,
            independent_labels=independent_labels,
            representative=representative,
            cases=read_records(file),
        )
        with os.fdopen(
            os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8"
        ) as stream:
            json.dump(body.model_dump(mode="json"), stream, ensure_ascii=False)
        output(
            {
                "file": str(destination),
                "examples": len(body.cases),
                "independent_labels": independent_labels,
                "uploaded": False,
                "next_action": "review rights and examples before explicit import",
            }
        )

    @datasets.command("import")
    def import_dataset(project: UUID, file: Path, endpoint: str = "http://127.0.0.1:8080"):
        """Import dataset using the configured scoped credentials."""
        from cutback.evals.datasets import DatasetImport

        body = DatasetImport.model_validate(read_json(file))
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/datasets",
                json=body.model_dump(mode="json"),
            )
        )

    @experiments.command("plan")
    def plan_experiment(
        project: UUID,
        dataset_id: UUID,
        baseline: str,
        workflow: str,
        destination: Annotated[Path, typer.Option("--output")],
        provider: Literal["openai", "anthropic"] = "openai",
        max_usd: str = "0",
        max_requests: int = 100,
        minimum_accuracy: float | None = None,
        endpoint: str = "http://127.0.0.1:8080",
    ):
        """Plan experiment using the configured scoped credentials."""
        from cutback.evals.experiments import ModelSpec
        from cutback.evals.planning import draft_plan

        found = request("GET", endpoint, f"/v1/projects/{project}/datasets")
        dataset = next((d for d in found if d["id"] == str(dataset_id)), None)
        if not dataset:
            fail("DATASET_NOT_FOUND", "import an authorized dataset in this project first")
        assert dataset is not None
        if workflow != dataset["workflow"]:
            fail("DATASET_WORKFLOW_MISMATCH", "use the frozen dataset workflow")
        spec = draft_plan(
            dataset,
            ModelSpec(key="baseline", provider=provider, model=baseline),
            minimum=minimum_accuracy,
            max_usd=max_usd,
            max_requests=max_requests,
        )
        task = dataset["provenance"].get("task_inference", {})
        metric = spec.metric.kind
        semantic = metric != "nonempty"
        output(
            {"dataset_id": str(dataset_id), "specification": spec.model_dump(mode="json")},
            destination,
        )
        output(
            {
                "plan_file": str(destination),
                "provider_calls": 0,
                "approval_status": "pending",
                "task_inference": task,
                "quality_scope": f"heldout_{metric}" if semantic else "technical_only_not_verified",
                "next_action": "review plan then submit, approve its hash, and queue one run",
            }
        )

    @experiments.command("submit")
    def submit_plan(project: UUID, file: Path, endpoint: str = "http://127.0.0.1:8080"):
        """Submit plan using the configured scoped credentials."""
        output(
            request(
                "POST", endpoint, f"/v1/projects/{project}/experiments/plans", json=read_json(file)
            )
        )

    @experiments.command("approve")
    def approve_plan(
        project: UUID,
        plan_id: UUID,
        plan_hash: str,
        approval_ref: str,
        endpoint: str = "http://127.0.0.1:8080",
    ):
        """Approve plan using the configured scoped credentials."""
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/experiments/plans/{plan_id}/approve",
                token_env="CUTBACK_ADMIN_TOKEN",
                json={"plan_hash": plan_hash, "approval_ref": approval_ref},
            )
        )

    @experiments.command("run")
    def queue_run(project: UUID, plan_id: UUID, endpoint: str = "http://127.0.0.1:8080"):
        """Queue run using the configured scoped credentials."""
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/experiments",
                json={"plan_id": str(plan_id)},
            )
        )

    @experiments.command("report")
    def run_report(project: UUID, run_id: UUID, endpoint: str = "http://127.0.0.1:8080"):
        """Run report using the configured scoped credentials."""
        output(request("GET", endpoint, f"/v1/projects/{project}/experiments/{run_id}"))

    @recommendations.command("export")
    def export_recs(
        project: UUID,
        destination: Annotated[Path | None, typer.Option("--output")] = None,
        endpoint: str = "http://127.0.0.1:8080",
    ):
        """Export recs using the configured scoped credentials."""
        output(request("GET", endpoint, f"/v1/projects/{project}/recommendations"), destination)

    @recommendations.command("patch")
    def patch(
        target: Path,
        inventory: Path,
        recommendation: Path,
        call_site: str,
        source_sha256: str,
        destination: Annotated[Path | None, typer.Option("--output")] = None,
        dry_run: bool = True,
        upload_project: UUID | None = None,
        approval_ref: str | None = None,
        endpoint: str = "http://127.0.0.1:8080",
    ):
        """Patch using the configured scoped credentials."""
        from cutback.changes import model_patch

        if not dry_run:
            fail(
                "APPLY_REQUIRES_APPLICATION_REVIEW",
                "export a dry-run diff and use your review workflow",
            )
        try:
            result = model_patch(
                target,
                read_json(inventory),
                call_site,
                read_json(recommendation),
                expected_source_sha256=source_sha256,
            )
        except (ValueError, OSError, KeyError):
            fail(
                "PATCH_REFUSED",
                "check review status, source hash, commit and literal model compatibility",
            )
        if upload_project:
            if not approval_ref:
                fail(
                    "UPLOAD_REQUIRES_APPROVAL_REFERENCE",
                    "source diffs contain application content; "
                    "authorize encrypted upload explicitly",
                )
            result["uploaded_change"] = request(
                "POST",
                endpoint,
                f"/v1/projects/{upload_project}/recommendations/"
                f"{result['recommendation_id']}/patches",
                token_env="CUTBACK_ADMIN_TOKEN",
                json={"approval_ref": approval_ref, "artifact": result},
            )
        output(result, destination)

    @worker.command("maintain")
    def maintenance(apply: bool = False, raw_days: int = 30, aggregate_days: int = 395):
        """Maintenance using the configured scoped credentials."""
        from cutback.server.db import database
        from cutback.server.operations import maintain_all
        from cutback.server.worker import expire_payloads

        engine, sessions = database(os.environ["CUTBACK_DATABASE_URL"])
        try:
            result = maintain_all(
                sessions, apply=apply, raw_days=raw_days, aggregate_days=aggregate_days
            )
            if apply and os.getenv("CUTBACK_PAYLOAD_KEY"):
                from cutback.evals.artifacts import configured_store

                result["payloads"] = expire_payloads(sessions, configured_store())
            output(result)
        finally:
            engine.dispose()

    @worker.command("run")
    def worker_run(once: bool = True):
        """Worker run using the configured scoped credentials."""
        from cutback.evals.artifacts import configured_store
        from cutback.server.db import database
        from cutback.server.worker import configured_execution_enabled, run_one

        engine, sessions = database(os.environ["CUTBACK_DATABASE_URL"])
        try:
            enabled = configured_execution_enabled()
            if not enabled:
                output({"status": "EXPERIMENT_EXECUTION_DISABLED", "provider_calls": 0})
                return
            store = configured_store()
            while True:
                output(run_one(sessions, store, execution_enabled=enabled))
                if once:
                    break
                time.sleep(2)
        finally:
            engine.dispose()

    @app.command("dashboard")
    def dashboard(endpoint: str = "http://127.0.0.1:8080", local: bool = True):
        """Open an existing collector dashboard; does not start a server."""
        import webbrowser

        from cutback.core.config import validate_endpoint

        url = validate_endpoint(endpoint) + "/dashboard/"
        webbrowser.open(url)
        output({"url": url, "credentials": "enter control key in browser memory; never in URL"})

    @app.command("uninstall")
    def uninstall(target: Path = Path("."), plan: bool = True):
        """Plan removal of instrumentation and credentials."""
        from cutback.scanner import scan

        inventory = scan(target)
        output(
            {
                "status": "PLAN_ONLY",
                "source_files": inventory["coverage"],
                "steps": [
                    "set CUTBACK_ENABLED=false and verify primary app behavior",
                    "remove the marked cutback bootstrap and handler flush wrapper after review",
                    "revoke ingest/control keys through the control API",
                    "stop collector/worker after exporting or deleting authorized evidence",
                    "remove cutback from the application lockfile and redeploy with approval",
                ],
                "files_changed": False,
                "data_deleted": False,
            }
        )

    def protect(callback):
        @functools.wraps(callback)
        def guarded(*args, **kwargs):
            try:
                return callback(*args, **kwargs)
            except (ValueError, OSError, KeyError):
                fail(
                    "INVALID_COMMAND_INPUT_OR_CONFIGURATION",
                    "check schema, paths and runtime settings; sensitive details omitted",
                )
            except ImportError:
                fail("MISSING_EXTRA", "install the documented server/eval/provider extra")

        return guarded

    for group in (scans, prices, datasets, experiments, recommendations, worker, *advanced_groups):
        for command in group.registered_commands:
            if command.callback:
                command.callback = protect(command.callback)
