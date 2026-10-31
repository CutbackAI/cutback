"""Exact source configuration binding, rejected changes and byte-exact rollback."""

import json
import shutil
from uuid import uuid4

import pytest
from support_compression_application import git

from cutback.core.routing_producer import PROGRAM
from cutback.evals.experiments import ModelSpec
from cutback.routing_changes import (
    RoutingSourceProposal,
    inspect_configuration,
    policy_bytes,
    prepare_patch,
    review_change,
)


def application(tmp_path):
    root = tmp_path / "application"
    root.mkdir()
    (root / "app.py").write_text(PROGRAM, encoding="utf-8")
    baseline = ModelSpec(
        key="source-baseline",
        provider="openai",
        model="gpt-4.1",
        instruction="Classify the owned priority. Return only A or B.",
        max_output_tokens=64,
    )
    policy = ModelSpec(
        key="source-policy",
        provider="router",
        model="input-size-v1",
        routes=[{"max_input_bytes": 4000, "target": baseline}],
    )
    (root / "routing.json").write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n.cutback/\n", encoding="utf-8")
    git(root, "init")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent native routing baseline",
    )
    return root


def proposal(root):
    baseline, binding = inspect_configuration(root, "app.py")
    candidate = ModelSpec(
        key="fitted",
        provider="router",
        model="input-size-v1",
        routes=[
            {
                "max_input_bytes": 100,
                "target": baseline.model_copy(
                    update={"key": "small", "model": "gpt-4.1-mini", "max_output_tokens": 32}
                ),
            },
            {"max_input_bytes": 4000, "target": baseline},
        ],
    )
    return RoutingSourceProposal(
        run_id=uuid4(),
        plan_hash="a" * 64,
        dataset_hash="b" * 64,
        binding=binding,
        baseline=baseline,
        proposed=candidate,
        decision="synthetic_local_review_only",
    )


def test_review_runs_both_actual_native_consumers_and_preserves_local_branch(tmp_path):
    from cutback.application_checks import ApplicationCommand, prepare, run_checks

    base = application(tmp_path)
    value = proposal(base)
    artifact = prepare_patch(base, value)
    target = tmp_path / "proposed"
    shutil.copytree(base, target)
    patch = tmp_path / "proposal.patch"
    patch.write_text(artifact["diff"], encoding="utf-8", newline="")
    git(target, "apply", str(patch))
    git(target, "add", ".")
    git(
        target,
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent proposed native policy",
    )
    command = ApplicationCommand(
        key="native-consumer",
        arguments=[
            "-c",
            (
                "import httpx,json; from openai import OpenAI; from app import classify; "
                "calls=[]; "
                "transport=httpx.MockTransport(lambda r: (calls.append(json.loads(r.content)) or "
                "httpx.Response(200,json={'id':'resp_owned','created_at':1,'object':'response',"
                "'model':json.loads(r.content)['model'],'status':'completed','output':[],"
                "'parallel_tool_calls':True,'tool_choice':'auto','tools':[]}))); "
                "client=OpenAI(api_key='synthetic-no-network',max_retries=0,"
                "http_client=httpx.Client(transport=transport)); "
                "result=classify(client,'owned'); "
                "assert result.id=='resp_owned' and result.status=='completed'; "
                "assert len(calls)==1 and calls[0]['store'] is False; "
                "assert calls[0]['model'] in {'gpt-4.1','gpt-4.1-mini'}; "
                "assert calls[0]['instructions']=='Classify the owned priority. "
                "Return only A or B.'; "
                "assert calls[0]['max_output_tokens'] in {32,64}; "
                "client.close()"
            ),
        ],
    )
    contract = prepare(base, target, [command])
    receipt = run_checks(contract, base, target, execute_local=True)
    for _ in range(2):
        reviewed = review_change(
            artifact,
            contract,
            receipt,
            base,
            target,
            {"owned-priority": ["native-consumer"]},
            branch="cutback/owned-routing-review",
        )
        assert reviewed["status"] == "READY_FOR_LOCAL_REVIEW"
        assert reviewed["local_review_ref"] == "refs/heads/cutback/owned-routing-review"
        assert not reviewed["remote_published"] and not reviewed["full_task_cost_known"]
    for coverage in ({}, {"owned-priority": ["missing"]}, {"owned-priority": [{}]}):
        with pytest.raises(ValueError, match="CONSUMER_CHECK_COVERAGE"):
            review_change(artifact, contract, receipt, base, target, coverage)
    git(target, "update-ref", "refs/heads/cutback/owned-routing-review", contract.base.commit)
    with pytest.raises(ValueError, match="BRANCH_ALREADY_POINTS_ELSEWHERE"):
        review_change(
            artifact,
            contract,
            receipt,
            base,
            target,
            {"owned-priority": ["native-consumer"]},
            branch="cutback/owned-routing-review",
        )
    with pytest.raises(ValueError, match="ARTIFACT_CHANGED"):
        review_change(
            {**artifact, "proposed_sha256": "f" * 64},
            contract,
            receipt,
            base,
            target,
            {"owned-priority": ["native-consumer"]},
        )


def test_two_tracked_files_bind_and_patch_and_rollback_preserve_the_exact_native_consumer(tmp_path):
    root = application(tmp_path)
    value = proposal(root)
    original = (root / "routing.json").read_bytes()
    producer = (root / "app.py").read_bytes()
    change = prepare_patch(root, value)
    assert change["applied"] is False and change["production_deployed"] is False
    patch = tmp_path / "source.patch"
    patch.write_text(change["diff"], encoding="utf-8", newline="")
    git(root, "apply", "--check", str(patch))
    git(root, "apply", str(patch))
    actual = policy_bytes((root / "routing.json").read_bytes())
    assert actual.key == value.binding.configuration_key
    assert actual.routes[0].target.model == "gpt-4.1-mini"
    assert actual.routes[-1].max_input_bytes == 4000
    assert (root / "app.py").read_bytes() == producer
    patch.write_text(change["rollback_diff"], encoding="utf-8", newline="")
    git(root, "apply", "--check", str(patch))
    git(root, "apply", str(patch))
    assert (root / "routing.json").read_bytes() == original


@pytest.mark.parametrize("changed", ["instruction", "max_output_tokens", "provider", "input_bound"])
def test_proposals_cannot_widen_the_source_frame_provider_output_or_input_bound(tmp_path, changed):
    value = proposal(application(tmp_path)).model_dump(mode="json")
    target = value["proposed"]["routes"][0]["target"]
    if changed == "instruction":
        target["instruction"] = "A different task"
    elif changed == "max_output_tokens":
        target["max_output_tokens"] = 128
    elif changed == "provider":
        target["provider"], target["model"] = "anthropic", "claude-haiku-4-5"
    else:
        value["proposed"]["routes"][-1]["max_input_bytes"] = 4001
    with pytest.raises(ValueError):
        RoutingSourceProposal.model_validate(value)


def test_dirty_changed_or_unqualified_source_and_ambiguous_json_do_not_produce_a_patch(tmp_path):
    root = application(tmp_path)
    value = proposal(root)
    with pytest.raises(ValueError, match="NOT_QUALIFIED"):
        policy_bytes(b'{"key":"first","key":"ambiguous"}')
    (root / "app.py").write_text(
        PROGRAM + '\nraise RuntimeError("must never execute")\n', encoding="utf-8"
    )
    with pytest.raises(ValueError):
        prepare_patch(root, value)
    git(root, "add", "app.py")
    git(
        root,
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Unqualified source refuses",
    )
    with pytest.raises(ValueError, match="NOT_QUALIFIED"):
        inspect_configuration(root, "app.py")
