"""Synthetic metadata frame with no SDK, server or application imports."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.policy_activity import PolicyRuntimeScope, PolicyRuntimeSummary
from cutback.core.policy_producer import PolicySourceBinding
from cutback.evals.experiments import ModelSpec


def frame(*, active=True):
    baseline = ModelSpec(
        key="reference",
        provider="openai",
        model="gpt-4.1",
        instruction="OWNED_PRIVATE_BASE_INSTRUCTION",
        max_output_tokens=64,
    )
    selected = SourceImprovementPolicy(
        workflow="owned-ticket-priority",
        max_input_bytes=128,
        baseline=baseline,
        strategy=baseline.model_copy(
            update={
                "key": "cheap",
                "model": "gpt-4.1-mini",
                "instruction": "OWNED_PRIVATE_VARIANT",
                "max_output_tokens": 32,
            }
        ),
        cache_execution={
            "freshness_ref": "owned-fixture",
            "ttl_seconds": 60,
            "content_retention_authorized": True,
            "immutable_or_safely_invalidated": True,
            "operation_cost_upper_usd": "0.000001",
            "operation_cost_basis_ref": "owned-fixture",
        },
    )
    source = PolicySourceBinding(
        producer_file="app.py",
        producer_sha256="a" * 64,
        configuration_file="policy.json",
        configuration_sha256="b" * 64,
        policy_sha256=selected.policy_sha256,
        source_tree_hash="c" * 64,
        commit="d" * 40,
        workflow=selected.workflow,
        max_input_bytes=selected.max_input_bytes,
    )
    now = datetime.now(UTC)
    scope = PolicyRuntimeScope(
        environment="staging",
        service_name="owned-reference",
        workflow=source.workflow,
        call_site_id="callsite:" + "a" * 24,
        source_commit=source.commit,
        policy_sha256=selected.policy_sha256,
        window_start=now - timedelta(minutes=10),
        window_end=now - timedelta(seconds=1),
    )
    summary = PolicyRuntimeSummary(
        scope=scope,
        project_id=uuid4(),
        environment_id=uuid4(),
        retained_operations=2,
        truncated=False,
        raw_sha256="e" * 64,
        states={"hit": 1, "native_miss": 1} if active else {"inactive": 2},
        completions={"completed": 2},
        configured_operations=2,
        requested_cache_operations=2 if active else 0,
        known_native_sdk_invocations=1 if active else 2,
        native_sdk_invocations=1 if active else 2,
        unknown_native_operations=0,
        known_cache_operations=3 if active else 0,
        cache_operations=3 if active else 0,
        unknown_cache_operations=0,
        cache_component_upper_usd="0.000003" if active else "0",
        cache_cost_basis_refs=["owned-fixture"],
    )
    return selected, source, summary, now
