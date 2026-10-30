"""Independent bounded JSON application; guard checks use an actual owned process."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cutback.core.guardrails import GuardrailPolicy, LiteralDenyRule, ToolAllowlistRule
from cutback.guarded_workflow import GuardedWorkflow, GuardrailRejected
from cutback.sample_app.guardrail_service import owned_guardrail_service


def run_guardrail_sample():
    now = datetime.now(UTC)
    policy = GuardrailPolicy(
        policy_id=uuid4(),
        project_id=uuid4(),
        principal_id=uuid4(),
        environment="staging",
        workflows=("owned-support-task",),
        starts_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=15),
        content_use_approved=True,
        protected_stages=("input", "output", "tool_input", "tool_output"),
        rules=(
            LiteralDenyRule(
                key="owned-literal-boundary",
                stages=("input", "output", "tool_input", "tool_output"),
                terms=("SYNTHETIC_SAMPLE_CANARY",),
            ),
            ToolAllowlistRule(key="owned-read-only-tool", names=("lookup_owned_ticket",)),
        ),
    )
    calls, results = [], {}

    def application(value):
        calls.append("application")
        return {"answer": "Owned synthetic answer"}

    def unsafe_output(value):
        calls.append("application")
        return {"answer": "SYNTHETIC_SAMPLE_CANARY"}

    with owned_guardrail_service(policy, workflow="owned-support-task") as service:
        workflow = GuardedWorkflow(service.client)
        allowed = workflow.invoke({"question": "Find the owned ticket"}, application)
        results["benign"] = {"delivered": True, "coverage_complete": allowed.coverage_complete}
        for name, value, function in (
            ("input_deny", {"question": "SYNTHETIC_SAMPLE_CANARY"}, application),
            (
                "output_deny",
                {"question": "Owned request"},
                unsafe_output,
            ),
        ):
            try:
                workflow.invoke(value, function)
            except GuardrailRejected as denied:
                results[name] = {
                    "delivered": False,
                    "dispatched": denied.dispatched,
                    "code": str(denied),
                    "cost_usd": denied.cost,
                }
        tools = GuardedWorkflow(service.client, tool=True)
        tool_calls = []
        try:
            tools.invoke(
                {"name": "unapproved_delete", "arguments": {}}, lambda _: tool_calls.append(1)
            )
        except GuardrailRejected as denied:
            results["unapproved_tool"] = {"dispatched": denied.dispatched, "code": str(denied)}
        if (
            len(calls) != 2
            or tool_calls
            or set(results) != {"benign", "input_deny", "output_deny", "unapproved_tool"}
        ):
            raise ValueError("OWNED_GUARDRAIL_SAMPLE_ACCEPTANCE_FAILED")
    return {
        "version": "cutback.runtime-guardrail-sample.v1",
        "synthetic": True,
        "separate_checker_process": True,
        "application_calls": len(calls),
        "vendor_calls": 0,
        "results": results,
        "model_semantic_safety": "not_measured",
        "persistent_collector_approval": "separate_integration_required",
    }
