"""An agent can inspect current-source metadata without importing or executing its app."""

import builtins
from uuid import UUID, uuid4

import pytest
from support_policy_application import application
from test_source_policy_cli import command
from typer.testing import CliRunner

from cutback.agent_policy_activity import inspect_current
from cutback.core.policy_activity import PolicyRuntimeScope, PolicyRuntimeSummary


class Metadata:
    def __init__(self, *, change=None):
        self.project = str(uuid4())
        self.calls = []
        self.change = change

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            assert path == "/v1/health/ready"
            return {"source_policy_runtime_contract": "cutback.source-policy-runtime.v1"}
        assert method == "POST" and path == f"/v1/projects/{self.project}/policy-runtime/inspect"
        result = PolicyRuntimeSummary(
            scope=PolicyRuntimeScope.model_validate(body),
            project_id=self.project,
            environment_id=uuid4(),
            retained_operations=0,
            truncated=False,
            raw_sha256="a" * 64,
            states={},
            completions={},
            configured_operations=0,
            requested_cache_operations=0,
            known_native_sdk_invocations=0,
            native_sdk_invocations=None,
            unknown_native_operations=0,
            known_cache_operations=0,
            cache_operations=None,
            unknown_cache_operations=0,
            cache_component_upper_usd=None,
            cache_cost_basis_refs=[],
        ).model_dump(mode="json")
        if self.change:
            self.change(result)
        return result


@pytest.mark.parametrize("asynchronous", [False, True])
def test_current_source_is_qualified_parse_only_without_private_runtime_scope_or_provider_import(
    tmp_path, monkeypatch, asynchronous
):
    root = application(tmp_path, async_consumer=asynchronous)
    control = Metadata()
    monkeypatch.setenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", "OWNED_PRIVATE_UNREAD_RUNTIME_FILE")
    original = builtins.__import__

    def forbid(name, *args, **kwargs):
        if name.split(".")[0] in {"openai", "anthropic", "fastapi", "app"}:
            raise AssertionError("NO_RUNTIME_IMPORT_FOR_METADATA_INSPECTION")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forbid)
    result = inspect_current(
        root, control, control.project, producer_file="app.py", service_name="owned-support"
    )
    assert result["status"] == "RETAINED_METADATA_ONLY" and result["provider_calls"] == 0
    assert result["application_source_changed"] is False
    assert len(control.calls) == 2 and result["summary"]["retained_operations"] == 0
    assert result["summary"]["cache_component_upper_usd"] is None
    assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in str(result)
    assert "OWNED_PRIVATE_UNREAD_RUNTIME_FILE" not in str(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_commit", "d" * 40),
        ("policy_sha256", "d" * 64),
        ("call_site_id", "other"),
        ("environment", "other"),
        ("service_name", "other"),
    ],
)
def test_other_scope_cannot_be_accepted_as_current_application_evidence(tmp_path, field, value):
    root = application(tmp_path)
    control = Metadata(change=lambda result: result["scope"].update({field: value}))
    with pytest.raises(ValueError, match="SOURCE_OR_INSPECTION_SCOPE_CHANGED"):
        inspect_current(
            root, control, control.project, producer_file="app.py", service_name="owned"
        )


def test_other_project_and_source_change_during_metadata_request_are_refused(tmp_path):
    root = application(tmp_path)
    control = Metadata(change=lambda result: result.update(project_id=str(uuid4())))
    with pytest.raises(ValueError, match="SOURCE_OR_INSPECTION_SCOPE_CHANGED"):
        inspect_current(
            root, control, control.project, producer_file="app.py", service_name="owned"
        )

    def mutate(result):
        with (root / "app.py").open("a", encoding="utf-8") as stream:
            stream.write("\n# concurrent source change\n")

    control = Metadata(change=mutate)
    with pytest.raises(ValueError):
        inspect_current(
            root, control, control.project, producer_file="app.py", service_name="owned"
        )


def test_cli_preserves_read_only_controls_and_bounds_before_collector_io(tmp_path, monkeypatch):
    root = application(tmp_path)
    control = Metadata()
    monkeypatch.setattr(
        "cutback.agent_control.connect", lambda *args, **kwargs: (control, control.project)
    )
    app, output, errors = command()
    args = [
        "source-policy",
        "inspect-activity",
        "--target",
        str(root),
        "--producer",
        "app.py",
        "--service",
        "owned",
    ]
    assert CliRunner().invoke(app, args).exit_code == 0
    assert output[0]["provider_calls"] == 0 and errors == [] and len(control.calls) == 2
    control.calls.clear()
    assert CliRunner().invoke(app, [*args, "--window-minutes", "0"]).exit_code == 2
    assert (
        errors[0][0] == "POLICY_ACTIVITY_BOUNDED_INSPECTION_WINDOW_REQUIRED" and control.calls == []
    )


@pytest.mark.parametrize(
    "change",
    [
        {"native_sdk_invocations": 0},
        {"cache_operations": 0},
        {"cache_component_upper_usd": "0"},
        {"cache_component_upper_usd": 0.0},
        {"known_native_sdk_invocations": True},
        {"unknown_native_operations": 1},
        {"states": {"hit": 1}},
        {"invoice_verified_cost_usd": "0"},
        {"business_task_success": True},
        {"content_included": True},
        {"execution_authorized": True},
        {"raw_sha256": "invalid"},
        {"project_id": str(UUID(int=0))},
    ],
)
def test_agent_refuses_false_complete_totals_and_unearned_evidence_claims(tmp_path, change):
    root = application(tmp_path)
    control = Metadata(change=lambda result: result.update(change))
    with pytest.raises(ValueError):
        inspect_current(
            root, control, control.project, producer_file="app.py", service_name="owned"
        )
