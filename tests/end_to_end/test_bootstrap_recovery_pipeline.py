"""Real TCP acknowledgement loss cannot duplicate projects, keys or inference work."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from test_pipeline import server

from cutback.agent_bootstrap import bootstrap_connection
from cutback.agent_control import Control
from cutback.server.db import AccessKey, ExperimentRunRecord

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


@pytest.mark.parametrize("lost", ["project", "project_admin", "ingest"])
def test_real_http_bootstrap_read_error_reuses_saved_credentials(
    api, tmp_path, event, caplog, lost
):
    failed = []

    def lose_ack(response):
        if failed or response.request.method != "POST":
            return
        body = json.loads(response.request.content)
        if (
            lost == "project"
            and response.request.url.path == "/v1/projects"
            or lost != "project"
            and body.get("role") == lost
        ):
            failed.append(True)
            raise httpx.ReadError(
                "synthetic committed acknowledgement lost", request=response.request
            )

    owner = str(uuid4())
    admin = api[1]["Authorization"].removeprefix("Bearer ")
    with server(api[2]) as endpoint:
        with httpx.Client(
            trust_env=False, timeout=30, event_hooks={"response": [lose_ack]}
        ) as client:
            with pytest.raises(ValueError, match="RESUME_SAVED_INTENT"):
                bootstrap_connection(tmp_path, Control(endpoint, admin, client=client), owner=owner)
        assert failed == [True]
        with httpx.Client(trust_env=False, timeout=30) as client:
            saved = bootstrap_connection(
                tmp_path, Control(endpoint, admin, client=client), owner=owner
            )
            response = client.get(
                endpoint + "/v1/projects/" + saved["project"],
                headers={"Authorization": "Bearer " + saved["token"]},
            )
            assert response.status_code == 200
            response = client.post(
                endpoint + "/v1/ingest/events",
                headers={"Authorization": "Bearer " + saved["ingest_token"]},
                json={
                    "project_id": saved["project"],
                    "events": [
                        event.model_copy(update={"traffic_kind": "simulation"}).model_dump(
                            mode="json"
                        )
                    ],
                },
            )
            assert response.status_code == 200
            assert response.json()["accepted"] == 1
        no_credential_logs = all(
            value not in caplog.text for value in (admin, saved["token"], saved["ingest_token"])
        )
        assert no_credential_logs, "BOOTSTRAP_CREDENTIAL_ENTERED_LOGS"
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AccessKey)
                .where(AccessKey.project_id == UUID(saved["project"]))
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExperimentRunRecord)
                .where(ExperimentRunRecord.project_id == UUID(saved["project"]))
            )
            == 0
        )
