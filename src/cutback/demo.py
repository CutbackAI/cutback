"""Real local PostgreSQL/HTTP/SDK/worker demo; every provider request is intercepted."""

import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4


def require_demo_git():
    """Source-bound sample workspaces need Git before any resources are created."""
    import shutil

    if shutil.which("git") is None:
        raise ValueError("DEMO_GIT_REQUIRED")


def sample_cases():
    cases = []
    for i in range(240):
        category = ("billing", "access", "bug")[i % 3]
        priority = "urgent" if i % 4 == 0 else "normal"
        reference = chr(65 + i // 26) + chr(65 + i % 26)
        cases.append(
            {
                "id": f"ticket-{reference}",
                "input": (
                    f"Synthetic ticket {reference}. Category: {category}. Priority: {priority}. "
                )
                + {
                    "billing": "Please explain this duplicate invoice.",
                    "access": "I cannot sign in to my account.",
                    "bug": "The export button produces an error.",
                }[category],
                "gold": {"category": category, "priority": priority},
                "stratum": category,
            }
        )
    return cases


def fixture_response(request):
    """Own the complete provider transport. No socket to an inference provider exists."""
    import httpx

    if request.url.host != "api.openai.com" or request.url.path != "/v1/responses":
        raise ValueError("DEMO_PROVIDER_REQUEST_REFUSED")
    body = json.loads(request.content)
    text = body["input"]
    category = next(
        (item for item in ("billing", "access", "bug") if f"Category: {item}." in text), "bug"
    )
    priority = "urgent" if "Priority: urgent." in text else "normal"
    if "unsafe" in (body.get("instructions") or ""):
        priority = "normal"
    return httpx.Response(
        200,
        json={
            "id": "resp_demo_" + uuid4().hex,
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": body["model"],
            "output": [
                {
                    "id": "msg_demo",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps({"category": category, "priority": priority}),
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {
                "input_tokens": 120,
                "output_tokens": 16,
                "total_tokens": 136,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        },
    )


def seed_support_desk(
    app,
    endpoint,
    admin,
    workspace: Path,
    *,
    ticket_router=False,
    provider_batch=False,
    semantic_cache=False,
    tool_image_id=None,
    security_suite=False,
    runtime_guardrails=False,
    guarded_tools=False,
    guarded_security=False,
    joint_model_context=False,
    reviewed_criteria=False,
    semantic_rubrics=False,
    learned_routing=False,
    source_routing=False,
):
    """Use actual scoped HTTP APIs and the durable worker, retaining the sample project."""
    require_demo_git()

    import hashlib
    import runpy
    import shutil

    import httpx

    import cutback
    from cutback.changes import model_patch
    from cutback.evals.executors import OpenAIExecutor
    from cutback.instrumentation.attribution import build_source_map
    from cutback.scanner import scan
    from cutback.server.worker import run_one

    sample = workspace / "support-desk"
    sample.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).parent / "sample_app/app.py", sample / "app.py")
    inventory = scan(sample)
    map_path = sample / ".cutback/source-map.json"
    map_path.parent.mkdir(exist_ok=True)
    map_path.write_text(json.dumps(build_source_map(sample, inventory)), encoding="utf-8")
    application = runpy.run_path(str((sample / "app.py").resolve()))
    site = next(
        item for item in inventory["call_sites"] if item["sdk_surface"] == "responses.create"
    )
    from cutback.core.request_profiles import prepare_profile

    profile = prepare_profile(sample, site, commit=inventory["project"].get("commit"))
    workflow = profile.binding.workflow_id
    inventory["request_profiles"] = [profile.binding.model_dump(mode="json")]
    with httpx.Client(
        base_url=endpoint, headers={"Authorization": "Bearer " + admin}, trust_env=False, timeout=30
    ) as control:

        def call(path, value):
            response = control.post(path, json=value)
            response.raise_for_status()
            return response.json()

        project = call("/v1/projects", {"slug": "support-desk-demo-" + uuid4().hex[:10]})
        base = "/v1/projects/" + project["id"]
        ingest = call(base + "/keys", {"role": "ingest"})["token"]
        demo_key = call(base + "/keys", {"role": "project_admin", "expires_in_days": 1})["token"]
        call(base + "/scans", inventory)
        policy = call(
            base + "/capture-policies",
            {
                "enabled": True,
                "approval_ref": "owned-synthetic-support-desk",
                "rights_basis": "synthetic",
                "environments": ["staging"],
                "workflows": [workflow],
                "allowed_providers": ["openai"],
                "sample_rate": 1,
                "max_samples": 5000,
                "max_payload_bytes": 64000,
                "retention_days": 1,
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        from cutback.demo_traces import seed_execution_sample

        execution_samples = seed_execution_sample(
            control, project["id"], ingest, policy["id"], workflow
        )
        cases = sample_cases()
        dataset = call(
            base + "/datasets",
            {
                "workflow": workflow,
                "policy_id": policy["id"],
                "source_type": "synthetic_fixture",
                "provenance_ref": "support-desk-owned-synthetic-v1",
                "independent_labels": True,
                "representative": False,
                "cases": cases,
            },
        )
        original = {
            key: os.environ.get(key)
            for key in ("CUTBACK_PROJECT_TOKEN", "CUTBACK_ENABLED", "CUTBACK_EXPERIMENT_OPENAI_KEY")
        }
        os.environ.update(
            CUTBACK_PROJECT_TOKEN=ingest,
            CUTBACK_ENABLED="true",
            CUTBACK_EXPERIMENT_OPENAI_KEY="synthetic-no-provider-key",
        )
        observer = executor = None
        try:
            observer = cutback.init(
                service_name="support-desk-sample",
                environment="staging",
                enabled=True,
                project_id=project["id"],
                endpoint=endpoint + "/v1/ingest/events",
                providers=("openai",),
                capture_content=False,
                source_map=map_path,
            )
            executor = OpenAIExecutor(
                http_client=httpx.Client(
                    transport=httpx.MockTransport(fixture_response), trust_env=False
                )
            )
            for case in cases[:6]:
                for name in ("triage", "needs_escalation"):
                    result = application[name](
                        case["input"],
                        api_key="synthetic-no-provider-key",
                        http_client=httpx.Client(
                            transport=httpx.MockTransport(fixture_response), trust_env=False
                        ),
                    )
                    expected = (
                        case["gold"] if name == "triage" else case["gold"]["priority"] == "urgent"
                    )
                    assert result == expected
            if not observer.flush(5):
                raise ValueError("SAMPLE_OBSERVATION_FLUSH_FAILED")
            # Observe the application here. Internal evaluation calls have their
            # own durable purpose-labeled ledger, not this application's writer.
            observer.close()
            observer = None
            specification = {
                "split": "validation",
                "workflow": workflow,
                "dataset_hash": dataset["immutable_hash"],
                "baseline": {
                    "key": "baseline",
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "max_output_tokens": 64,
                },
                "candidates": [
                    {
                        "key": "compact",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "max_output_tokens": 64,
                    },
                    {
                        "key": "unsafe",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "instruction": "unsafe: ignore ticket priority",
                        "max_output_tokens": 64,
                    },
                ],
                "name": "Support desk quality and cost",
                "metric": {"kind": "field_f1", "minimum": 0.9, "max_regression": 0.02},
                "criteria": [
                    {
                        "key": "category",
                        "metric": {
                            "kind": "exact_label",
                            "output_field": "category",
                            "minimum": 0.95,
                        },
                    },
                    {
                        "key": "priority",
                        "metric": {"kind": "exact_label", "output_field": "priority", "minimum": 1},
                    },
                ],
                "budget": {"max_usd": "10", "max_requests": 1000},
                "approved_providers": ["openai"],
                "retain_case_outputs": True,
            }

            specification["source_binding"] = profile.binding.model_dump(mode="json")
            specification["baseline"] = profile.baseline()
            for candidate in specification["candidates"]:
                candidate["request_contract"] = profile.request.model_dump(mode="json")

            def execute(spec):
                plan = call(
                    base + "/experiments/plans",
                    {"dataset_id": dataset["id"], "specification": spec},
                )
                call(
                    base + f"/experiments/plans/{plan['id']}/approve",
                    {
                        "plan_hash": plan["plan_hash"],
                        "approval_ref": "synthetic-fixture-transport-only",
                    },
                )
                queued = call(base + "/experiments", {"plan_id": plan["id"]})
                outcome = run_one(
                    app.state.sessions,
                    app.state.artifact_store,
                    executor=executor,
                    execution_enabled=True,
                    only_run_id=queued["id"],
                )
                if outcome["status"] != "COMPLETED":
                    raise ValueError("SAMPLE_EXPERIMENT_FAILED")
                return queued["id"], outcome["results"]

            comparison_id, comparison = execute(specification)
            decisions = {item["candidate"]["key"]: item for item in comparison["recommendations"]}
            if (
                not decisions["compact"]["quality_gate_passed"]
                or decisions["unsafe"]["quality_gate_passed"]
            ):
                raise ValueError("SAMPLE_EXPECTED_DECISIONS_FAILED")
            stages = []
            previous = None
            for stage, size in (("smoke", 3), ("pilot", 10), ("full", None)):
                staged = {
                    **specification,
                    "candidates": specification["candidates"][:1],
                    "staged": True,
                    "stage": stage,
                    "split": "test" if stage == "full" else "validation",
                    "sample_limit": size,
                    "predecessor_run_id": previous,
                }
                previous, result = execute(staged)
                stages.append({"stage": stage, "run_id": previous, "cases": result["sample_n"]})
            technical_id, _ = execute(
                {
                    **specification,
                    "candidates": specification["candidates"][:1],
                    "name": "Technical response check only",
                    "metric": {"kind": "nonempty", "minimum": 1},
                    "criteria": [],
                    "retain_case_outputs": False,
                }
            )
            recommendation = next(
                item
                for item in control.get(base + "/recommendations").json()
                if item["run_id"] == stages[-1]["run_id"]
                and item["evidence"]["candidate"]["key"] == "compact"
            )
            call(
                base + f"/recommendations/{recommendation['id']}/review",
                {
                    "decision": "accepted_for_staging",
                    "reason": "owned-synthetic-fixture-review-only",
                },
            )
            patch = model_patch(
                sample,
                inventory,
                site["call_site_id"],
                {**recommendation, "status": "accepted_for_staging"},
                expected_source_sha256=hashlib.sha256((sample / "app.py").read_bytes()).hexdigest(),
            )
            call(
                base + f"/recommendations/{recommendation['id']}/patches",
                {"approval_ref": "owned-synthetic-diff-review", "artifact": patch},
            )
            from cutback.demo_agents import seed_agent_episodes

            agent_sample = seed_agent_episodes(app, control, project["id"])
            from cutback.demo_campaigns import seed_campaign
            from cutback.demo_economics import seed_task_economics

            task_sample = seed_task_economics(control, endpoint, project["id"], ingest)
            campaign_sample = seed_campaign(app, control, project["id"], ingest, workspace)
            from cutback.demo_retrieval import seed_retrieval

            retrieval_sample = seed_retrieval(app, control, project["id"], workspace)
            from cutback.demo_compression import seed_compression

            # These modes share a corpus. Execute one comparison so its protected
            # confirmation groups are never reused by a second demonstration.
            compression_sample = seed_compression(
                app, control, project["id"], workspace, joint=joint_model_context
            )
            joint_sample = compression_sample if joint_model_context else None
            criteria_sample = None
            if reviewed_criteria:
                from cutback.demo_criteria import seed_criteria

                criteria_sample = seed_criteria(app, control, project["id"], workspace=workspace)
            rubric_sample = None
            if semantic_rubrics:
                from cutback.demo_rubrics import seed_rubrics

                rubric_sample = seed_rubrics(app, control, project["id"])
            routing_sample = None
            if source_routing:
                from cutback.demo_source_routing import seed_source_routing

                routing_sample = seed_source_routing(
                    app, control, project["id"], workspace, demo=True
                )
            elif learned_routing:
                from cutback.demo_routing import seed_routing

                routing_sample = seed_routing(app, control, project["id"], demo=True)
            ticket_sample = None
            if ticket_router:
                from cutback.demo_campaign_outcomes import run_sample

                ticket_sample = run_sample(
                    app,
                    control,
                    endpoint,
                    admin,
                    project["id"],
                    ingest,
                    workspace,
                    ticket_router=True,
                )
            batch_sample = None
            if provider_batch:
                from cutback.demo_provider_batches import seed_provider_batch

                batch_sample = seed_provider_batch(app, control, project["id"])
            semantic_sample = None
            if semantic_cache:
                from cutback.demo_semantic_cache import seed_semantic_cache

                semantic_sample = seed_semantic_cache(app, control, project["id"])
            isolated_sample = None
            security_sample = None
            runtime_sample = None
            if runtime_guardrails:
                from cutback.demo_runtime_guardrails import seed_runtime_guardrails

                runtime_sample = seed_runtime_guardrails(app, control, project["id"])
            if security_suite or guarded_security:
                from cutback.demo_security_suites import seed_security_suite

                security_sample = seed_security_suite(
                    app, control, project["id"], tool_image_id, guarded=guarded_security
                )
            if tool_image_id:
                from cutback.demo_isolated_tools import seed_isolated_tools

                isolated_sample = seed_isolated_tools(
                    app,
                    control,
                    project["id"],
                    tool_image_id,
                    guarded=guarded_tools,
                    deny_tool_output=guarded_tools,
                )
            manifest = {
                "available": True,
                "title": "Support-desk triage",
                "disclosure": (
                    "SYNTHETIC provider replies. Modeled fixture costs are not "
                    "measured provider performance or customer savings."
                ),
                "project_id": project["id"],
                "project_slug": project["slug"],
                "comparison_run_id": comparison_id,
                "technical_run_id": technical_id,
                "stages": stages,
                "sample_cases": len(cases),
                "sample_source": "cutback/sample_app/app.py",
                "source_diff": patch,
                "paid_provider_calls": 0,
                **({"reviewed_criteria_sample": criteria_sample} if criteria_sample else {}),
                **({"semantic_rubrics_sample": rubric_sample} if rubric_sample else {}),
                **({"learned_routing_sample": routing_sample} if routing_sample else {}),
                **({"isolated_tool_sample": isolated_sample} if isolated_sample else {}),
                **({"security_suite_sample": security_sample} if security_sample else {}),
                **({"runtime_guardrail_sample": runtime_sample} if runtime_sample else {}),
                **({"semantic_cache_sample": semantic_sample} if semantic_sample else {}),
                **(
                    {
                        "joint_model_context_sample": {
                            key: joint_sample[key]
                            for key in (
                                "dataset_id",
                                "validation_run_id",
                                "confirmation_run_id",
                                "native_sdk_calls",
                                "paid_provider_calls",
                            )
                        }
                    }
                    if joint_sample
                    else {}
                ),
                **(
                    {
                        "provider_batch_sample": {
                            key: batch_sample[key]
                            for key in (
                                "batch_id",
                                "status",
                                "reference_labels",
                                "declared_intent_families",
                                "vendor_files_deleted",
                                "paid_provider_calls",
                            )
                        }
                    }
                    if batch_sample
                    else {}
                ),
                **(
                    {
                        "ticket_campaign_sample": {
                            "campaign_id": ticket_sample["campaign_id"],
                            "validation_run_id": next(
                                r["run_id"]
                                for r in ticket_sample["report"]["runs"]
                                if r["phase"] == "validation"
                            ),
                            "native_calls": ticket_sample["native_calls"],
                            "reference_labels": 3,
                            "declared_intent_families": 60,
                            "paid_provider_calls": 0,
                        }
                    }
                    if ticket_sample
                    else {}
                ),
                "execution_samples": execution_samples,
                "agent_episode_run_id": agent_sample["run_id"],
                "task_economics_sample": task_sample,
                "improvement_campaign_sample": campaign_sample,
                "retrieval_sample": {
                    key: retrieval_sample[key]
                    for key in (
                        "dataset_id",
                        "validation_run_id",
                        "confirmation_run_id",
                        "native_sdk_calls",
                        "source_change",
                        "paid_provider_calls",
                    )
                },
                "compression_sample": {
                    key: compression_sample[key]
                    for key in (
                        "dataset_id",
                        "validation_run_id",
                        "confirmation_run_id",
                        "native_sdk_calls",
                        "source_change",
                        "paid_provider_calls",
                    )
                },
            }
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "sample-report.json").write_text(
                json.dumps({**manifest, "comparison": comparison}, indent=2), encoding="utf-8"
            )
            recipe = {"dataset_id": dataset["id"], "specification": specification}
            import yaml

            (workspace / "experiment.yaml").write_text(
                yaml.safe_dump(recipe, sort_keys=False), encoding="utf-8"
            )
            install_demo_routes(app, manifest, demo_key, endpoint)
            return manifest
        finally:
            if observer:
                observer.close()
            if executor:
                executor.close()
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def install_demo_routes(app, manifest, token, endpoint):
    from fastapi import HTTPException, Request

    @app.get("/v1/demo")
    def info():
        return manifest

    @app.post("/v1/demo/connect")
    def connect(request: Request):
        if (
            not request.client
            or request.client.host not in {"127.0.0.1", "::1"}
            or request.headers.get("origin") not in {None, endpoint}
            or request.headers.get("x-cutback-demo") != "connect"
        ):
            raise HTTPException(403, "DEMO_LOOPBACK_SAME_ORIGIN_ONLY")
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"token": token, "project_id": manifest["project_id"]},
            headers={"Cache-Control": "no-store"},
        )


def launch_demo(
    port=8766,
    open_browser=True,
    *,
    ticket_router=False,
    provider_batch=False,
    semantic_cache=False,
    tool_image_id=None,
    security_suite=False,
    runtime_guardrails=False,
    guarded_tools=False,
    guarded_security=False,
    joint_model_context=False,
    reviewed_criteria=False,
    semantic_rubrics=False,
    learned_routing=False,
    source_routing=False,
):
    """Own one temporary Docker database. Never connect to production or reuse app keys."""
    require_demo_git()
    if learned_routing and source_routing:
        raise ValueError("CHOOSE_LEARNED_ROUTING_OR_SOURCE_ROUTING_DEMO")
    if security_suite and tool_image_id is None:
        raise ValueError("SECURITY_DEMO_REQUIRES_EXPLICIT_LOCAL_TOOL_IMAGE_ID")
    if guarded_tools and tool_image_id is None:
        raise ValueError("GUARDED_TOOL_DEMO_REQUIRES_EXPLICIT_LOCAL_TOOL_IMAGE_ID")
    if guarded_security and tool_image_id is None:
        raise ValueError("GUARDED_SECURITY_DEMO_REQUIRES_EXPLICIT_LOCAL_TOOL_IMAGE_ID")
    if tool_image_id is not None and not re.fullmatch(r"sha256:[a-f0-9]{64}", tool_image_id):
        raise ValueError("DEMO_EXACT_LOCAL_TOOL_IMAGE_ID_REQUIRED")

    import psycopg
    import uvicorn

    from cutback.evals.artifacts import EncryptedFileStore
    from cutback.server.api import create_app
    from cutback.server.migrate import migrate

    # Fail before provisioning anything when another local app owns this port.
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    except OSError:
        raise ValueError("DEMO_PORT_UNAVAILABLE") from None
    workspace = Path.cwd() / ".cutback" / "support-desk-demo"
    workspace.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cutback-demo-") as temporary:
        secret = Path(temporary) / "db_password"
        secret.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        secret.chmod(0o600)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        db_port = sock.getsockname()[1]
        sock.close()
        container = "cutback-demo-" + uuid4().hex[:12]
        command = [
            "docker",
            "run",
            "--rm",
            "--detach",
            "--name",
            container,
            "--publish",
            f"127.0.0.1:{db_port}:5432",
            "--mount",
            f"type=bind,source={secret.resolve()},target=/run/secrets/db_password,readonly",
            "--env",
            "POSTGRES_PASSWORD_FILE=/run/secrets/db_password",
            "--env",
            "POSTGRES_USER=cutback",
            "--env",
            "POSTGRES_DB=cutback",
            (
                "postgres:17-"
                "alpine@sha256:eee6c6e1b0894fa4fe0075eb054af9fd1cf39a35321c2d4110"
                "626cfe506411fb"
            ),
        ]
        thread = None
        server = None
        routing_context = None
        old_url = os.environ.get("CUTBACK_DATABASE_URL")
        old_payload = os.environ.get("CUTBACK_ALLOW_PAYLOADS")
        old_semantic = os.environ.get("CUTBACK_SEMANTIC_CACHE_ENABLED")
        try:
            result = subprocess.run(command, capture_output=True, timeout=180)
            if result.returncode:
                raise ValueError("DEMO_NEEDS_RUNNING_DOCKER_DESKTOP")
            from urllib.parse import quote

            password = secret.read_text().strip()
            database_url = (
                f"postgresql+psycopg://cutback:{quote(password, safe='')}"
                f"@127.0.0.1:{db_port}/cutback"
            )
            for attempt in range(120):
                try:
                    with psycopg.connect(
                        host="127.0.0.1",
                        port=db_port,
                        user="cutback",
                        password=password,
                        dbname="cutback",
                        connect_timeout=1,
                    ):
                        break
                except psycopg.OperationalError:
                    if attempt == 119:
                        raise ValueError("DEMO_DATABASE_NOT_READY") from None
                    time.sleep(0.5)
            os.environ.update(CUTBACK_DATABASE_URL=database_url, CUTBACK_ALLOW_PAYLOADS="true")
            if semantic_cache:
                os.environ["CUTBACK_SEMANTIC_CACHE_ENABLED"] = "true"
            migrate()
            admin = secrets.token_urlsafe(48)
            app = create_app(database_url=database_url, admin_token=admin, allow_insecure_http=True)
            app.state.artifact_store = EncryptedFileStore(
                Path(temporary) / "encrypted", os.urandom(32)
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    app, host="127.0.0.1", port=port, log_level="warning", access_log=False
                )
            )
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            for _ in range(100):
                if server.started:
                    break
                if not thread.is_alive():
                    raise ValueError("DEMO_PORT_UNAVAILABLE")
                time.sleep(0.05)
            endpoint = f"http://127.0.0.1:{port}"
            print(
                (
                    "Building the support-desk workspace through the real collector "
                    "and worker; all provider responses are synthetic."
                ),
                flush=True,
            )
            manifest = seed_support_desk(
                app,
                endpoint,
                admin,
                workspace,
                ticket_router=ticket_router,
                provider_batch=provider_batch,
                semantic_cache=semantic_cache,
                tool_image_id=tool_image_id,
                security_suite=security_suite,
                runtime_guardrails=runtime_guardrails,
                guarded_tools=guarded_tools,
                guarded_security=guarded_security,
                joint_model_context=joint_model_context,
                reviewed_criteria=reviewed_criteria,
                semantic_rubrics=semantic_rubrics,
                learned_routing=learned_routing,
                source_routing=source_routing,
            )
            if learned_routing or source_routing:
                from cutback.demo_routing import routing_worker

                routing_context = routing_worker(app, manifest["project_id"])
                routing_context.__enter__()
            print(
                f"Ready: {endpoint}/dashboard/\n"
                "Choose Explore sample. No API key is needed.\n"
                "Compact candidate passes sample gates; unsafe candidate fails priority.\n"
                "Evidence: .cutback/support-desk-demo/sample-report.json\n"
                "Press Ctrl+C to stop this sample and remove its temporary database.",
                flush=True,
            )
            if open_browser:
                import webbrowser

                webbrowser.open(endpoint + "/dashboard/")
            while thread.is_alive():
                thread.join(0.5)
            return manifest
        finally:
            routing_shutdown_failed = False
            if routing_context:
                try:
                    routing_context.__exit__(None, None, None)
                except RuntimeError:
                    routing_shutdown_failed = True
            if server:
                server.should_exit = True
            if thread:
                thread.join(timeout=5)
            try:
                subprocess.run(
                    ["docker", "stop", "--time", "3", container], capture_output=True, timeout=30
                )
            except (OSError, subprocess.TimeoutExpired):
                print(f"If Docker is unavailable, remove this sample container: {container}")
            for name, old in (
                ("CUTBACK_DATABASE_URL", old_url),
                ("CUTBACK_ALLOW_PAYLOADS", old_payload),
                ("CUTBACK_SEMANTIC_CACHE_ENABLED", old_semantic),
            ):
                if old is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = old
            if routing_shutdown_failed:
                raise RuntimeError("SYNTHETIC_ROUTING_WORKER_SHUTDOWN_INCOMPLETE")
