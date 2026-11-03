"""A shared chart over the independent demo's already observed synthetic calls."""

from uuid import uuid4


def panels():
    return [
        {
            "key": "application-traffic",
            "metric": "observed_attempts",
            "chart": "line",
            "group_by": "day",
            "traffic_kind": "application",
        },
        {
            "key": "known-cost",
            "metric": "known_modeled_cost_subtotal_usd",
            "chart": "bar",
            "group_by": "model",
            "traffic_kind": "application",
        },
        {
            "key": "observed-errors",
            "metric": "errors",
            "chart": "stat",
            "group_by": "day",
            "traffic_kind": "application",
        },
        {
            "key": "observed-duration",
            "metric": "latency_p95_ms",
            "chart": "line",
            "group_by": "day",
            "traffic_kind": "application",
        },
    ]


def seed_analytics_dashboard(client, project):
    base = "/v1/projects/" + project + "/analytics-dashboards"
    response = client.post(
        base,
        json={
            "dashboard_id": str(uuid4()),
            "key": "support-cost-and-reliability",
            "expected_revision": 0,
            "environment": "staging",
            "window_days": 7,
            "panels": panels(),
        },
    )
    response.raise_for_status()
    saved = response.json()
    response = client.get(
        base + "/" + saved["id"] + "/data", params={"expected_revision": saved["revision"]}
    )
    response.raise_for_status()
    data = response.json()
    if (
        data["content_included"] is not False
        or data["population_coverage"] != "unknown"
        or data["panels"][0]["summary"]["observed_attempts"] < 1
    ):
        raise ValueError("ANALYTICS_SAMPLE_OBSERVED_METADATA_REQUIRED")
    return {
        "contract": "cutback.analytics-dashboard-sample.v1",
        "dashboard_id": saved["id"],
        "revision": saved["revision"],
        "definition_sha256": saved["definition_sha256"],
        "key": saved["key"],
        "panels": len(data["panels"]),
        "observed_attempts": data["panels"][0]["summary"]["observed_attempts"],
        "source": "existing_independent_demo_observations_not_extra_fabricated_usage",
        "provider_calls_added": 0,
        "synthetic_only": True,
        "content_included": False,
        "population_coverage": "unknown",
    }
