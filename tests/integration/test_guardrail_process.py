"""Real private-pipe child and loopback HTTP; no provider transport or database."""

import asyncio
from uuid import uuid4

from unit.test_guardrails import policy

from cutback.sample_app.guardrail_service import owned_guardrail_service


def test_separate_process_sync_async_replay_conflict_outage_and_secret_isolation(monkeypatch):
    # Boot explicitly rejects this key. Startup therefore also proves the owned
    # child did not inherit the parent's unrelated application credentials.
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-parent-key-never-forward")
    monkeypatch.setenv("CUTBACK_DATABASE_URL", "synthetic-parent-database-never-forward")
    definition = policy()
    with owned_guardrail_service(definition, workflow="owned-record-task") as service:
        assert service.client.identity() == service.binding
        operation = uuid4()
        known = service.client.check(operation_id=operation, stage="input", content="benign")
        assert known.status == "allowed"
        assert (
            service.client.check(operation_id=operation, stage="input", content="benign") == known
        )
        assert (
            service.client.check(operation_id=operation, stage="input", content="changed").code
            == "GUARDRAIL_OPERATION_CONFLICT"
        )
        assert (
            service.client.check(
                operation_id=uuid4(), stage="output", content="SYNTHETIC_DENIED_LITERAL_OWNED"
            ).status
            == "blocked"
        )

        async def check():
            client = service.async_client()
            try:
                allowed = await client.check(
                    operation_id=uuid4(), stage="input", content={"owned": "benign"}
                )
                assert allowed.status == "allowed"
                return client
            finally:
                await client.close()

        asyncio.run(check())
        assert "synthetic-parent" not in repr(service)
    assert (
        service.client.check(operation_id=uuid4(), stage="input", content="benign").code
        == "GUARDRAIL_SCOPE_DENIED"
    )
