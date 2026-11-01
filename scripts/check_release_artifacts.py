"""Check release payloads, metadata and internal-file exclusion before upload."""

import email
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath


def check_application_payload(wheel_path: Path, source_path: Path, root: Path):
    """Require exact application bytes in source, wheel and sdist; never extract archives."""
    package = root / "src/cutback"
    expected = {}
    for path in package.rglob("*"):
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError("application source must not contain symlinks")
        if path.is_file():
            expected[path.relative_to(package.parent).as_posix()] = path.read_bytes()
    if not expected:
        raise ValueError("application source is empty")
    with zipfile.ZipFile(wheel_path) as wheel:
        if len(wheel.namelist()) != len(set(wheel.namelist())):
            raise ValueError("wheel contains duplicate members")
        actual = {
            name: wheel.read(name)
            for name in wheel.namelist()
            if name.startswith("cutback/") and not name.endswith("/")
        }
    with tarfile.open(source_path) as source:
        members = source.getmembers()
        if len(members) != len({member.name for member in members}):
            raise ValueError("source distribution contains duplicate members")
        archived = {}
        for member in members:
            parts = PurePosixPath(member.name).parts
            if len(parts) < 4 or parts[1:3] != ("src", "cutback"):
                continue
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("source application member must be a regular file")
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError("source application member is unreadable")
            name = "/".join(parts[2:])
            if name in archived:
                raise ValueError("source distribution contains ambiguous application roots")
            archived[name] = stream.read()
    if actual != expected or archived != expected:
        # Do not include file contents, names or potentially private checkout paths.
        raise ValueError(
            "application payload differs between source, wheel and source distribution"
        )
    return len(expected)


def check(directory: Path):
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("expected exactly one wheel and one source distribution")
    banned = (
        "MASTER_ENGINEERING_HANDOFF",
        "private-notes",
        "secrets/",
        ".env",
        "synthetic-compose-report",
        "changed-files.txt",
        "prepare_public_release",
    )
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        (metadata_name,) = (name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = email.message_from_bytes(wheel.read(metadata_name))
        if re.search(r"\]\((?!https?://|#)[^)]+\)", metadata.get_payload()):
            raise ValueError("package README contains index-broken relative links")
        version = tomllib.loads(
            (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        )["project"]["version"]
        if metadata["Name"] != "cutback" or metadata["Version"] != version:
            raise ValueError("unexpected release identity")
        if "Development Status :: 2 - Pre-Alpha" not in metadata.get_all("Classifier", []):
            raise ValueError("development status must be explicit")
        if not any(name.endswith("licenses/LICENSE") for name in names):
            raise ValueError("wheel license missing")
    with tarfile.open(sources[0]) as source:
        source_names = source.getnames()
        names += source_names
        for member in source.getmembers():
            if member.isfile() and Path(member.name).suffix in {
                ".md",
                ".py",
                ".toml",
                ".json",
                ".jsonl",
                ".ts",
                ".tsx",
                ".js",
                ".mjs",
                ".yml",
                ".yaml",
                ".txt",
                ".css",
                ".html",
                ".svg",
            }:
                stream = source.extractfile(member)
                if stream is None:
                    raise ValueError("source archive text member is unreadable")
                try:
                    stream.read().decode("utf-8")
                except UnicodeError:
                    raise ValueError(
                        "source archive contains non-UTF8 text: " + member.name
                    ) from None
        for required in (
            "docs/learned-routing.md",
            "examples/learned-routing/README.md",
            "examples/learned-routing/app.py",
            "examples/learned-routing/check_application.py",
            "examples/learned-routing/routing.json",
            "src/cutback/core/input_routing.py",
            "src/cutback/server/routing_api.py",
            "src/cutback/server/routing_service.py",
            "src/cutback/demo_routing.py",
            "tests/end_to_end/test_learned_routing_sample.py",
            "web/tests/routing.spec.ts",
            ".dockerignore",
            "scripts/image_acceptance.py",
            "examples/model-context-search/README.md",
            "examples/model-context-search/app.py",
            "examples/model-context-search/check_application.py",
            "examples/model-context-search/questions.jsonl",
            "docs/joint-model-context.md",
            "docs/reviewed-criteria.md",
            "docs/agent-reviewed-criteria.md",
            "docs/semantic-rubrics.md",
            "examples/semantic-rubrics/README.md",
            "examples/reviewed-criteria/README.md",
            "examples/reviewed-criteria/app.py",
            "examples/reviewed-criteria/check_application.py",
            "src/cutback/demo_criteria.py",
            "tests/end_to_end/test_reviewed_criteria_pipeline.py",
            "src/cutback/core/context_producer.py",
            "tests/integration/test_agent_joint_context_workflow.py",
            "tests/unit/test_demo_prerequisites.py",
            ".github/workflows/publish.yml",
            "scripts/check_toolchain.py",
            "docs/otel-ingestion.md",
            "infra/compose/docker-compose.yml",
            "infra/compose/prepare.py",
            "scripts/export_openapi.py",
            "scripts/generate_ts_client.py",
            "web/scripts/licenses.mjs",
            "packages/js-sdk/index.mjs",
            "infra/helm/cutback/Chart.yaml",
            "infra/terraform/kubernetes/main.tf",
            "infra/training/uv.lock",
            "infra/sdk-compat/README.md",
            "infra/sdk-compat/openai3/pyproject.toml",
            "infra/sdk-compat/openai3/uv.lock",
            "examples/experiment_walkthrough.py",
            "docs/local-training.md",
            "docs/testing-the-sample.md",
            "docs/experiment-files.md",
            "examples/support_desk/README.md",
            "docs/imports-and-review.md",
            "docs/prompt-releases.md",
            "docs/application-checks.md",
            "docs/online-monitoring.md",
            "examples/online-monitoring/sample.py",
            "docs/online-quality.md",
            "docs/agent-evaluation.md",
            "docs/task-economics.md",
            "docs/improvement-campaigns.md",
            "docs/evidence/simple-first-campaign-acceptance.json",
            "tests/integration/test_campaign_simple_first.py",
            "src/cutback/campaign_operator.py",
            "docs/evidence/campaign-operator-acceptance.json",
            "tests/integration/test_campaign_operator.py",
            "tests/integration/test_campaign_operator_local.py",
            "tests/unit/test_campaign_portfolio_contracts.py",
            "tests/integration/test_campaign_portfolio.py",
            "examples/automatic-portfolio/README.md",
            "scripts/check_source_snapshot.py",
            "tests/contract/test_committed_source_snapshot.py",
            "src/cutback/sample_app/ticket_campaign.py",
            "src/cutback/sample_app/ticket_fixture.py",
            "tests/unit/test_ticket_campaign_fixture.py",
            "tests/integration/test_agent_case_retention.py",
            "tests/end_to_end/test_ticket_campaign.py",
            "web/tests/ticket-campaign.spec.ts",
            "docs/provider-batches.md",
            "docs/semantic-cache.md",
            "docs/request-reuse.md",
            "examples/request-reuse/README.md",
            "examples/request-reuse/demo.py",
            "examples/request-reuse/source_application.py",
            "examples/source-workload/README.md",
            "examples/source-workload/app.py",
            "docs/source-workload.md",
            "src/cutback/core/cache_workload.py",
            "src/cutback/server/native_profile_metadata.py",
            "src/cutback/server/cache_workload_api.py",
            "src/cutback/agent_cache_workload.py",
            "src/cutback/agent_source_map.py",
            "tests/integration/test_cache_workload_assessment.py",
            "tests/integration/test_agent_cache_workload.py",
            "web/src/cache-workload.tsx",
            "src/cutback/core/native_request_profile.py",
            "src/cutback/server/native_profile_api.py",
            "src/cutback/server/native_profile_service.py",
            "src/cutback/server/native_profile_db.py",
            "src/cutback/server/migrations/versions/0032_native_request_profiles.py",
            "src/cutback/server/migrations/versions/0033_routing_improvement_programs.py",
            "src/cutback/server/migrations/versions/0034_source_policy_programs.py",
            "src/cutback/core/improvement_policy.py",
            "src/cutback/core/policy_execution.py",
            "src/cutback/server/policy_program_api.py",
            "src/cutback/server/policy_generation_worker.py",
            "src/cutback/server/policy_program_worker.py",
            "src/cutback/server/policy_program_ledger.py",
            "src/cutback/source_policy.py",
            "src/cutback/cli_policy_program.py",
            "src/cutback/agent_policy_program.py",
            "src/cutback/agent_policy_handoff.py",
            "src/cutback/policy_program_operator.py",
            "src/cutback/demo_policy_program.py",
            "docs/source-policy-programs.md",
            "examples/source-policy/README.md",
            "examples/source-policy/app.py",
            "examples/source-policy/policy.json",
            "examples/source-policy/check_application.py",
            "tests/end_to_end/test_source_policy_program_sample.py",
            "tests/end_to_end/test_source_policy_program_dashboard.py",
            "tests/integration/test_source_policy_program_exact_cache.py",
            "web/tests/source-policy-programs.spec.ts",
            "src/cutback/evals/routing_programs.py",
            "src/cutback/server/routing_program_api.py",
            "src/cutback/server/routing_program_worker.py",
            "src/cutback/agent_routing_program.py",
            "src/cutback/agent_routing_handoff.py",
            "src/cutback/application_workspaces.py",
            "src/cutback/application_handoff.py",
            "src/cutback/application_patch.py",
            "src/cutback/agent_campaign_handoff.py",
            "src/cutback/routing_program_operator.py",
            "docs/routing-programs.md",
            "docs/campaign-handoff.md",
            "examples/routing-program/README.md",
            "examples/routing-program/sample.py",
            "tests/integration/test_routing_program_workflow.py",
            "tests/integration/test_routing_program_handoff.py",
            "tests/unit/test_agent_routing_handoff_controls.py",
            "tests/unit/test_application_workspaces.py",
            "tests/unit/test_application_patch.py",
            "tests/unit/test_agent_campaign_handoff_controls.py",
            "tests/integration/test_campaign_handoff.py",
            "tests/integration/test_campaign_handoff_remote_minimal.py",
            "tests/end_to_end/test_routing_program_sample.py",
            "tests/end_to_end/test_routing_program_dashboard.py",
            "web/src/routing-programs.tsx",
            "web/tests/routing-programs.spec.ts",
            "tests/integration/test_native_request_profiles.py",
            "tests/integration/test_native_profile_recovery.py",
            "tests/end_to_end/test_native_profile_dashboard.py",
            "tests/end_to_end/test_request_reuse_example.py",
            "web/tests/request-profiles.spec.ts",
            "examples/semantic-cache/README.md",
            "src/cutback/demo_semantic_cache.py",
            "src/cutback/sample_app/semantic_cache_fixture.py",
            "src/cutback/server/migrations/versions/0022_semantic_cache.py",
            "src/cutback/server/migrations/versions/0023_chunked_semantic_datasets.py",
            "src/cutback/server/migrations/versions/0024_isolated_tool_receipts.py",
            "src/cutback/server/migrations/versions/0025_runtime_guardrails.py",
            "src/cutback/core/guardrails.py",
            "src/cutback/gateway/guarded_stream.py",
            "src/cutback/sample_app/runtime_guardrails.py",
            "docs/runtime-guardrails.md",
            "examples/runtime-guardrails/README.md",
            "tests/end_to_end/test_runtime_guardrail_dashboard.py",
            "web/tests/guardrails.spec.ts",
            "docs/isolated-tools.md",
            "docs/security-suites.md",
            "examples/paired-security/README.md",
            "src/cutback/core/security_suites.py",
            "src/cutback/evals/security_suites.py",
            "src/cutback/cli_security.py",
            "src/cutback/demo_security_suites.py",
            "tests/integration/test_security_episodes.py",
            "tests/end_to_end/test_security_suite_pipeline.py",
            "web/tests/security-suites.spec.ts",
            "examples/isolated-tools/README.md",
            "examples/provider-batches/README.md",
            "src/cutback/sample_app/batch_provider.py",
            "src/cutback/demo_provider_batches.py",
            "src/cutback/server/migrations/versions/0021_provider_batches.py",
            "tests/unit/test_provider_batch_protocol.py",
            "tests/integration/test_provider_batch_native.py",
            "tests/end_to_end/test_provider_batch_pipeline.py",
            "tests/end_to_end/test_provider_batch_sample.py",
            "web/tests/provider-batches.spec.ts",
            "docs/campaign-outcomes.md",
            "docs/load-qualification.md",
            "docs/modern-model-accounting.md",
            "docs/native-modern-text.md",
            "docs/gateway-budgets.md",
            "docs/retrieval-optimization.md",
            "docs/context-compression.md",
            "src/cutback/core/compression.py",
            "src/cutback/server/compression_api.py",
            "examples/context-compression/README.md",
            "examples/context-compression/prepare_dataset.py",
            "examples/context-compression/prepare_request.py",
            "tests/unit/test_context_compression.py",
            "tests/unit/test_task_aware_splits.py",
            "tests/integration/test_task_aware_split_workflow.py",
            "tests/integration/test_compression_workflow.py",
            "tests/end_to_end/test_compression_pipeline.py",
            "web/tests/compression.spec.ts",
            "examples/retrieval-optimization/README.md",
            "examples/retrieval-optimization/check_application.py",
            "examples/retrieval-optimization/corpus.json",
            "src/cutback/core/retrieval.py",
            "tests/end_to_end/test_retrieval_pipeline.py",
            "examples/gateway/README.md",
            "examples/gateway/sample.py",
            "src/cutback/sample_app/gateway.py",
            "src/cutback/server/migrations/versions/0019_application_gateway.py",
            "src/cutback/server/migrations/versions/0020_gateway_streaming.py",
            "src/cutback/gateway/streaming.py",
            "tests/unit/test_gateway_stream_wire.py",
            "tests/integration/test_gateway_streaming.py",
            "tests/integration/test_gateway_stream_controls.py",
            "tests/unit/test_profile_bindings.py",
            "tests/unit/test_namespace_model_profiles.py",
            "tests/unit/test_source_client_settings.py",
            "src/cutback/evals/input_groups.py",
            "tests/unit/test_effective_input_groups.py",
            "tests/integration/test_native_input_lineage.py",
            "docs/source-request-safety.md",
            "examples/modern-pricing/sample.py",
            "examples/load-qualification/profile.json",
            "examples/load-qualification/compare.py",
            "src/cutback/server/migrations/versions/0016_effective_rate_lookup_index.py",
            "examples/improvement-campaign/README.md",
            "examples/improvement-campaign/sample.py",
            "src/cutback/server/migrations/versions/0015_budgeted_improvement_campaigns.py",
            "examples/task-economics/README.md",
            "examples/task-economics/sample.py",
            "src/cutback/server/migrations/versions/0014_application_task_outcomes.py",
            "docs/episode-semantic-judging.md",
            "tests/integration/test_semantic_episodes.py",
            "src/cutback/core/episode_judging.py",
            "src/cutback/server/migrations/versions/0017_episode_semantic_judge_budget.py",
            "examples/agent-evaluation/sample.py",
            "src/cutback/server/migrations/versions/0013_execution_episode_and_per_call_ledger.py",
            "examples/online-quality/sample.py",
            "src/cutback/server/migrations/versions/0011_online_quality.py",
            "examples/prompt-releases/sample.py",
            "examples/application-checks/commands.json",
            "src/cutback/server/migrations/versions/0010_prompts.py",
            "examples/review-workflow/README.md",
            "examples/review-workflow/generate_promptfoo_fixture.py",
            "tests/fixtures/imports/promptfoo-0.125.1-v3.json",
            "src/cutback/server/migrations/versions/0008_dataset_lineage.py",
            "src/cutback/server/migrations/versions/0009_reviews.py",
            "src/cutback/sample_app/app.py",
            "docs/reactive-users.md",
            "docs/generated-scenarios.md",
            "examples/reactive-user/README.md",
            "examples/reactive-user/app.py",
            "src/cutback/core/user_simulation.py",
            "src/cutback/evals/user_simulation.py",
            "src/cutback/server/migrations/versions/0029_reactive_user_call_budget.py",
            "src/cutback/server/migrations/versions/0030_observed_response_models.py",
            "src/cutback/core/model_identity.py",
            "src/cutback/core/native_fixture.py",
            "src/cutback/server/native_fixture.py",
            "docs/native-fixtures.md",
            "src/cutback/native_cache.py",
            "src/cutback/core/exact_cache.py",
            "src/cutback/cache_changes.py",
            "src/cutback/server/migrations/versions/0031_exact_cache_call_evidence.py",
            "src/cutback/evals/exact_cache.py",
            "src/cutback/server/native_cache.py",
            "docs/native-response-cache.md",
            "examples/native-response-cache/README.md",
            "examples/native-response-cache/app.py",
            "tests/unit/test_native_cache.py",
            "tests/unit/test_native_cache_protocol.py",
            "tests/integration/test_native_cache_workflow.py",
            "tests/integration/test_exact_cache_experiment.py",
            "tests/integration/test_agent_exact_cache.py",
            "tests/integration/test_cache_call_evidence.py",
            "tests/integration/test_native_cache_runtime.py",
            "tests/unit/test_cache_source_patch.py",
            "tests/end_to_end/test_exact_cache_pipeline.py",
            "tests/support_exact_cache.py",
            "src/cutback/evals/artifact_retention.py",
            "tests/support_artifact_retention.py",
            "tests/unit/test_artifact_retention.py",
            "tests/unit/test_s3_retention.py",
            "tests/integration/test_encrypted_object_retention.py",
            "tests/integration/test_capture_commit_recovery.py",
            "docs/encrypted-retention.md",
            "tests/support_http.py",
            "web/src/exact-cache.tsx",
            "web/tests/exact-cache.spec.ts",
            "tests/unit/test_native_fixture_contract.py",
            "tests/integration/test_native_fixture_source.py",
            "tests/end_to_end/test_native_fixture_pipeline.py",
            "web/tests/native-fixture.spec.ts",
            "tests/unit/test_model_identity.py",
            "tests/integration/test_response_model_evidence.py",
            "src/cutback/agent_strategy.py",
            "tests/unit/test_agent_strategy.py",
            "src/cutback/agent_preparation.py",
            "src/cutback/agent_bootstrap.py",
            "src/cutback/core/bootstrap.py",
            "src/cutback/server/bootstrap_api.py",
            "docs/bootstrap-recovery.md",
            "tests/unit/test_bootstrap_intent.py",
            "tests/integration/test_bootstrap_recovery.py",
            "tests/end_to_end/test_bootstrap_recovery_pipeline.py",
            "src/cutback/core/preparation.py",
            "tests/unit/test_agent_preparation.py",
            "tests/integration/test_preparation_recovery.py",
            "tests/integration/test_agent_preparation_recovery.py",
            "tests/end_to_end/test_preparation_recovery_pipeline.py",
            "tests/support_reactive_users.py",
            "tests/integration/test_reactive_users.py",
            "tests/end_to_end/test_reactive_users_pipeline.py",
        ):
            if not any(name.endswith("/" + required) for name in source_names):
                raise ValueError("source distribution missing reproducibility file: " + required)
        if any(member.issym() or member.islnk() for member in source.getmembers()):
            raise ValueError("release archive must not contain symlinks")
    if any(
        marker.lower() in name.lower().replace("\\", "/") for name in names for marker in banned
    ):
        raise ValueError("internal or secret file included in archive")
    count = check_application_payload(wheels[0], sources[0], Path(__file__).resolve().parents[1])
    print(
        "Release identity, pre-alpha metadata, licenses, archive exclusions "
        f"and exact application payload ({count} files) passed."
    )


if __name__ == "__main__":
    import sys

    check(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist"))
