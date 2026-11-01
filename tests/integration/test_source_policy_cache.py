"""Real PG/encrypted cache authority with a single routed source-policy contract."""

import json

import pytest
from support_http import owned_collector
from test_native_cache_runtime import runtime_configuration
from test_native_cache_workflow import prepared

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.workspace import write_private
from cutback.evals.experiments import ModelSpec
from cutback.source_policy import responses_create

pytestmark = pytest.mark.postgres


def test_same_policy_preserves_native_results_cache_bindings_and_current_rights(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    client.max_retries = 2
    try:
        with owned_collector(api[2]) as endpoint:
            configuration, _ = runtime_configuration(cache, endpoint)
            baseline = ModelSpec(
                key="reference",
                provider="openai",
                model="gpt-4.1",
                instruction="Owned synthetic task",
                max_output_tokens=64,
            )
            policy = SourceImprovementPolicy(
                workflow=cache.scope.workflow,
                max_input_bytes=128,
                baseline=baseline,
                strategy=ModelSpec(
                    key="input-size",
                    provider="router",
                    model="input-size-v1",
                    routes=[
                        {
                            "max_input_bytes": 64,
                            "target": baseline.model_copy(
                                update={
                                    "key": "cheap",
                                    "model": "gpt-4.1-mini",
                                    "max_output_tokens": 32,
                                }
                            ),
                        },
                        {"max_input_bytes": 128, "target": baseline},
                    ],
                ),
                cache_execution=configuration["contract"],
            )
            configuration["scope"]["prompt_revision"] = policy.policy_sha256
            path = tmp_path / "private-policy-cache.json"
            write_private(path, configuration)
            first = responses_create(client, "owned short", policy, scope_file=path)
            hit = responses_create(client, "owned short", policy, scope_file=path)
            assert first.output_text == hit.output_text and len(calls) == 1
            responses_create(client, "x" * 65, policy, scope_file=path)
            responses_create(client, "x" * 65, policy, scope_file=path)
            assert len(calls) == 2
            assert [json.loads(call.content)["model"] for call in calls] == [
                "gpt-4.1-mini",
                "gpt-4.1",
            ]
            assert client.max_retries == 2 and not client._client.is_closed
            changed = policy.model_dump(mode="json")
            changed["strategy"]["routes"][0]["target"]["instruction"] = "Changed private revision"
            with pytest.raises(ValueError, match="PRIVATE_RUNTIME_CONFIGURATION_INVALID"):
                responses_create(
                    client,
                    "owned short",
                    SourceImprovementPolicy.model_validate(changed),
                    scope_file=path,
                )
            assert len(calls) == 2
            response = api[0].delete(
                f"/v1/projects/{tenant[0]}/capture-policies/{cache.scope.policy_id}", headers=api[1]
            )
            assert response.status_code == 200
            with pytest.raises(ValueError, match="CURRENT_POLICY_AND_SCOPED_KEY_REQUIRED"):
                responses_create(client, "owned short", policy, scope_file=path)
            assert len(calls) == 2
    finally:
        client.close()
