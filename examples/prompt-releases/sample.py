"""Own synthetic prompt lifecycle through the ordinary authenticated HTTP API.

No provider calls, worker execution, application mutation or deployment.
Credentials come from explicitly named environment variables and are never printed.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from cutback.core.config import validate_endpoint
from cutback.prompts import PromptClient


def demonstrate(endpoint, project, token, *, transport=None):
    endpoint = validate_endpoint(endpoint)
    base = f"/v1/projects/{project}"
    key = "synthetic-prompt-" + uuid4().hex[:12]
    with httpx.Client(
        base_url=endpoint,
        headers={"Authorization": "Bearer " + token},
        transport=transport,
        trust_env=False,
        follow_redirects=False,
    ) as client:

        def post(path, body):
            response = client.post(path, json=body)
            if response.status_code not in {200, 201, 202}:
                raise ValueError("SYNTHETIC_PROMPT_STEP_FAILED")
            return response.json()

        policy = post(
            base + "/capture-policies",
            {
                "enabled": True,
                "approval_ref": key,
                "rights_basis": "synthetic",
                "environments": ["staging"],
                "workflows": [key],
                "allowed_providers": ["local"],
                "sample_rate": 1,
                "max_samples": 100,
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )["id"]
        prompt = post(base + "/prompts", {"key": key, "workflow": key})
        versions = []
        for ordinal, template in enumerate(
            ("Classify {{input}}.", "Classify {{input}}. Return exactly one label.")
        ):
            versions.append(
                post(
                    base + "/prompts/" + prompt["id"] + "/versions",
                    {
                        "version_id": str(uuid4()),
                        "expected_latest_ordinal": ordinal,
                        "policy_id": policy,
                        "environment": "staging",
                        "provenance_ref": key,
                        "document": {"text": template, "variables": {"input": "string"}},
                    },
                )
            )
        reader = post(base + "/keys", {"role": "prompt_reader", "environment": "staging"})
        post(
            base + "/prompts/" + prompt["id"] + "/read-grants",
            {
                "actor": reader["key_id"],
                "environment": "staging",
                "workflow": key,
                "policy_id": policy,
                "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
                "approval_ref": key,
            },
        )
        path = base + "/prompts/" + prompt["id"] + "/aliases"
        alias = post(
            path,
            {
                "version_id": versions[0]["id"],
                "environment": "staging",
                "alias": "staging",
                "expected_revision": 0,
                "approval_ref": key,
                "quality_unverified_acknowledged": True,
            },
        )
        with PromptClient(endpoint, project, reader["token"], transport=transport) as prompts:
            resolved = prompts.resolve(
                key,
                environment="staging",
                workflow=key,
                provider="local",
                purpose="owned-synthetic-render",
                alias="staging",
            )
            rendered = resolved.render({"input": "LOCAL_ONLY_SYNTHETIC_CASE"})
            assert rendered["text"] == "Classify LOCAL_ONLY_SYNTHETIC_CASE."
        second = post(
            path,
            {
                "version_id": versions[1]["id"],
                "environment": "staging",
                "alias": "staging",
                "expected_revision": 1,
                "expected_version_id": versions[0]["id"],
                "approval_ref": key,
                "quality_unverified_acknowledged": True,
            },
        )
        rollback = post(
            base + "/prompt-aliases/" + alias["alias"]["id"] + "/rollback",
            {"receipt_id": second["receipt"]["id"], "expected_revision": 2, "approval_ref": key},
        )
        assert rollback["alias"]["version_id"] == versions[0]["id"]
        revoke = client.delete(base + "/keys/" + reader["key_id"])
        assert revoke.status_code == 200
        return {
            "prompt_id": prompt["id"],
            "key": key,
            "versions": len(versions),
            "alias_revision": rollback["alias"]["revision"],
            "rendering": "local_variables_not_uploaded",
            "quality": "unverified_synthetic_lifecycle_only",
            "provider_calls": 0,
        }


if __name__ == "__main__":
    import json

    print(
        json.dumps(
            demonstrate(
                os.environ["CUTBACK_ENDPOINT"],
                os.environ["CUTBACK_PROJECT_ID"],
                os.environ["CUTBACK_CONTROL_TOKEN"],
            ),
            indent=2,
        )
    )
