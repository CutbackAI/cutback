"""Independent SDK-free example over real local HTTP; lost replies never create another view."""

import json
import runpy
import socket
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from support_http import owned_collector

from cutback.server.db import AuditEvent
from cutback.server.monitoring_db import SavedView

pytestmark = pytest.mark.postgres


def example():
    return runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "examples/shared-analytics/sample.py")
    )["demonstrate"]


def test_preview_is_offline_writes_nothing_and_remote_execution_is_refused(tmp_path, monkeypatch):
    action = example()

    def forbid(*args, **kwargs):
        raise AssertionError("EXAMPLE_NETWORK_FORBIDDEN")

    monkeypatch.setattr(socket, "create_connection", forbid)
    receipt = tmp_path / ".cutback/state.json"
    preview = action("http://127.0.0.1:8766", None, "owned-secret-sentinel", receipt)
    assert preview["status"] == "PREVIEW_ONLY" and preview["network_requests"] == 0
    assert not receipt.parent.exists() and "owned-secret-sentinel" not in json.dumps(preview)
    with pytest.raises(ValueError, match="OWNED_NUMERIC_LOOPBACK"):
        action(
            "https://example.invalid",
            str(uuid4()),
            "owned-secret-sentinel",
            receipt,
            execute_local=True,
        )
    assert not receipt.parent.exists()


@pytest.mark.parametrize("lost", ["save", "data"])
def test_actual_http_resume_same_metadata_intent_and_completed_receipt_never_recreates(
    api, tenant, tmp_path, lost
):
    action = example()
    receipt = tmp_path / ".cutback/state.json"
    token = api[1]["Authorization"][7:]

    class LostReply(httpx.HTTPTransport):
        def __init__(self):
            super().__init__(retries=0)
            self.failed = False

        def handle_request(self, request):
            response = super().handle_request(request)
            if not self.failed and (
                (lost == "save" and request.method == "POST")
                or (lost == "data" and request.url.path.endswith("/data"))
            ):
                self.failed = True
                response.read()
                response.close()
                raise httpx.ReadTimeout("owned lost metadata reply", request=request)
            return response

    with owned_collector(api[2]) as endpoint:
        with pytest.raises(httpx.ReadTimeout):
            action(endpoint, tenant[0], token, receipt, execute_local=True, transport=LostReply())
        state = json.loads(receipt.read_text())
        identifier = state["body"]["dashboard_id"]
        result = action(endpoint, tenant[0], token, receipt, execute_local=True)
        assert result["status"] == "COMPLETE" and result["dashboard_id"] == identifier
        assert result["provider_calls_added"] == 0 and token not in receipt.read_text()
        with api[2].state.sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(SavedView)
                    .where(SavedView.project_id == tenant[0])
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.project_id == tenant[0],
                        AuditEvent.action == "analytics_dashboard_updated",
                    )
                )
                == 1
            )
        deleted = api[0].delete(
            f"/v1/projects/{tenant[0]}/analytics-dashboards/{identifier}",
            headers=api[1],
            params={"expected_revision": 1},
        )
        assert deleted.status_code == 200
        # The completed local receipt is explicitly stale; it performs no HTTP or recreation.
        cached = action(endpoint, tenant[0], token, receipt, execute_local=True)
        assert (
            cached["network_requests"] == 0
            and cached["source_freshness"] == "saved_receipt_not_rechecked"
        )
        assert (
            api[0]
            .get(f"/v1/projects/{tenant[0]}/analytics-dashboards/{identifier}", headers=api[1])
            .status_code
            == 404
        )


def test_lost_save_followed_by_explicit_deletion_cannot_resurrect_the_old_uuid(
    api, tenant, tmp_path
):
    action = example()
    receipt = tmp_path / ".cutback/state.json"
    token = api[1]["Authorization"][7:]

    class LostSave(httpx.HTTPTransport):
        def handle_request(self, request):
            response = super().handle_request(request)
            if request.method == "POST":
                response.read()
                response.close()
                raise httpx.ReadTimeout("owned lost save", request=request)
            return response

    with owned_collector(api[2]) as endpoint:
        with pytest.raises(httpx.ReadTimeout):
            action(endpoint, tenant[0], token, receipt, execute_local=True, transport=LostSave())
        identifier = json.loads(receipt.read_text())["body"]["dashboard_id"]
        path = f"/v1/projects/{tenant[0]}/analytics-dashboards/{identifier}"
        assert (
            api[0].delete(path, headers=api[1], params={"expected_revision": 1}).status_code == 200
        )
        with pytest.raises(ValueError, match="CURRENT_RIGHTS_OR_STATE_REFUSED"):
            action(endpoint, tenant[0], token, receipt, execute_local=True)
        assert api[0].get(path, headers=api[1]).status_code == 404
