"""A real HTTP response loss resumes encrypted setup without queueing inference."""

import os
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from support_agent import fresh_application
from test_pipeline import server

from cutback.agent import analyze, load_session, prepare
from cutback.agent_control import Control
from cutback.agent_preparation import resume
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import CapturePolicyRecord, DatasetRecord, ExperimentRunRecord

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_real_http_timeout_after_import_commit_resumes_without_private_logging_or_inference(
    api, tenant, tmp_path, monkeypatch, caplog
):
    root = tmp_path / "application"
    root.mkdir()
    fresh_application(root)
    analyze(root)
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    lost = []

    def lose_response(response):
        if (
            response.request.method == "POST"
            and response.request.url.path.endswith("/datasets")
            and not lost
        ):
            lost.append(True)
            raise httpx.ReadError("synthetic committed response lost", request=response.request)

    with server(api[2]) as endpoint:
        with httpx.Client(
            trust_env=False, timeout=30, event_hooks={"response": [lose_response]}
        ) as client:
            control = Control(endpoint, api[1]["Authorization"][7:], client=client)
            with pytest.raises(
                ValueError, match="COLLECTOR_RESPONSE_UNCERTAIN_INSPECT_SAVED_STATE"
            ):
                prepare(
                    root,
                    control,
                    tenant[0],
                    files=["cases.jsonl"],
                    rights_basis="synthetic",
                    rights_ref="owned-http-recovery",
                    max_usd="4",
                    max_requests=500,
                    minimum=0.9,
                    independent_labels=True,
                    min_test_samples=3,
                    automatic=True,
                )
        frozen = load_session(root)["preparation_intent"]
        assert load_session(root)["phase"] == "PREPARING" and lost == [True]
        with httpx.Client(trust_env=False, timeout=30) as client:
            control = Control(endpoint, api[1]["Authorization"][7:], client=client)
            completed = resume(root, control)
        assert completed["phase"] == "PLAN_READY" and completed["preparation_intent"] == frozen
    with api[2].state.sessions() as session:
        for table in (CapturePolicyRecord, DatasetRecord):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.project_id == UUID(tenant[0]))
                )
                == 1
            )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExperimentRunRecord)
                .where(ExperimentRunRecord.project_id == UUID(tenant[0]))
            )
            == 0
        )
    assert "Owned synthetic classification task" not in caplog.text
    assert api[1]["Authorization"] not in caplog.text
