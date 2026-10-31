"""Independent exact-cache experiment fixture using real PostgreSQL and closed native transport."""

from uuid import UUID

import httpx
from support_agent import native_response
from support_evaluation import authorized_dataset, plan_for

from cutback.evals.executors import OpenAIExecutor


def prepare_cache_experiment(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    *,
    name="owned-cache-validation",
    repeats=2,
    request_contract=None,
    usage=True,
):
    base, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    model = {"provider": "openai", "model": "gpt-4.1", "max_output_tokens": 64}
    if request_contract is not None:
        model["request_contract"] = request_contract
    cached = {
        **model,
        "key": "exact-cache",
        "exact_cache": {
            "freshness_ref": "owned-immutable-v1",
            "ttl_seconds": 300,
            "content_retention_authorized": True,
            "immutable_or_safely_invalidated": True,
            "operation_cost_upper_usd": "0.000000001",
            "operation_cost_basis_ref": "owned-synthetic-compute-bound",
        },
    }
    plan = plan_for(
        dataset,
        name=name,
        baseline={**model, "key": "baseline"},
        candidates=[cached],
        approved_providers=["openai"],
        split="validation",
        sample_limit=3,
        repeats=repeats,
    )
    response = api[0].post(
        base + "/experiments/plans",
        headers=api[1],
        json={"dataset_id": dataset["id"], "specification": plan.model_dump(mode="json")},
    )
    assert response.status_code == 201, response.text
    frozen = response.json()
    approved = api[0].post(
        base + f"/experiments/plans/{frozen['id']}/approve",
        headers=api[1],
        json={"plan_hash": frozen["plan_hash"], "approval_ref": "owned-synthetic-cache-only"},
    )
    assert approved.status_code == 200, approved.text
    queued = api[0].post(base + "/experiments", headers=api[1], json={"plan_id": frozen["id"]})
    assert queued.status_code == 202, queued.text
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-only")
    monkeypatch.setenv("CUTBACK_CACHE_ENABLED", "true")
    calls = []

    def handle(request):
        calls.append(request)
        body = native_response(request).json()
        body.update(parallel_tool_calls=False, tool_choice="none", tools=[])
        if not usage:
            body.pop("usage", None)
        return httpx.Response(200, json=body)

    executor = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    )
    executor.synthetic_transport = True
    return store, UUID(queued.json()["id"]), executor, calls


def qualify_source_runtime(api, tenant, source, artifact, state, contract, directory, monkeypatch):
    """Apply only in owned copies, check both real Git trees, enable against owned TCP."""
    import hashlib
    import json
    import os
    import subprocess

    from openai import OpenAI
    from support_http import owned_collector

    from cutback.application_checks import ApplicationCommand, prepare, run_checks, verify
    from cutback.core.workspace import write_private

    original = (source / "app.py").read_bytes()
    check_source = """import os, sys
from types import SimpleNamespace
assert "CUTBACK_NATIVE_CACHE_SCOPE_FILE" not in os.environ
assert "OPENAI_API_KEY" not in os.environ
seen = []
answer = SimpleNamespace(output_text="A")
def respond(**request):
    seen.append(request)
    return answer
client = SimpleNamespace(responses=SimpleNamespace(create=respond))
sys.modules["openai"] = SimpleNamespace(OpenAI=lambda: client)
from app import classify
assert classify("owned synthetic source check") is answer
assert seen[0]["input"] == "owned synthetic source check"
assert seen[0]["model"] == "gpt-4.1-mini"
assert seen[0]["max_output_tokens"] == 16
"""
    base, proposed = directory / "base", directory / "proposed"

    def git(root, *arguments, patch=None):
        checked = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "core.autocrlf=false",
                "-c",
                "commit.gpgSign=false",
                "-c",
                "core.hooksPath=" + str(directory / "no-hooks"),
                "-c",
                "user.name=Cutback synthetic fixture",
                "-c",
                "user.email=fixture@example.invalid",
                *arguments,
            ],
            input=patch.encode() if patch is not None else None,
            capture_output=True,
            timeout=15,
        )
        assert checked.returncode == 0, "OWNED_CACHE_SOURCE_GIT_OPERATION_FAILED"

    for root in (base, proposed):
        root.mkdir(parents=True)
        (root / "app.py").write_bytes(original)
        (root / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        (root / "check_application.py").write_text(check_source, encoding="utf-8")
        git(root, "-c", "init.templateDir=", "init", "--quiet")
        git(root, "add", "app.py", ".gitignore", "check_application.py")
        git(root, "commit", "--quiet", "-m", "Independent synthetic base")
    git(proposed, "apply", "--check", "-", patch=artifact["diff"])
    git(proposed, "apply", "-", patch=artifact["diff"])
    changed = (proposed / "app.py").read_bytes()
    assert hashlib.sha256(changed).hexdigest() == artifact["verification"]["after_sha256"]
    git(proposed, "add", "app.py")
    git(proposed, "commit", "--quiet", "-m", "Explicit synthetic cache proposal")
    checks = prepare(
        base,
        proposed,
        [
            ApplicationCommand(
                key="independent-business-behavior", arguments=["check_application.py"]
            )
        ],
    )
    receipt = run_checks(checks, base, proposed, execute_local=True)
    assert receipt["all_passed"] and len(receipt["results"]) == 2
    assert verify(checks, receipt, base, proposed)["authenticity"] == "not_independently_attested"

    calls = []

    def handle(request):
        calls.append(request)
        body = native_response(request).json()
        body.update(parallel_tool_calls=False, tool_choice="none", tools=[])
        return httpx.Response(200, json=body)

    native = OpenAI(
        api_key="owned-synthetic-only",
        max_retries=2,
        http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False),
    )
    native.organization, native.project = None, None
    try:
        monkeypatch.setenv("OPENAI_API_KEY", "owned-synthetic-placeholder-only")
        monkeypatch.delenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", raising=False)
        namespace = {"__name__": "owned_synthetic_cache_application"}
        # Only the independent fixture in this owned process is executed. The
        # scanner, shared collector and worker never import application source.
        exec(compile(changed, str(proposed / "app.py"), "exec"), namespace)
        namespace["client"].close()
        namespace["client"] = native
        with owned_collector(api[2]) as endpoint:
            uploaded_artifact = {
                **artifact,
                "application_checks": {
                    "contract": checks.model_dump(mode="json"),
                    "receipt": receipt,
                    "verified": verify(checks, receipt, base, proposed),
                },
            }
            with httpx.Client(base_url=endpoint, trust_env=False, timeout=30) as owner:
                rec_path = (
                    f"/v1/projects/{tenant[0]}/recommendations/{artifact['recommendation_id']}"
                )
                reviewed = owner.post(
                    rec_path + "/review",
                    headers=api[1],
                    json={
                        "decision": "accepted_for_staging",
                        "reason": "owned-synthetic-cache-review-only",
                    },
                )
                assert reviewed.status_code == 200, "SYNTHETIC_CACHE_REVIEW_FAILED"
                uploaded = owner.post(
                    rec_path + "/patches",
                    headers=api[1],
                    json={
                        "approval_ref": "owned-synthetic-encrypted-cache-source",
                        "artifact": uploaded_artifact,
                    },
                )
                assert uploaded.status_code == 201, "SYNTHETIC_CACHE_PATCH_UPLOAD_FAILED"
                assert uploaded.json()["production_deployed"] is False
                stored = owner.post(
                    f"/v1/projects/{tenant[0]}/changes/{uploaded.json()['id']}/content",
                    headers=api[1],
                    json={"purpose": "owned-cache-source-roundtrip"},
                )
                assert stored.status_code == 200 and stored.json()["diff"] == artifact["diff"]
                assert stored.json()["native_fixture"]["real_model_quality_verified"] is False
                assert tenant[1]["Authorization"][7:] not in json.dumps(stored.json())
                exported = owner.get(f"/v1/projects/{tenant[0]}/export", headers=api[1])
                assert (
                    exported.status_code == 200
                    and b"def _cutback_cached_responses" not in exported.content
                )
            runtime = artifact["runtime_cache"]
            configuration = {
                "version": "cutback.native-cache-runtime.v1",
                "contract": contract,
                "scope": {
                    "endpoint": endpoint,
                    "project_id": tenant[0],
                    "policy_id": state["policy_id"],
                    "token": tenant[1]["Authorization"][7:],
                    "request_hmac_key": "owned-private-runtime-hmac-material-" + "x" * 32,
                    "workflow": runtime["workflow"],
                    "environment": "staging",
                    "prompt_revision": runtime["request_revision"],
                    "freshness_ref": contract["freshness_ref"],
                    "ttl_seconds": contract["ttl_seconds"],
                    "content_retention_authorized": True,
                    "immutable_or_safely_invalidated": True,
                },
            }
            path = directory / "private-runtime.json"
            write_private(path, configuration)
            monkeypatch.setenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", str(path))
            first = namespace["classify"]("owned synthetic runtime source")
            hit = namespace["classify"]("owned synthetic runtime source")
            assert first.output_text == hit.output_text == "A" and len(calls) == 1
            assert json.loads(calls[0].content)["store"] is False
            assert native.max_retries == 2 and not native._client.is_closed
            # A bad configured file must stop; it cannot silently fall back to
            # the uncached provider path once the owner explicitly enabled it.
            invalid = directory / "invalid-runtime.json"
            invalid.write_text("{}", encoding="utf-8")
            monkeypatch.setenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", str(invalid))
            try:
                namespace["classify"]("owned synthetic runtime source")
            except ValueError as error:
                assert str(error) == "NATIVE_CACHE_PRIVATE_RUNTIME_CONFIGURATION_INVALID"
            else:
                raise AssertionError("ENABLED_INVALID_CACHE_MUST_NOT_FALL_BACK")
            assert len(calls) == 1
    finally:
        native.close()
        monkeypatch.delenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", raising=False)
    git(proposed, "apply", "--check", "-", patch=artifact["rollback_diff"])
    git(proposed, "apply", "-", patch=artifact["rollback_diff"])
    assert (proposed / "app.py").read_bytes() == (source / "app.py").read_bytes() == original
    assert "CUTBACK_NATIVE_CACHE_SCOPE_FILE" not in os.environ
    return checks, receipt
