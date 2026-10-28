from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_prompt_registry import create_prompt, post_version, version_body
from test_prompt_registry import prompt_setup as prompt_setup

pytestmark = pytest.mark.postgres


def test_metadata_observations_are_env_scoped_idempotent_and_not_coverage_claims(
    api, tenant, prompt_setup
):
    client, admin, app = api
    base, policy, creds = prompt_setup
    prompt = create_prompt(client, creds[0][1], base)
    version = post_version(client, creds[0][1], base, prompt, version_body(policy)).json()
    observation = {
        "observation_id": str(uuid4()),
        "version_id": version["id"],
        "version_hash": version["document_hash"],
        "environment": "staging",
        "workflow": "classify",
        "consumer": "synthetic-service",
        "release": "synthetic-v1",
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    path = "/v1/ingest/prompt-observations"
    assert (
        client.post(path, headers=tenant[2], json={"observations": [observation]}).status_code
        == 403
    )
    bad = client.post(
        path,
        headers=tenant[1],
        json={"observations": [{**observation, "prompt": "PRIVATE_NOT_ALLOWED"}]},
    )
    assert bad.status_code == 422 and "PRIVATE_NOT_ALLOWED" not in bad.text
    assert client.post(
        path,
        headers=tenant[1],
        json={"observations": [{**observation, "environment": "production"}]},
    ).status_code in {403, 422}
    accepted = client.post(
        path, headers=tenant[1], json={"observations": [observation, observation]}
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["accepted"] == 1 and accepted.json()["duplicates"] == 1
    assert (
        client.post(
            path,
            headers=tenant[1],
            json={"observations": [{**observation, "release": "conflicting"}]},
        ).status_code
        == 409
    )
    impact = client.get(base + "/prompts/" + prompt["id"] + "/impact", headers=tenant[2])
    assert impact.status_code == 200, impact.text
    assert (
        impact.json()["usage_coverage"] == "unknown"
        and impact.json()["unobserved_consumers"] == "unknown"
    )
    assert len(impact.json()["items"]) == 1
    assert (
        client.post(
            base + "/prompts/resolve/synthetic-prompt",
            headers=tenant[1],
            json={
                "version_id": version["id"],
                "environment": "staging",
                "workflow": "classify",
                "provider": "local",
                "purpose": "synthetic-test",
            },
        ).status_code
        == 403
    )
