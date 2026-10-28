"""Independent gateway fixtures; credentials and provider responses are synthetic."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from cutback.core.native_models import NativeModelContract
from cutback.gateway.contracts import (
    GatewayPolicy,
    ModelLimit,
    ResponsesText,
    SpendLimit,
    TeamLimit,
)
from cutback.server.db import Environment


def prepare_gateway(api, tenant, *, cap="10", concurrency=8, actor=None):
    client, admin, app = api
    project = UUID(tenant[0])
    with app.state.sessions() as session:
        environment = session.scalar(
            select(Environment.id).where(Environment.project_id == project)
        )
    now = datetime.now(UTC)
    team = uuid4()
    limit = SpendLimit(modeled_usd=cap, requests=20, concurrent_requests=concurrency)
    specification = GatewayPolicy(
        policy_id=uuid4(),
        environment_ids=[environment],
        starts_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(hours=1),
        project_limit=limit,
        teams=[TeamLimit(team_id=team, limit=limit)],
        models=[
            ModelLimit(
                price_contract=NativeModelContract.current_luna_text(reasoning_effort="medium"),
                limit=limit,
            )
        ],
    )
    headers = actor or admin
    path = f"/v1/projects/{project}/gateway"
    response = client.post(
        path + "/policies", headers=headers, json=specification.model_dump(mode="json")
    )
    assert response.status_code == 201, response.text
    policy = response.json()
    approved = client.post(
        path + f"/policies/{policy['id']}/approve",
        headers=headers,
        json={
            "approval_hash": policy["approval_hash"],
            "approval_ref": str(uuid4()),
            "expected_state_version": policy["state_version"],
        },
    )
    assert approved.status_code == 200, approved.text
    policy = approved.json()
    return {
        "path": path,
        "policy": policy,
        "specification": specification,
        "project": project,
        "environment": environment,
        "team": team,
        "headers": headers,
        "app": app,
        "client": client,
    }


def issue_gateway_key(context, *, limit=None, traffic_kind="application"):
    policy, specification = context["policy"], context["specification"]
    response = context["client"].post(
        context["path"] + "/keys",
        headers=context["headers"],
        json={
            "request_id": str(uuid4()),
            "policy_id": policy["id"],
            "environment_id": str(context["environment"]),
            "team_id": str(context["team"]),
            "traffic_kind": traffic_kind,
            "expires_at": specification.expires_at.isoformat(),
            "limit": (limit or specification.project_limit).model_dump(mode="json"),
            "model_keys": [specification.models[0].key],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def gateway_request():
    return ResponsesText(
        model="gpt-6-luna",
        input="owned synthetic private phrase",
        max_output_tokens=1024,
        store=False,
        service_tier="default",
    )


def native_response():
    return {
        "id": "resp_gateway_owned",
        "object": "response",
        "created_at": 1,
        "model": "gpt-6-luna",
        "status": "completed",
        "service_tier": "default",
        "output": [
            {
                "id": "msg_owned",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "A", "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "input_tokens_details": {
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "cache_write_tokens_details": {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0},
            },
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }
