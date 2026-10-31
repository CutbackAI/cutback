"""Owned observed-source cache trial. Native transports are closed synthetic fixtures."""

import base64
import importlib.util
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4


def seed_workload_portfolio(app, control, project_id, workspace, ingest_token):
    from cutback.demo import require_demo_git

    require_demo_git()

    import httpx
    from openai import OpenAI

    import cutback
    from cutback.agent import advance, analyze, confirm, gate, load_session, prepare, proposal
    from cutback.agent import workspace as agent_workspace
    from cutback.agent_cache_workload import assess
    from cutback.agent_control import Control
    from cutback.application_check_discovery import discover
    from cutback.application_checks import ApplicationCheckContract, git, run_checks, verify
    from cutback.core.exact_cache import ExactCacheExecution
    from cutback.core.workload_portfolio import WorkloadPortfolioBinding
    from cutback.core.workspace import private_directory, write_private
    from cutback.evals.executors import OpenAIExecutor
    from cutback.instrumentation import workflow
    from cutback.instrumentation.attribution import build_source_map
    from cutback.scanner import scan
    from cutback.server.native_profile_service import run_native_profile_one
    from cutback.server.worker import run_one

    authorization = control.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise ValueError("SYNTHETIC_WORKLOAD_SCOPED_CONTROL_REQUIRED")
    endpoint = str(control.base_url).rstrip("/")
    adapter = Control(endpoint, authorization[7:], client=control)
    base = f"/v1/projects/{project_id}"
    root = private_directory(workspace, "workload-reuse-" + uuid4().hex)
    shutil.copyfile(Path(__file__).parent / "sample_app/workload_reuse.py", root / "app.py")
    original = (root / "app.py").read_bytes()
    cases = [
        {
            "id": f"reuse-{chr(65 + group)}-{repeat}",
            "input": f"Owned reusable billing family {chr(65 + group)}",
            "gold": "billing",
            "group": f"reuse-family-{chr(65 + group)}",
        }
        for group in range(20)
        for repeat in range(10)
    ]
    (root / "cases.jsonl").write_text(
        "".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8"
    )
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    (root / "check_application.py").write_text(
        "import os\nfrom app import classify_reuse, client\n\ndef main():\n"
        '    assert "CUTBACK_NATIVE_CACHE_SCOPE_FILE" not in os.environ\n'
        '    assert "OPENAI_API_KEY" not in os.environ\n'
        '    result = classify_reuse("Owned reusable billing behavior check")\n'
        '    assert result.output_text == "billing"\n'
        '    assert result.model == "gpt-4.1-mini"\n'
        "    client.close()\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    git(root, "init")
    git(root, "config", "core.autocrlf", "false")
    git(root, "add", ".")

    def commit(directory, message):
        git(
            directory,
            "-c",
            "user.name=Synthetic Cutback Sample",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            message,
        )

    commit(root, "Independent synthetic repeat-workload application")
    analyzed = analyze(root)
    if len(analyzed["profiles"]) != 1:
        raise ValueError("SYNTHETIC_WORKLOAD_SOURCE_PROFILE_REQUIRED")
    workflow_id = analyzed["profiles"][0]["binding"]["workflow_id"]
    source_map = root / ".cutback/source-map.json"
    source_map.write_text(json.dumps(build_source_map(root, scan(root))), encoding="utf-8")
    contract = {
        "freshness_ref": "owned-immutable-reuse-fixture",
        "ttl_seconds": 300,
        "content_retention_authorized": True,
        "immutable_or_safely_invalidated": True,
        "operation_cost_upper_usd": "0.000000000001",
        "operation_cost_basis_ref": "owned-synthetic-operation-bound",
    }
    policy = adapter(
        "POST",
        base + "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-synthetic-reuse-observation",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow_id],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 16,
            "max_payload_bytes": 64000,
            "retention_days": 1,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "native_request_profile": {
                "freshness_ref": contract["freshness_ref"],
                "prospective_ttl_seconds": 300,
                "fingerprint_capture_authorized": True,
                "immutable_or_safely_invalidated": True,
            },
        },
    )
    module_spec = importlib.util.spec_from_file_location("owned_reuse_application", root / "app.py")
    if module_spec is None or module_spec.loader is None:
        raise ValueError("SYNTHETIC_WORKLOAD_FIXTURE_LOAD_FAILED")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    module.client.close()
    calls = []

    def provider(request):
        calls.append(1)
        return module.fixture_response(request)

    with patch.dict(
        os.environ,
        {
            "CUTBACK_PROJECT_TOKEN": ingest_token,
            "CUTBACK_NATIVE_REQUEST_PROFILE_HMAC_KEY": base64.b64encode(os.urandom(32)).decode(),
            "CUTBACK_ENABLED": "true",
            "CUTBACK_CACHE_ENABLED": "true",
            "CUTBACK_EXPERIMENT_OPENAI_KEY": "owned-synthetic-only",
        },
    ):
        start = datetime.now(UTC)
        observer = cutback.init(
            service_name="workload-reuse-sample",
            environment="staging",
            enabled=True,
            project_id=project_id,
            endpoint=endpoint + "/v1/ingest/events",
            providers=("openai",),
            capture_content=True,
            capture_policy_id=policy["id"],
            source_map=str(source_map),
            timeout_seconds=2,
        )
        try:
            with (
                OpenAI(
                    api_key="owned-synthetic-only",
                    max_retries=0,
                    http_client=httpx.Client(
                        transport=httpx.MockTransport(provider), trust_env=False
                    ),
                ) as native,
                workflow(workflow_id),
            ):
                module.__dict__["client"] = native
                for index in range(8):
                    text = "Owned observed reuse billing " + ("repeat" if index < 4 else str(index))
                    if module.classify_reuse(text).output_text != "billing":
                        raise ValueError("SYNTHETIC_WORKLOAD_NATIVE_RESPONSE_MISMATCH")
            if not observer.flush(5) or observer.health().get("native_profile_exported") != 8:
                raise ValueError("SYNTHETIC_WORKLOAD_CAPTURE_INCOMPLETE")
        finally:
            observer.close()
        analysis = adapter(
            "POST",
            base + "/native-request-profiles/analyses",
            {
                "request_id": str(uuid4()),
                "policy_id": policy["id"],
                "environment": "staging",
                "workflow": workflow_id,
                "purpose": "owned-synthetic-reuse-assessment",
                "window_start": (start - timedelta(seconds=1)).isoformat(),
                "window_end": datetime.now(UTC).isoformat(),
            },
        )
        result = run_native_profile_one(
            app.state.sessions, app.state.artifact_store, only_analysis_id=analysis["id"]
        )
        if result["status"] != "COMPLETED":
            raise ValueError("SYNTHETIC_WORKLOAD_ANALYSIS_FAILED")
        assessed = assess(root, adapter, project_id, cache_execution=contract)
        if assessed["decision"] != "prepare_separate_cache_trial":
            raise ValueError("SYNTHETIC_WORKLOAD_NO_MEASURED_CACHE_HYPOTHESIS")
        binding = WorkloadPortfolioBinding(
            assessment=assessed["selected_assessment"],
            cache_execution=ExactCacheExecution.model_validate(contract),
        )
        planned = prepare(
            root,
            adapter,
            project_id,
            files=["cases.jsonl"],
            rights_basis="synthetic",
            rights_ref="owned-synthetic-reuse-trial",
            max_usd="5",
            max_requests=500,
            automatic=True,
            fixture_transport=True,
            exact_cache_contract=contract,
            workload_portfolio_binding=binding.model_dump(mode="json"),
            minimum=1,
            min_test_samples=3,
            independent_labels=True,
            representative=False,
        )
        if not planned["plan"].get("native_fixture"):
            raise ValueError("SYNTHETIC_WORKLOAD_FROZEN_FIXTURE_REQUIRED")
        executor = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(provider), trust_env=False, follow_redirects=False
            )
        )
        executor.synthetic_transport = True

        def execute(state):
            queued = advance(
                root,
                adapter,
                plan_hash=state["plan"]["plan_hash"],
                approval_ref="owned-synthetic-reuse-fixture-only",
            )
            status = run_one(
                app.state.sessions,
                app.state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=UUID(queued["run_id"]),
            )
            if status["status"] != "COMPLETED":
                raise ValueError("SYNTHETIC_WORKLOAD_TRIAL_FAILED")
            return advance(root, adapter)

        try:
            validated = execute(planned)
            choices = [
                row
                for row in validated["decisions"]
                if row.get("quality_gate_passed") and row.get("cheaper_on_sample")
            ]
            choices.sort(key=lambda row: -Decimal(row["sample_modeled_cost_difference_usd"]))
            if not choices or not choices[0]["candidate"].get("exact_cache"):
                raise ValueError("SYNTHETIC_WORKLOAD_EXPECTED_CACHE_WINNER_REQUIRED")
            selected = choices[0]["candidate"]["key"]
            frozen = confirm(
                root,
                adapter,
                candidate=selected,
                max_usd="5",
                max_requests=200,
                purpose="owned-synthetic-reuse-confirmation",
            )
            completed = execute(frozen)
            if not gate(root, adapter, candidate=selected, sample_only=True)["passed"]:
                raise ValueError("SYNTHETIC_WORKLOAD_SAMPLE_CONFIRMATION_FAILED")
            if gate(root, adapter, candidate=selected)["passed"]:
                raise ValueError("SYNTHETIC_WORKLOAD_CANNOT_VERIFY_REAL_QUALITY")
            artifact = proposal(root, adapter, candidate=selected, purpose="owned-synthetic-review")
        finally:
            executor.close()
    proposed = root.parent / (root.name + "-proposed")
    shutil.copytree(root, proposed, ignore=shutil.ignore_patterns(".cutback", "__pycache__"))
    change = root.parent / (root.name + ".patch")
    change.write_text(artifact["diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(change))
    git(proposed, "add", ".")
    commit(proposed, "Review independent synthetic cache proposal")
    checks = ApplicationCheckContract.model_validate(discover(root, proposed)["contract"])
    receipt = run_checks(checks, root, proposed, execute_local=True)
    if not receipt["all_passed"]:
        raise ValueError("SYNTHETIC_WORKLOAD_APPLICATION_CHECK_FAILED")
    verified = verify(checks, receipt, root, proposed)
    change.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
    git(proposed, "apply", str(change))
    if (root / "app.py").read_bytes() != original or (proposed / "app.py").read_bytes() != original:
        raise ValueError("SYNTHETIC_WORKLOAD_ROLLBACK_MISMATCH")
    _, location = agent_workspace(root)
    write_private(location / "cache-proposal.json", artifact)
    return {
        "dataset_id": planned["dataset_id"],
        "analysis_id": analysis["id"],
        "validation_run_id": validated["run_id"],
        "confirmation_run_id": completed["run_id"],
        "selected_candidate": selected,
        "observed_native_calls": 8,
        "modeled_potential_hits": binding.assessment.modeled_potential_hits,
        "native_sdk_calls": len(calls),
        "paid_provider_calls": 0,
        "source_directory": str(root),
        "source_change": artifact,
        "application_checks": {
            "contract": checks.model_dump(mode="json"),
            "receipt": receipt,
            "verified": verified,
        },
        "rollback_verified": True,
        "runtime_cache_activated": False,
        "real_model_quality_verified": False,
        "real_customer_savings_verified": False,
        "session_id": load_session(root)["id"],
    }
