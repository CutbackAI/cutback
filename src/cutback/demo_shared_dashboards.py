"""Independent synthetic metadata dashboard seeded through the real scoped API."""

from uuid import uuid4


def seed_shared_dashboard(client, project, workflow):
    base = "/v1/projects/" + project
    views = []
    for key, page in [("support-team-traces", "traces"), ("support-team-incidents", "incidents")]:
        result = client.post(
            base + "/saved-views",
            json={
                "view_id": str(uuid4()),
                "key": key,
                "expected_revision": 0,
                "environment": "staging",
                "page": page,
                "filter": {"workflow": workflow},
                "incident_state": "open" if page == "incidents" else None,
            },
        )
        result.raise_for_status()
        views.append(result.json())
    result = client.post(
        base + "/dashboards",
        json={
            "dashboard_id": str(uuid4()),
            "key": "support-team-overview",
            "expected_revision": 0,
            "environment": "staging",
            "panels": [{"view_id": row["id"], "revision": row["revision"]} for row in views],
        },
    )
    result.raise_for_status()
    dashboard = result.json()
    trace = client.get(
        base + "/dashboards/" + dashboard["id"] + "/panels/" + views[0]["id"],
        params={"expected_revision": dashboard["revision"]},
    )
    trace.raise_for_status()
    evidence = trace.json()
    if not evidence["items"] or evidence["content_included"] is not False:
        raise ValueError("SHARED_DASHBOARD_SAMPLE_RETAINED_METADATA_REQUIRED")
    return {
        "contract": "cutback.shared-dashboard-sample.v1",
        "dashboard_id": dashboard["id"],
        "definition_sha256": dashboard["definition_sha256"],
        "panels": len(dashboard["panels"]),
        "matching_retained_spans_verified": True,
        "content_included": False,
        "population_coverage": "unknown",
        "paid_provider_calls": 0,
        "synthetic_only": True,
    }
