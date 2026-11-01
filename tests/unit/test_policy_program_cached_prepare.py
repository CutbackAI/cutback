"""Cached-current preparation freezes metadata without runtime activation or LLM calls."""

import json
from uuid import UUID, uuid4

import pytest
from support_policy_activity import frame
from support_policy_application import cached_application

from cutback.agent_policy_program import prepare
from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.policy_activity import PolicyRuntimeScope
from cutback.evals.policy_artifacts import prepare_policy_payload


class Remote:
    endpoint = "https://collector.example.invalid"

    def __init__(self, *, requested):
        self.project, self.environment, self.dataset, self.snapshot = [
            str(uuid4()) for _ in range(4)
        ]
        self.plan, self.program = str(uuid4()), str(uuid4())
        _, _, self.summary, _ = frame(active=requested)
        self.calls, self.scopes, self.policies, self.plans, self.previews = [], [], [], [], []
        self.raw = "b" * 64
        self.services = ["owned-current"]
        self.references = {}

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if path == "/v1/health/ready":
            return {
                "source_policy_program_contract": "cutback.source-policy-program.v1",
                "source_policy_cached_current_program_contract": "cutback.source-policy-program.v2",
                "source_policy_runtime_contract": "cutback.source-policy-runtime.v1",
                "source_policy_runtime_services_contract": (
                    "cutback.source-policy-runtime-services.v1"
                ),
            }
        if path.endswith("/policy-runtime/services"):
            return dict(
                project_id=self.project,
                environment_id=self.environment,
                scope=body,
                services=self.services,
                truncated=False,
            )
        if path.endswith("/policy-runtime/inspect"):
            self.scopes.append(body)
            return self.summary.model_copy(
                update={
                    "project_id": UUID(self.project),
                    "environment_id": UUID(self.environment),
                    "scope": PolicyRuntimeScope.model_validate(body),
                    "raw_sha256": self.raw,
                }
            ).model_dump(mode="json")
        if path.endswith("/datasets/" + self.dataset):
            return dict(
                id=self.dataset,
                hash="a" * 64,
                workflow="routing-task",
                split_counts={"train": 20},
                provenance={"source_type": "synthetic_fixture"},
            )
        assert method == "POST"
        if path.endswith("/source-policies"):
            self.policies.append(body)
            if body["request_id"] not in self.references:
                self.references[body["request_id"]] = prepare_policy_payload(
                    body["key"],
                    UUID(body["request_id"]),
                    SourceImprovementPolicy.model_validate(body["policy"]),
                )[1].model_dump(mode="json")
            return self.references[body["request_id"]]
        if path.endswith("/experiments/plans"):
            self.plans.append(body)
            return {"id": self.plan}
        assert path.endswith("/source-policy-programs")
        self.previews.append(body)
        return {"id": self.program, "program_hash": "c" * 64, "status": "READY_FOR_APPROVAL"}


def prepare_current(root, control, **changes):
    return prepare(
        root,
        control,
        control.project,
        producer_file="app.py",
        purpose="owned-current-review",
        dataset_id=control.dataset,
        baseline_snapshot_id=control.snapshot,
        max_usd="1",
        generate=True,
        **changes,
    )


@pytest.mark.parametrize("requested", [False, True])
def test_prepare_preserves_actual_cache_activation_and_retries_the_exact_frozen_window(
    tmp_path, requested
):
    root, original = cached_application(tmp_path)
    remote = Remote(requested=requested)
    first = prepare_current(root, remote)
    assert first["status"] == "READY_FOR_APPROVAL"
    execution = remote.plans[0]["specification"]["source_policy_execution"]
    assert execution["version"] == "cutback.source-policy-execution.v2"
    assert all(
        value["cache_requested"] is requested
        for value in [execution["reference"], *execution["candidates"]]
    )
    assert all(
        value["policy"]["cache_execution"] == original.cache_execution.model_dump(mode="json")
        for value in remote.policies
    )
    assert remote.previews[0]["runtime_reference"]["summary"]["requested_cache_operations"] == (
        remote.summary.retained_operations if requested else 0
    )
    saved = json.loads((root / ".cutback/agent/policy-program.json").read_text("utf-8"))
    assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in json.dumps(saved)
    assert prepare_current(root, remote) == first
    assert remote.scopes[0] == remote.scopes[1]  # No new timestamps on an uncertain reply.
    assert remote.previews[0] == remote.previews[1]
    assert remote.plans[0] == remote.plans[1]
    before = len(remote.policies)
    remote.raw = "f" * 64
    with pytest.raises(ValueError, match="RAW_ACTIVITY_CHANGED_OR_EXPIRED"):
        prepare_current(root, remote)
    assert len(remote.policies) == before
    assert all("approve" not in path and not path.endswith("/runs") for _, path in remote.calls)


def test_ambiguous_current_runtime_is_refused_before_content_upload(tmp_path):
    root, _ = cached_application(tmp_path)
    remote = Remote(requested=True)
    remote.services.append("another")
    with pytest.raises(ValueError, match="SELECT_EXPLICIT_SERVICE"):
        prepare_current(root, remote)
    assert not remote.policies and not remote.previews
    assert not (root / ".cutback/agent/policy-program.json").exists()


def test_existing_cache_contract_is_not_duplicated_or_silently_replaced(tmp_path):
    root, original = cached_application(tmp_path)
    remote = Remote(requested=False)
    prepare_current(root, remote, cache_contract=original.cache_execution.model_dump(mode="json"))
    assert all(row["key"] != "source-exact-cache" for row in remote.policies)
    assert len({row["policy"]["strategy"]["key"] for row in remote.policies}) == len(
        remote.policies
    )
    changed = original.cache_execution.model_copy(update={"ttl_seconds": 61})
    before = len(remote.policies)
    with pytest.raises(ValueError, match="CURRENT_CACHE_CONTRACT_MUST_BE_PRESERVED"):
        prepare_current(root, remote, cache_contract=changed.model_dump(mode="json"))
    assert len(remote.policies) == before


def test_cli_explains_unqualified_runtime_evidence_without_exposing_validation_content(
    tmp_path, monkeypatch
):
    from test_source_policy_cli import command
    from typer.testing import CliRunner

    root, _ = cached_application(tmp_path)
    remote = Remote(requested=True)
    remote.summary = remote.summary.model_copy(update={"completions": {"failed": 2}})
    monkeypatch.setattr(
        "cutback.agent_control.connect", lambda *args, **kw: (remote, remote.project)
    )
    app, output, errors = command()
    result = CliRunner().invoke(
        app,
        [
            "source-policy",
            "prepare",
            "--target",
            str(root),
            "--producer",
            "app.py",
            "--purpose-reference",
            "owned-no-spend",
            "--max-usd",
            "1",
            "--dataset",
            remote.dataset,
            "--baseline-snapshot",
            remote.snapshot,
        ],
    )
    assert result.exit_code == 2 and not output
    assert errors[0][0] == "POLICY_RUNTIME_REFERENCE_COMPLETE_UNIFORM_RETAINED_ACTIVITY_REQUIRED"
    assert "inspect-activity" in errors[0][1]
    assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in str(errors) + result.output
    assert not remote.policies and not remote.previews
