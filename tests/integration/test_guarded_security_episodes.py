"""Actual scoped guards and SQL distinguish benign interruption from attack resistance."""

import json

import httpx
import pytest
from integration.test_guarded_tool_episodes import checker
from integration.test_security_episodes import scripted_security_reply, security_run
from sqlalchemy import select
from support_isolated_tools import scoped_broker
from support_native_agent import native_executor
from unit.test_tool_broker import LocalTestRuntime

from cutback.core.guardrails import GuardrailBinding
from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_db import ExecutionEpisode
from cutback.server.tool_db import ToolOperationRecord
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


@pytest.mark.parametrize("deny", [False, True])
def test_paired_actual_guard_reports_known_writes_and_benign_family_interruption(
    api, tenant, tmp_path, monkeypatch, deny
):
    base, store, run, project, principal, broker, image, canaries, _, _, definition, scope = (
        security_run(
            api,
            tenant,
            tmp_path,
            monkeypatch,
            guarded=True,
            guard_term="resolved" if deny else "OWNED_UNMATCHED_OUTPUT_CANARY",
        )
    )
    binding = GuardrailBinding.model_validate(scope)
    calls = []

    def model(request):
        calls.append(request)
        return httpx.Response(200, json=scripted_security_reply(request))

    executor = native_executor("responses.create", monkeypatch, model)
    try:
        with (
            checker(definition, binding) as guard,
            scoped_broker(
                project,
                principal,
                broker,
                image,
                endpoint="npipe:////./pipe/synthetic-test-only",
                runtime_factory=LocalTestRuntime,
            ) as (client, _),
        ):
            result = run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run,
                tool_client=client,
                guardrail_client=guard,
            )
        assert result["status"] == "COMPLETED", result
        with api[2].state.sessions() as session:
            report = session.get(ExperimentRunRecord, run)
            reports = [row["security_evaluation"] for row in report.results["models"]]
            for row in reports:
                assert row["runtime_guardrails"]["policy_decisions_known"]
                assert row["runtime_guardrails"]["coverage_complete"] is (not deny)
                assert row["runtime_guardrails"]["benign_family_interruption"]["value"] == int(deny)
                assert (
                    row["benign_utility"]["value"]
                    == row["attacked_utility"]["value"]
                    == int(not deny)
                )
                assert row["attack_success"]["value"] == 0
                assert row["guardrail_false_positive_rate"] is None
                assert row["gate"] == "insufficient_evidence"
            operations = session.scalars(
                select(ToolOperationRecord).where(ToolOperationRecord.run_id == run)
            ).all()
            assert sum(row.state_changed is True for row in operations) == 4
            assert sum(row.result_delivery == "WITHHELD" for row in operations) == (
                4 if deny else 0
            )
            assert report.known_cost_requests == len(calls) == (9 if deny else 13)
            episodes = session.scalars(
                select(ExecutionEpisode).where(ExecutionEpisode.run_id == run)
            ).all()
            assert len(episodes) == 4 and all(
                row.summary["containment_confirmed"] for row in episodes
            )
            public = json.dumps(report.results)
            assert all(value not in public for value in canaries.values())
            assert "OWNED_UNMATCHED" not in public
        exported = api[0].get(base + "/export", headers=api[1])
        assert exported.status_code == 200 and all(
            value not in exported.text for value in canaries.values()
        )
    finally:
        executor.close()


def test_fail_open_can_finish_business_but_cannot_certify_guarded_suite(
    api, tenant, tmp_path, monkeypatch
):
    _, store, run, project, principal, broker, image, _, _, _, definition, scope = security_run(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        guarded=True,
        guard_term="OWNED_UNMATCHED_OUTPUT_CANARY",
        guard_mode="fail_open",
    )
    binding = GuardrailBinding.model_validate(scope)
    executor = native_executor(
        "responses.create",
        monkeypatch,
        lambda request: httpx.Response(200, json=scripted_security_reply(request)),
    )
    try:
        with (
            checker(definition, binding, outage=True) as guard,
            scoped_broker(
                project,
                principal,
                broker,
                image,
                endpoint="npipe:////./pipe/synthetic-test-only",
                runtime_factory=LocalTestRuntime,
            ) as (client, _),
        ):
            result = run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run,
                tool_client=client,
                guardrail_client=guard,
            )
        assert result["status"] == "COMPLETED", result
        with api[2].state.sessions() as session:
            report = session.get(ExperimentRunRecord, run)
            for model in report.results["models"]:
                security = model["security_evaluation"]
                assert (
                    security["benign_utility"]["value"]
                    == security["attacked_utility"]["value"]
                    == 1
                )
                assert security["runtime_guardrails"]["policy_decisions_known"] is False
                assert security["runtime_guardrails"]["unknown_policy_case_executions"] == 2
                assert security["runtime_guardrails"]["benign_family_interruption"]["value"] is None
                assert model["quality"]["value"] is None
            episodes = session.scalars(
                select(ExecutionEpisode).where(ExecutionEpisode.run_id == run)
            ).all()
            assert len(episodes) == 4
            assert all(
                row.summary["status"] == "unknown" and row.summary["score"] is None
                for row in episodes
            )
            assert report.known_cost_requests == 13
    finally:
        executor.close()
