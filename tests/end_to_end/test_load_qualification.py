import asyncio
import json
import secrets
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from starlette.responses import JSONResponse
from test_pipeline import server

from cutback.load_qualification import LocalLoadProfile, qualify_local
from cutback.server.api import create_app
from cutback.server.db import Project


@pytest.mark.postgres
@pytest.mark.parametrize(
    "mode", ["success", "span-outage", "cleanup-outage", "lost-create-ack", "strict-slo"]
)
def test_declared_local_load_envelope_uses_real_http_pg_isolates_tenants_and_cleans_up(
    postgres_url, mode
):
    secret = secrets.token_urlsafe(48)
    app = create_app(database_url=postgres_url, admin_token=secret, allow_insecure_http=True)
    concurrent = {"active": 0, "peak": 0}
    fault = {"mode": mode}

    @app.middleware("http")
    async def observe_actual_ingest_concurrency(request, call_next):
        if request.url.path.startswith("/v1/ingest/"):
            concurrent["active"] += 1
            concurrent["peak"] = max(concurrent["active"], concurrent["peak"])
            try:
                await asyncio.sleep(0.01)
                return await call_next(request)
            finally:
                concurrent["active"] -= 1
        return await call_next(request)

    @app.middleware("http")
    async def fail_owned_requests(request, call_next):
        if (
            fault["mode"] == "span-outage"
            and request.url.path == "/v1/ingest/spans"
            or fault["mode"] == "cleanup-outage"
            and request.method == "DELETE"
        ):
            return JSONResponse({"detail": "OWNED_FAILURE_BODY_MUST_NOT_APPEAR"}, status_code=500)
        response = await call_next(request)
        if (
            fault["mode"] == "lost-create-ack"
            and request.method == "POST"
            and request.url.path == "/v1/projects"
        ):
            return JSONResponse({"detail": "OWNED_FAILURE_BODY_MUST_NOT_APPEAR"}, status_code=500)
        return response

    profile = LocalLoadProfile(
        events=200,
        batch_size=50,
        concurrency=4,
        query_requests=4,
        minimum_latency_samples=4,
        p95_ingest_ms_ceiling=60000,
        p95_query_ms_ceiling=60000,
        minimum_unique_events_per_second=0.00001,
    )
    with server(app) as endpoint:
        if mode == "strict-slo":
            profile.p95_query_ms_ceiling = 0.000001
        receipt = asyncio.run(qualify_local(endpoint, secret, profile))
        assert receipt["provider_calls"] == 0
        assert receipt["cleanup_complete"] == (mode not in {"cleanup-outage", "lost-create-ack"})
        assert receipt["viewer_cross_tenant_denial_verified"] == (mode != "lost-create-ack")
        assert receipt["client_inflight_peak"] <= profile.concurrency
        raw = json.dumps(receipt)
        assert secret not in raw and "OWNED_FAILURE_BODY_MUST_NOT_APPEAR" not in raw
        if mode in {"cleanup-outage", "lost-create-ack"}:
            assert receipt["status"] == "FAILED_OR_INCOMPLETE_QUALIFICATION"
            assert not receipt["cleanup_complete"]
            fault["mode"] = "off"
            with app.state.sessions() as session:
                owned = session.scalars(
                    select(Project).where(
                        Project.id.in_([UUID(r["project_id"]) for r in receipt["cleanup"]])
                        | Project.slug.in_(receipt["unresolved_creation_slugs"])
                    )
                ).all()
                assert len(owned) == (1 if mode == "lost-create-ack" else 2)
                with httpx.Client(
                    base_url=endpoint,
                    headers={"Authorization": "Bearer " + secret},
                    trust_env=False,
                ) as control:
                    for row in owned:
                        assert (
                            control.delete(
                                f"/v1/projects/{row.id}", params={"confirm_slug": row.slug}
                            ).status_code
                            == 200
                        )
        with app.state.sessions() as session:
            assert not session.scalars(
                select(Project.id).where(
                    Project.id.in_([UUID(r["project_id"]) for r in receipt["cleanup"]])
                    | Project.slug.in_(receipt["unresolved_creation_slugs"])
                )
            ).all()
        if mode == "span-outage":
            assert receipt["status"] == "FAILED_OR_INCOMPLETE_QUALIFICATION"
            assert receipt["errors"] == {"LOAD_HTTP_500": 1}
            assert receipt["unique_events_acknowledged"] > 0
            assert receipt["root_spans_acknowledged"] == 0
            assert receipt["latency"]["trace_query"]["p95_ms"] is None
        elif mode == "strict-slo":
            assert receipt["status"] == "FAILED_OR_INCOMPLETE_QUALIFICATION"
            assert receipt["aggregate_counts_and_unknown_cost_verified"]
            assert not receipt["declared_gates"]["trace_query_p95"]
        elif mode == "success":
            assert receipt["status"] == "PASSED_DECLARED_LOCAL_ENVELOPE", receipt["errors"]
            assert (
                receipt["unique_events_acknowledged"]
                == receipt["duplicate_replays_acknowledged"]
                == 200
            )
            assert receipt["aggregate_counts_and_unknown_cost_verified"]
            assert all(len(v) == 4 for v in receipt["raw_client_request_latencies_ns"].values())
            assert 2 <= concurrent["peak"] <= profile.concurrency
