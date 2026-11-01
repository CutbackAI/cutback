"""Exact source -> real SDK/cache -> HTTP collector -> PostgreSQL -> metadata report."""

import json
import runpy
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from openai.types.responses import Response
from sqlalchemy import select
from support_http import owned_collector
from support_policy_application import application
from test_native_cache_runtime import runtime_configuration
from test_native_cache_workflow import prepared

import cutback
from cutback.application_checks import git
from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.workspace import write_private
from cutback.instrumentation import workflow
from cutback.instrumentation.attribution import build_source_map
from cutback.scanner import scan
from cutback.server.db import SDKHealth, StoredEvent
from cutback.server.policy_activity_db import PolicyActivityRecord

pytestmark = pytest.mark.postgres


def test_real_source_attribution_cache_hits_inactive_scope_and_revocation_preserve_native_results(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    root = application(tmp_path)
    observer = None
    client.max_retries = 2
    try:
        with owned_collector(api[2]) as endpoint:
            configuration, _ = runtime_configuration(cache, endpoint)
            raw = json.loads((root / "policy.json").read_text("utf-8"))
            raw.update(workflow="classify", cache_execution=configuration["contract"])
            policy = SourceImprovementPolicy.model_validate(raw)
            (root / "policy.json").write_bytes(policy.configuration_bytes())
            git(root, "add", "policy.json")
            git(
                root,
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=synthetic@example.invalid",
                "commit",
                "-m",
                "Independent cache activity fixture",
            )
            configuration["scope"]["prompt_revision"] = policy.policy_sha256
            private = tmp_path / "owned-private-runtime.json"
            write_private(private, configuration)
            directory = root / ".cutback"
            directory.mkdir()
            mapping = directory / "source-map.json"
            inventory = scan(root)
            site = next(
                value for value in inventory["call_sites"] if value["source_file"] == "app.py"
            )
            mapping.write_text(json.dumps(build_source_map(root, inventory)), encoding="utf-8")
            namespace = runpy.run_path(str(root / "app.py"))
            monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", tenant[1]["Authorization"][7:])
            monkeypatch.setenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", str(private))
            observer = cutback.init(
                service_name="owned-cache-activity",
                environment="staging",
                enabled=True,
                endpoint=endpoint + "/v1/ingest/events",
                project_id=tenant[0],
                source_map=str(mapping),
                observe_policy_runtime=True,
                timeout_seconds=2,
            )
            start = datetime.now(UTC) - timedelta(seconds=1)
            with workflow(policy.workflow):
                first = namespace["classify"](client, "PRIVATE_SYNTHETIC_CACHE_INPUT")
                hit = namespace["classify"](client, "PRIVATE_SYNTHETIC_CACHE_INPUT")
                assert type(first) is type(hit) is Response and first.output_text == hit.output_text
                assert len(calls) == 1 and client.max_retries == 2 and not client.is_closed()
                monkeypatch.delenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE")
                assert (
                    type(namespace["classify"](client, "PRIVATE_SYNTHETIC_CACHE_INPUT")) is Response
                )
                assert len(calls) == 2
                monkeypatch.setenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", str(private))
                assert (
                    api[0]
                    .delete(
                        f"/v1/projects/{tenant[0]}/capture-policies/{cache.scope.policy_id}",
                        headers=api[1],
                    )
                    .status_code
                    == 200
                )
                with pytest.raises(ValueError, match="CURRENT_POLICY_AND_SCOPED_KEY_REQUIRED"):
                    namespace["classify"](client, "PRIVATE_SYNTHETIC_CACHE_INPUT")
                assert len(calls) == 2
            assert observer.flush(5)
            scope = dict(
                environment="staging",
                service_name="owned-cache-activity",
                workflow=policy.workflow,
                call_site_id=site["call_site_id"],
                source_commit=inventory["project"]["commit"],
                policy_sha256=policy.policy_sha256,
                window_start=start.isoformat(),
                window_end=(datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
            )
            response = api[0].post(
                f"/v1/projects/{tenant[0]}/policy-runtime/inspect", headers=tenant[2], json=scope
            )
            assert response.status_code == 200
            summary = response.json()
            assert summary["retained_operations"] == 4
            assert summary["states"] == {"native_miss": 1, "hit": 1, "inactive": 1, "unknown": 1}
            assert summary["native_sdk_invocations"] is summary["cache_component_upper_usd"] is None
            assert (
                summary["known_native_sdk_invocations"] == 2
                and summary["unknown_native_operations"] == 1
            )
            assert (
                summary["known_cache_operations"] == 3 and summary["unknown_cache_operations"] == 1
            )
            with api[2].state.sessions() as session:
                rows = session.scalars(
                    select(PolicyActivityRecord).where(
                        PolicyActivityRecord.project_id == UUID(tenant[0])
                    )
                ).all()
                native = session.scalars(
                    select(StoredEvent).where(StoredEvent.project_id == UUID(tenant[0]))
                ).all()
                assert len(rows) == 4 and len(native) == 2
                assert all(row.metadata_record["invoice_verified_cost_usd"] is None for row in rows)
                metadata = json.dumps([row.metadata_record for row in rows]) + response.text
                assert "PRIVATE_SYNTHETIC_CACHE_INPUT" not in metadata
                assert "PRIVATE_SYNTHETIC_CACHE_OUTPUT" not in metadata
                assert (
                    str(private) not in metadata
                    and cache.scope.token.get_secret_value() not in metadata
                )
                health = session.scalars(
                    select(SDKHealth).where(SDKHealth.project_id == UUID(tenant[0]))
                ).all()
                assert any(row.counters.get("policy_runtime_exported") == 4 for row in health)
    finally:
        if observer is not None:
            assert observer.close(5)
        client.close()
