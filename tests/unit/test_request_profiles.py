import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from cutback.changes import model_patch
from cutback.core.request_profiles import prepare_profile
from cutback.evals.executors import OpenAIExecutor
from cutback.evals.experiments import ModelSpec
from cutback.scanner import scan


def inspect(root, source):
    (root / "app.py").write_text(source, encoding="utf-8")
    inventory = scan(root)
    site = inventory["call_sites"][0]
    return inventory, site, prepare_profile(root, site, commit=inventory["project"].get("commit"))


SOURCE = """from openai import OpenAI
raise RuntimeError("application source must never execute during analysis")
client=OpenAI()
def classify(text):
    return client.responses.create(model="gpt-4.1",input=text,
        instructions="Private synthetic task",max_output_tokens=64,
        text={"format":{"type":"json_object"}})
"""


def recommendation(profile):
    baseline = profile.baseline()
    return {
        "id": str(uuid4()),
        "run_id": str(uuid4()),
        "status": "accepted_for_staging",
        "evidence": {
            "evaluator_version": "deterministic-v3",
            "lineage": {"contract": "cutback.lineage.v1", "fresh_test_claim": True},
            "quality_gate_passed": True,
            "cheaper_on_sample": True,
            "status": "PROMISING",
            "dataset_hash": "a" * 64,
            "plan_hash": "b" * 64,
            "source_binding": profile.binding.model_dump(mode="json"),
            "candidate": {**baseline, "key": "candidate", "model": "gpt-4.1-mini"},
            "rollback": {"baseline": baseline},
        },
    }


def test_patch_context_never_emits_a_known_secret_pattern(tmp_path):
    source = SOURCE.replace(
        "client=OpenAI()", 'client=OpenAI(api_key="sk-ownedSyntheticSentinel123456789")'
    )
    inventory, site, profile = inspect(tmp_path, source)
    with pytest.raises(ValueError, match="PATCH_CONTEXT_REQUIRES_MINIMAL_PRIVATE_REVIEW"):
        model_patch(
            tmp_path,
            inventory,
            site["call_site_id"],
            recommendation(profile),
            expected_source_sha256=profile.binding.source_sha256,
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace("client=OpenAI()", 'client=OpenAI(base_url="https://example.invalid")'),
        lambda s: s.replace("    return client.", '    text = "changed"\n    return client.'),
        lambda s: s.replace(
            "def classify(text):",
            'SYSTEM="first"\ndef classify(text):\n    if text:\n        SYSTEM="other"',
        ).replace('instructions="Private synthetic task"', "instructions=SYSTEM"),
    ],
)
def test_unqualified_dynamic_or_custom_destination_abstains(tmp_path, change):
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        inspect(tmp_path, change(SOURCE))


@pytest.mark.parametrize(
    "mutation",
    ["messages.append({'role':'user','content':text})", "messages[0]['content']='changed'"],
)
def test_mutated_messages_are_not_replayed_as_their_old_literal(tmp_path, mutation):
    source = """from openai import OpenAI
client=OpenAI()
def classify(text):
    messages=[{"role":"user","content":text}]
    MUTATION
    return client.chat.completions.create(model="gpt-4.1", messages=messages,max_tokens=64)
""".replace("MUTATION", mutation)
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        inspect(tmp_path, source)


def test_rendered_request_budget_is_checked_before_executor_or_reservation():
    from cutback.core.request_profiles import RequestContract
    from cutback.evals.experiments import Budget, BudgetLedger

    spec = ModelSpec(
        key="baseline",
        provider="openai",
        model="gpt-4.1",
        max_input_tokens=5000,
        request_contract=RequestContract(
            sdk_surface="chat.completions.create",
            parameters=["text"],
            payload={
                "op": "list",
                "items": [
                    {
                        "op": "dict",
                        "items": {
                            "role": {"op": "literal", "value": "system"},
                            "content": {"op": "literal", "value": "x" * 6000},
                        },
                    }
                ],
            },
        ),
    )
    reserved = []
    ledger = BudgetLedger(Budget(max_usd="1", max_requests=2), reserve=reserved.append)

    class Never:
        def execute(self, *args):
            raise AssertionError("BILLABLE_EXECUTOR_MUST_NOT_RUN")

    with pytest.raises(ValueError, match="INPUT_EXCEEDS_PREAPPROVED_TOKEN_BOUND"):
        ledger.call(Never(), spec, "small")
    assert not reserved and ledger.requests == 0


def test_actual_request_profile_preserves_contract_and_blocks_cross_workflow_patch(tmp_path):
    inventory, site, profile = inspect(tmp_path, SOURCE)
    assert profile.instruction == "Private synthetic task"
    assert "Private synthetic task" not in json.dumps(profile.public())
    assert profile.request.render("owned input") == "owned input"
    rec = recommendation(profile)
    before = (tmp_path / "app.py").read_bytes()
    patch = model_patch(
        tmp_path,
        inventory,
        site["call_site_id"],
        rec,
        expected_source_sha256=hashlib.sha256(before).hexdigest(),
    )
    assert (
        "gpt-4.1-mini" in patch["diff"]
        and patch["source_binding"] == rec["evidence"]["source_binding"]
    )
    assert (tmp_path / "app.py").read_bytes() == before
    rec["evidence"]["source_binding"] = None
    with pytest.raises(ValueError, match="SOURCE_REQUEST_BINDING_REQUIRED"):
        model_patch(
            tmp_path,
            inventory,
            site["call_site_id"],
            rec,
            expected_source_sha256=hashlib.sha256(before).hexdigest(),
        )
    rec = recommendation(profile)
    changed = SOURCE.replace("Private synthetic task", "Another synthetic business rule")
    inventory, site, _ = inspect(tmp_path, changed)
    with pytest.raises(ValueError, match="SOURCE_REQUEST_PROFILE_CHANGED"):
        model_patch(
            tmp_path,
            inventory,
            site["call_site_id"],
            rec,
            expected_source_sha256=hashlib.sha256((tmp_path / "app.py").read_bytes()).hexdigest(),
        )


def test_config_backed_model_and_prompt_dependencies_are_bound(tmp_path):
    (tmp_path / "config.py").write_text('MODEL="gpt-4.1"\n')
    (tmp_path / "task.md").write_text("Owned task instruction")
    source = """from pathlib import Path
from config import MODEL
from openai import OpenAI
client=OpenAI()
SYSTEM=Path("task.md").read_text()
def classify(text):
    return client.responses.create(model=MODEL,input=text,
        instructions=SYSTEM,max_output_tokens=64)
"""
    inventory, site, profile = inspect(tmp_path, source)
    assert set(profile.binding.dependencies) == {"app.py", "config.py", "task.md"}
    rec = recommendation(profile)
    result = model_patch(
        tmp_path,
        inventory,
        site["call_site_id"],
        rec,
        expected_source_sha256=hashlib.sha256((tmp_path / "config.py").read_bytes()).hexdigest(),
    )
    assert result["source_file"] == "config.py" and result["call_source_file"] == "app.py"
    (tmp_path / "task.md").write_text("Changed task instruction")
    with pytest.raises(ValueError, match="SOURCE_REQUEST_PROFILE_CHANGED"):
        model_patch(
            tmp_path,
            inventory,
            site["call_site_id"],
            rec,
            expected_source_sha256=hashlib.sha256(
                (tmp_path / "config.py").read_bytes()
            ).hexdigest(),
        )


@pytest.mark.parametrize("surface", ["responses", "chat"])
def test_bound_native_executor_preserves_request_settings_and_never_retries(
    tmp_path, monkeypatch, surface
):
    source = (
        SOURCE
        if surface == "responses"
        else """from openai import OpenAI
client=OpenAI()
def classify(text):
    return client.chat.completions.create(model="gpt-4.1",
        messages=[{"role":"system","content":"Owned static instruction"},
                  {"role":"user","content":text}],max_tokens=64,
        response_format={"type":"json_object"},temperature=0)
"""
    )
    _, _, profile = inspect(tmp_path, source)
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if surface == "responses":
            return httpx.Response(
                200,
                json={
                    "id": "resp_owned",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": body["model"],
                    "output": [
                        {
                            "id": "msg_owned",
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "{}", "annotations": []}],
                        }
                    ],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "total_tokens": 12,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chat_owned",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "{}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-not-a-provider-key")
    executor = OpenAIExecutor(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        result = executor.execute(ModelSpec.model_validate(profile.baseline()), "owned input", 5)
        assert result.complete and result.output == "{}" and len(seen) == 1
        if surface == "responses":
            assert (
                seen[0]["instructions"] == profile.instruction
                and seen[0]["max_output_tokens"] == 64
            )
            assert seen[0]["text"] == {"format": {"type": "json_object"}}
        else:
            assert seen[0]["messages"] == [
                {"role": "system", "content": "Owned static instruction"},
                {"role": "user", "content": "owned input"},
            ]
            assert (
                seen[0]["response_format"] == {"type": "json_object"}
                and seen[0]["temperature"] == 0
            )
    finally:
        executor.close()
    errors = []

    def outage(request):
        errors.append(1)
        return httpx.Response(
            429, json={"error": {"message": "owned outage", "type": "rate_limit"}}
        )

    executor = OpenAIExecutor(http_client=httpx.Client(transport=httpx.MockTransport(outage)))
    try:
        with pytest.raises(__import__("openai").RateLimitError):
            executor.execute(ModelSpec.model_validate(profile.baseline()), "owned", 1)
        assert len(errors) == 1
    finally:
        executor.close()


def test_unqualified_tools_and_unbounded_requests_abstain(tmp_path):
    for source in (
        SOURCE.replace('text={"format":{"type":"json_object"}}', "tools=[]"),
        SOURCE.replace("max_output_tokens=64,", ""),
    ):
        (tmp_path / "app.py").write_text(source)
        inventory = scan(tmp_path)
        with pytest.raises(ValueError, match="UNQUALIFIED_REQUEST_OPTIONS|EXPLICIT_OUTPUT_BOUND"):
            prepare_profile(tmp_path, inventory["call_sites"][0])


def test_foreign_function_assignments_cannot_change_instruction(tmp_path):
    source = SOURCE.replace(
        "def classify(text):", 'def unrelated():\n    SYSTEM="Unrelated task"\ndef classify(text):'
    ).replace('instructions="Private synthetic task"', "instructions=SYSTEM")
    (tmp_path / "app.py").write_text(source)
    with pytest.raises(ValueError, match="DYNAMIC_REQUEST_VALUE"):
        prepare_profile(tmp_path, scan(tmp_path)["call_sites"][0])
