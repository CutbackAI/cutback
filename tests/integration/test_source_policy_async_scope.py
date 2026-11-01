"""An async source cannot authorize an unsupported cached runtime or persist one."""

from uuid import UUID, uuid4

import pytest
from integration.test_source_policy_execution import prepared
from sqlalchemy import func, select

from cutback.core.policy_execution import SourcePolicyExecutionSpec
from cutback.core.policy_producer import ASYNC_PROGRAM_SHA256, PolicySourceBinding
from cutback.server.db import PayloadSample

pytestmark = pytest.mark.postgres


def test_async_binding_refuses_cached_import_and_plan_before_inference(
    api, tenant, tmp_path, monkeypatch
):
    base, _, _, _, executor, calls, plan, _, imports = prepared(
        api, tenant, tmp_path, monkeypatch, cached=False, routed=False
    )
    try:
        raw = plan.source_policy_execution.source.model_dump(mode="json")
        raw["program_sha256"] = ASYNC_PROGRAM_SHA256
        source = PolicySourceBinding.model_validate(raw)
        body = {**imports[0], "request_id": str(uuid4()), "source": source.model_dump(mode="json")}
        body["policy"] = {
            **body["policy"],
            "cache_execution": {
                "freshness_ref": "owned-async-unqualified",
                "ttl_seconds": 60,
                "content_retention_authorized": True,
                "immutable_or_safely_invalidated": True,
                "operation_cost_upper_usd": "0.000001",
                "operation_cost_basis_ref": "owned-synthetic",
            },
        }
        with api[2].state.sessions() as session:
            before = session.scalar(
                select(func.count())
                .select_from(PayloadSample)
                .where(PayloadSample.project_id == UUID(tenant[0]))
            )
        response = api[0].post(base + "/source-policies", headers=api[1], json=body)
        assert (
            response.status_code == 409 and "CURRENT_SCOPED_POLICY_IMPORT_REQUIRED" in response.text
        )
        with api[2].state.sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(PayloadSample)
                    .where(PayloadSample.project_id == UUID(tenant[0]))
                )
                == before
            )
        execution = plan.source_policy_execution.model_dump(mode="json")
        execution["source"] = source.model_dump(mode="json")
        execution["candidates"][0]["cache_enabled"] = True
        execution["candidates"][0]["cache_operation_upper_usd"] = "0.000001"
        with pytest.raises(ValueError, match="DISTINCT_SOURCE_BOUND_REFERENCES"):
            SourcePolicyExecutionSpec.model_validate(execution)
        assert calls == []
    finally:
        executor.close()
