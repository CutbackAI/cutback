import json
from decimal import Decimal
from uuid import UUID

import httpx
import httpx2
import pytest
from sqlalchemy import select
from support_episode_judge import semantic_response
from support_native_agent import native_executor, response_body
from test_native_episodes import prepared_episode

from cutback.server.db import CapturePolicyRecord, ExperimentRunRecord, JobRecord
from cutback.server.execution_db import ExecutionCall, ExecutionEpisode
from cutback.server.execution_ledger import ExecutionLedger
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "mode",
    [
        "pass",
        "fail",
        "invalid",
        "outage",
        "unknown_usage",
        "revoked",
        "cancelled",
        "reservation_ack",
        "receipt_ack",
    ],
)
@pytest.mark.parametrize(
    "surface", ["responses.create", "chat.completions.create", "messages.create"]
)
def test_semantic_judge_durable_reservations_content_rbac_and_no_uncertain_retry(
    api, tenant, tmp_path, monkeypatch, mode, surface
):
    base, store, run_id, policy_id = prepared_episode(
        api, tenant, tmp_path, monkeypatch, surface, semantic=True
    )
    client, admin, app = api
    calls = []
    if mode == "reservation_ack":
        original = ExecutionLedger.reserve

        def lose_reservation(self, amount, model, purpose):
            identifier = original(self, amount, model, purpose)
            if purpose == "judge":
                raise ConnectionError("OWNED_JUDGE_RESERVATION_ACK_LOST")
            return identifier

        monkeypatch.setattr(ExecutionLedger, "reserve", lose_reservation)
    elif mode == "receipt_ack":
        original_record = ExecutionLedger.record

        def lose_receipt(self, identifier, result, status):
            original_record(self, identifier, result, status)
            with self.sessions() as session:
                if session.get(ExecutionCall, identifier).purpose == "judge":
                    raise ConnectionError("OWNED_JUDGE_RECEIPT_ACK_LOST")

        monkeypatch.setattr(ExecutionLedger, "record", lose_receipt)

    def handle(request):
        body = json.loads(request.content)
        judge = body.get("text", {}).get("format", {}).get("name") == "cutback_episode_judgments"
        calls.append(body)
        with app.state.sessions() as session:
            call = session.scalar(
                select(ExecutionCall)
                .where(ExecutionCall.run_id == run_id)
                .order_by(ExecutionCall.sequence.desc())
            )
            episode = session.get(ExecutionEpisode, call.episode_id)
            assert call.state == "reserved" and call.spent_usd is None
            assert call.reserved_usd > 0 and episode.max_requests == 4
            assert call.project_id == episode.project_id == UUID(tenant[0])
            assert call.environment_id == episode.environment_id
            assert episode.requests_used <= 4 and episode.state == "running"
            assert call.purpose == ("judge" if judge else "evaluation")
            assert call.model_key == ("semantic-judge" if judge else episode.model_key)
        if judge:
            scope = json.loads(body["input"])
            assert scope["sources"] == [{"source_id": "reference:0", "text": '["Urgent"]'}]
            if mode in {"revoked", "cancelled"}:
                with app.state.sessions.begin() as session:
                    if mode == "revoked":
                        session.get(CapturePolicyRecord, UUID(policy_id)).enabled = False
                    else:
                        session.get(ExperimentRunRecord, run_id).status = "CANCELLED"
                        session.scalar(
                            select(JobRecord).where(JobRecord.run_id == run_id)
                        ).status = "CANCELLED"
            if mode == "outage":
                return httpx.Response(503, json={"error": {"message": "PRIVATE_JUDGE_OUTAGE"}})
            response = semantic_response(response_body("responses.create"), mode)
            if mode == "unknown_usage":
                response.pop("usage")
        else:
            serialized = json.dumps(body)
            response = response_body(
                surface,
                "final"
                if any(
                    marker in serialized
                    for marker in ("function_call_output", '"role": "tool"', "tool_result")
                )
                else "tool",
            )
        transport = httpx2 if surface == "messages.create" and not judge else httpx
        return transport.Response(200, json=response)

    executor = native_executor(surface, monkeypatch, handle)
    if surface == "messages.create":
        judge_executor = native_executor("responses.create", monkeypatch, handle)
        executor.clients["openai"] = judge_executor.clients.pop("openai")
    try:
        result = run_one(
            app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
        )
        expected = (
            "CANCELLED"
            if mode == "cancelled"
            else "FAILED"
            if mode in {"revoked", "outage", "unknown_usage", "reservation_ack", "receipt_ack"}
            else "COMPLETED"
        )
        assert result["status"] == expected, result
        with app.state.sessions() as session:
            receipts = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id)
            ).all()
            run = session.get(ExperimentRunRecord, run_id)
            assert len(receipts) == (6 if expected == "COMPLETED" else 3)
            assert len(calls) == len(receipts) - int(mode == "reservation_ack")
            assert run.requests_used == len(receipts)
            assert run.known_cost_requests == len(calls) - int(mode in {"outage", "unknown_usage"})
            assert run.spent_usd == sum((c.spent_usd or Decimal(0) for c in receipts), Decimal(0))
            assert sum(c.purpose == "judge" for c in receipts) == (2 if len(calls) == 6 else 1)
        metadata = client.get(base + f"/experiments/{run_id}", headers=tenant[2])
        assert "PRIVATE_RUBRIC_CANARY" not in metadata.text
        assert "PRIVATE_JUDGE_OUTAGE" not in metadata.text
        if mode in {"reservation_ack", "receipt_ack"}:
            assert not result["results"]["billing_complete"]
        if expected == "COMPLETED":
            for row in result["results"]["models"]:
                assert row["quality"]["value"] == (
                    1 if mode == "pass" else 0 if mode == "fail" else None
                )
            path = base + f"/experiments/{run_id}/cases/paired"
            body = {"purpose": "inspect-owned-semantic-evidence"}
            assert client.post(path, headers=tenant[2], json=body).status_code == 403
            content = client.post(path, headers=admin, json=body, params={"failed_only": False})
            assert content.status_code == 200, content.text
            assert "episode_judge_output" in content.text and "reference:0" in content.text
            export = client.get(base + "/export", headers=admin)
            assert "PRIVATE_RUBRIC_CANARY" not in export.text
            with app.state.sessions.begin() as session:
                session.get(CapturePolicyRecord, UUID(policy_id)).enabled = False
            assert client.post(path, headers=admin, json=body).status_code == 403
        assert (
            run_one(
                app.state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )["status"]
            == "IDLE"
        )
    finally:
        executor.close()
