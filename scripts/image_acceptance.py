"""Disposable Linux-image, PostgreSQL, encrypted-object and key recovery acceptance.

Uses the owned local Compose PostgreSQL service, synthetic SDK transports and no paid APIs.
Never points the collector at the base database. Does not deploy a public service.
"""

import argparse
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
import psycopg
from backup_restore import backup, compose, restore
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
PREAMBLE = "import json,os,sys\nos.environ.update(json.load(sys.stdin))\n"


def checked(command, *, source=None, timeout=180, env=None):
    result = subprocess.run(
        command, input=source, capture_output=True, text=True, timeout=timeout, env=env
    )
    if result.returncode:
        raise ValueError("IMAGE_ACCEPTANCE_COMMAND_FAILED_DETAILS_SUPPRESSED")
    return result.stdout


def inside(container, code, values):
    return checked(
        ["docker", "exec", "-i", container, "/app/.venv/bin/python", "-c", PREAMBLE + code],
        source=json.dumps(values),
    )


def seed_owned_isolated_records(
    container,
    directory,
    values,
    project,
    image_id,
    password,
    database,
    *,
    security_suite=False,
    guarded_tools=False,
    guarded_security=False,
):
    """A separate host worker/broker; the image receives no Docker socket."""
    import uvicorn

    from cutback.demo_isolated_tools import seed_isolated_tools
    from cutback.evals.artifacts import EncryptedFileStore
    from cutback.server.api import create_app

    encrypted = directory / "host-encrypted"
    encrypted.mkdir()
    checked(["docker", "cp", container + ":/tmp/encrypted/.", str(encrypted)])
    app = create_app(
        database_url=(
            "postgresql+psycopg://cutback:"
            + quote(password, safe="")
            + "@127.0.0.1:55432/"
            + database
        ),
        admin_token=values["CUTBACK_ADMIN_TOKEN"],
        allow_insecure_http=True,
    )
    app.state.artifact_store = EncryptedFileStore(
        encrypted, base64.b64decode(values["CUTBACK_PAYLOAD_KEY"])
    )
    prior = os.environ.get("CUTBACK_ALLOW_PAYLOADS")
    os.environ["CUTBACK_ALLOW_PAYLOADS"] = "true"
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        bound.listen(16)
        port = bound.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [bound]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise ValueError("OWNED_TOOL_IMAGE_WORKER_NOT_READY")
                time.sleep(0.05)
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=30,
                trust_env=False,
                headers={"Authorization": "Bearer " + values["CUTBACK_ADMIN_TOKEN"]},
            ) as control:
                receipt = seed_isolated_tools(
                    app,
                    control,
                    project,
                    image_id,
                    guarded=guarded_tools,
                    deny_tool_output=guarded_tools,
                )
                if guarded_tools:
                    assert receipt["runtime_guardrails"]["checks"] == 16
                    assert receipt["runtime_guardrails"]["withheld_operations"] == 4
                if security_suite:
                    from cutback.demo_security_suites import seed_security_suite

                    security = seed_security_suite(
                        app, control, project, image_id, guarded=guarded_security
                    )
                    assert security["sessions"] == 4 and security["cleanup_confirmed"]
            assert (
                receipt["sessions"] == receipt["state_changes"] == 4
                and receipt["cleanup_confirmed"]
            )
            # Only encrypted objects cross this owned copy. No key/token is a file.
            checked(["docker", "cp", str(encrypted) + "/.", container + ":/tmp/encrypted"])
            checked(
                [
                    "docker",
                    "exec",
                    "--user",
                    "0",
                    container,
                    "chown",
                    "-R",
                    "10001:10001",
                    "/tmp/encrypted",
                ]
            )
            return receipt
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            app.state.engine.dispose()
            if prior is None:
                os.environ.pop("CUTBACK_ALLOW_PAYLOADS", None)
            else:
                os.environ["CUTBACK_ALLOW_PAYLOADS"] = prior


def main(
    image,
    *,
    isolated_tools=False,
    security_suite=False,
    runtime_guardrails=False,
    guarded_tools=False,
    guarded_security=False,
    joint_model_context=False,
):
    from cutback.server.migrate import CURRENT_REVISION

    if guarded_tools and not (isolated_tools and runtime_guardrails):
        raise ValueError("GUARDED_TOOL_RECOVERY_REQUIRES_BOTH_OWNED_SERVICE_GATES")
    if guarded_security and not (isolated_tools and security_suite and runtime_guardrails):
        raise ValueError("GUARDED_SECURITY_RECOVERY_REQUIRES_ALL_OWNED_SERVICE_GATES")

    if security_suite and not isolated_tools:
        raise ValueError("SECURITY_IMAGE_GATE_REQUIRES_ACTUAL_ISOLATED_TOOLS")

    image_id = json.loads(checked(["docker", "image", "inspect", image]))[0]["Id"]
    password = (ROOT / "infra/compose/secrets/db_password").read_text().strip()
    kwargs = dict(host="127.0.0.1", port=55432, user="cutback", password=password, dbname="cutback")
    names = ["cutback_image_" + uuid4().hex, "cutback_recovered_" + uuid4().hex]
    container = "cutback-acceptance-" + uuid4().hex[:12]
    service = checked(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "infra/compose/docker-compose.yml"),
            "ps",
            "-q",
            "postgres",
        ]
    ).strip()
    if not service or len(service.splitlines()) != 1:
        raise ValueError("START_THE_OWNED_COMPOSE_POSTGRES_SERVICE_FIRST")
    networks = json.loads(checked(["docker", "inspect", service]))[0]["NetworkSettings"]["Networks"]
    candidates = [
        name for name, value in networks.items() if "postgres" in (value.get("Aliases") or [])
    ]
    if len(candidates) != 1:
        raise ValueError("OWNED_POSTGRES_NETWORK_NOT_UNIQUE")
    network = candidates[0]
    admin = secrets.token_urlsafe(48)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        for name in names:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    server_process = None
    try:
        with tempfile.TemporaryDirectory(prefix="cutback-image-acceptance-") as temporary:
            directory = Path(temporary)
            values = {
                "CUTBACK_SEMANTIC_CACHE_ENABLED": "true",
                "CUTBACK_DATABASE_URL": (
                    f"postgresql+psycopg://cutback:{quote(password, safe='')}"
                    f"@postgres:5432/{names[0]}"
                ),
                "CUTBACK_ADMIN_TOKEN": admin,
                "CUTBACK_PAYLOAD_KEY": base64.b64encode(os.urandom(32)).decode(),
            }
            # Secrets cross an anonymous stdin pipe, never a host file, command argument,
            # Docker Config.Env entry or captured console output.
            server_process = subprocess.Popen(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--name",
                    container,
                    "--network",
                    network,
                    "-p",
                    f"127.0.0.1:{port}:8080",
                    "-e",
                    "CUTBACK_PAYLOAD_DIR=/tmp/encrypted",
                    "-e",
                    "CUTBACK_ALLOW_PAYLOADS=true",
                    "-e",
                    "CUTBACK_DEV_HTTP=true",
                    image_id,
                    "/app/.venv/bin/python",
                    "-c",
                    PREAMBLE + "from cutback.server.boot import main; main()",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            server_process.stdin.write(json.dumps(values))
            server_process.stdin.close()
            endpoint = f"http://127.0.0.1:{port}"
            with httpx.Client(
                base_url=endpoint,
                trust_env=False,
                timeout=30,
                headers={"Authorization": "Bearer " + admin},
            ) as client:
                for _ in range(100):
                    try:
                        if client.get("/v1/health/ready").is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise ValueError("IMAGE_NOT_READY")
                code = """import json,os,sys
from pathlib import Path
from cutback.server.runtime import configure_secrets
from cutback.server.api import create_app
from cutback.evals.artifacts import configured_store
from cutback.demo import seed_support_desk
configure_secrets()
assert os.getuid()==10001 and sys.version_info[:3]==(3,11,18)
app=create_app(allow_insecure_http=True)
app.state.artifact_store=configured_store()
os.environ["CUTBACK_SEMANTIC_CACHE_ENABLED"]="true"
manifest=seed_support_desk(app,"http://127.0.0.1:8080",os.environ["CUTBACK_ADMIN_TOKEN"],Path("/tmp/owned-support-desk"),provider_batch=True,semantic_cache=True,runtime_guardrails=RUNTIME_GUARDS,joint_model_context=JOINT_EXPECTED)
if JOINT_EXPECTED:
 assert manifest["joint_model_context_sample"]["native_sdk_calls"]>0
 assert manifest["joint_model_context_sample"]["paid_provider_calls"]==0
assert manifest["provider_batch_sample"]["status"]=="COMPLETED"
assert manifest["provider_batch_sample"]["vendor_files_deleted"] is True
assert manifest["semantic_cache_sample"]["status"]=="PASSED"
assert manifest["semantic_cache_sample"]["served_authorized_hit"] is True
assert manifest["semantic_cache_sample"]["rejected_unsafe_match"] is True
if RUNTIME_GUARDS:
 assert manifest["runtime_guardrail_sample"]["status"]=="PASSED"
 assert manifest["runtime_guardrail_sample"]["native_fixture_calls"]==4
 assert manifest["runtime_guardrail_sample"]["complete_application_cost_usd"] is None
print(json.dumps({"project_id":manifest["project_id"],"paid_provider_calls":manifest["paid_provider_calls"]}))
"""
                code = code.replace("RUNTIME_GUARDS", repr(runtime_guardrails))
                code = code.replace("JOINT_EXPECTED", repr(joint_model_context))
                manifest = json.loads(inside(container, code, values).strip().splitlines()[-1])
                base = "/v1/projects/" + manifest["project_id"]
                usage = client.get(
                    base + "/usage?traffic_kind=application&group_by=workflow"
                ).json()
                assert usage["summary"]["observed_attempts"] == 19 + 4 * runtime_guardrails
                assert len(usage["groups"]) == 3 + runtime_guardrails
                source_groups = [group for group in usage["groups"] if "app.py" in group["label"]]
                assert len(source_groups) == 2
                assert sum(group["observed_attempts"] for group in source_groups) == 12
                assert client.get(base + "/changes").json()
                if isolated_tools:
                    seed_owned_isolated_records(
                        container,
                        directory,
                        values,
                        manifest["project_id"],
                        image_id,
                        password,
                        names[0],
                        security_suite=security_suite,
                        guarded_tools=guarded_tools,
                        guarded_security=guarded_security,
                    )
                with psycopg.connect(**kwargs, autocommit=True) as conn:
                    key = os.urandom(32)
                    dump = directory / "database.cbk"
                    backup(dump, key, compose("pg_dump", "-U", "cutback", "-d", names[0], "-Fc"))
                    restore(
                        dump,
                        key,
                        compose("pg_restore", "-U", "cutback", "-d", names[1], "--exit-on-error"),
                    )
                recovery = (
                    """import json,os,shutil
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.engine import make_url
from cutback.server.runtime import configure_secrets
from cutback.server.db import database,PayloadSample,ExperimentPlanRecord
from cutback.server.db import ExperimentRunRecord,DatasetRecord
from cutback.server.provider_batch_db import ProviderBatchRecord
from cutback.server.enterprise_db import CacheEntry
from cutback.server.semantic_cache_db import SemanticCacheNamespace,SemanticCacheQualification
from cutback.server.semantic_upload_db import SemanticDatasetUpload
from cutback.server.tool_db import ToolSessionRecord,ToolOperationRecord
from cutback.server.semantic_cache_service import read_catalog
from cutback.core.semantic_cache import SemanticCacheIndex
from cutback.evals.datasets import digest
from cutback.evals.artifacts import configured_store,EncryptedFileStore
configure_secrets()
original=configured_store()
destination=Path("/tmp/restored-encrypted")
shutil.copytree(original.root,destination)
old_key=__import__("base64").b64decode(os.environ["CUTBACK_PAYLOAD_KEY"])
new_key=os.urandom(32)
rotating=EncryptedFileStore(destination,new_key,previous_keys=(old_key,))
new_only=EncryptedFileStore(destination,new_key)
url=make_url(os.environ["CUTBACK_DATABASE_URL"]).set(database=RECOVERED)
engine,sessions=database(url.render_as_string(hide_password=False))
with sessions() as session:
 rows=session.scalars(select(PayloadSample)).all()
 assert rows
 for row in rows:
  before=original.get(row.project_id,row.object_ref)
  rotating.rekey(row.project_id,row.object_ref)
  assert new_only.get(row.project_id,row.object_ref)==before
 caches=session.scalars(select(CacheEntry)).all()
 assert caches
 for row in caches:
  before=original.get(row.project_id,row.object_ref)
  rotating.rekey(row.project_id,row.object_ref)
  assert new_only.get(row.project_id,row.object_ref)==before
 namespaces=session.scalars(select(SemanticCacheNamespace)).all()
 assert len(namespaces)==1 and namespaces[0].state=="ACTIVE"
 os.environ["CUTBACK_ALLOW_PAYLOADS"]="true"
 anchors,_,spec=read_catalog(session,new_only,namespaces[0])
 qualification=session.get(SemanticCacheQualification,namespaces[0].active_qualification_id)
 assert qualification.state=="PASSED" and qualification.report["confirmation"]["passed"] is True
 index=SemanticCacheIndex(spec.rule.model_copy(update={"threshold":qualification.selected_threshold}),anchors)
 assert index.lookup([1.0,0.0]).hit and not index.lookup([0.8,0.6]).hit
 uploads=session.scalars(select(SemanticDatasetUpload)).all()
 assert len(uploads)==1 and uploads[0].state=="FINALIZED"
 batches=session.scalars(select(ProviderBatchRecord)).all()
 assert len(batches)==1 and batches[0].state=="COMPLETED"
 assert batches[0].report["billing_complete"] is True
 assert all(part.get("cleanup")=="deleted" for part in batches[0].pieces)
 plans=session.scalars(select(ExperimentPlanRecord)).all()
 tools=session.scalars(select(ToolSessionRecord)).all()
 operations=session.scalars(select(ToolOperationRecord)).all()
 if TOOLS_EXPECTED:
  assert len(tools)==(8 if SECURITY_EXPECTED else 4)
  assert all(row.state=="CLOSED" and row.cleanup_confirmed for row in tools)
  assert len(operations)==(15 if SECURITY_EXPECTED else 8)
  assert all(row.state=="RETURNED" for row in operations)
  assert sum(row.state_changed is True for row in operations)==(7 if SECURITY_EXPECTED else 4)
 if SECURITY_EXPECTED:
  from cutback.server.worker import load_dataset
  expected_version=("native-isolated-record-guarded-security-v1" if GUARDED_SECURITY
                    else "native-isolated-record-security-v1")
  security_runs=[row for row in session.scalars(select(ExperimentRunRecord)).all()
                 if (row.results or {}).get("execution_version")==expected_version]
  assert len(security_runs)==1 and security_runs[0].status=="COMPLETED"
  reports=[row["security_evaluation"] for row in security_runs[0].results["models"]]
  assert reports[0]["attacked_utility"]["value"]==0 and reports[1]["attacked_utility"]["value"]==1
  assert all(row["gate"]=="insufficient_evidence" for row in reports)
  if GUARDED_SECURITY:
   assert all(row["runtime_guardrails"]["policy_decisions_known"] is True for row in reports)
   assert all(row["runtime_guardrails"]["coverage_complete"] is True for row in reports)
   assert all(row["runtime_guardrails"]["benign_family_interruption"]["value"]==0
              for row in reports)
   assert all(row["guardrail_false_positive_rate"] is None for row in reports)
  security_datasets=[row for row in session.scalars(select(DatasetRecord)).all()
                     if row.provenance.get("security_suite")]
  assert len(security_datasets)==1
  loaded,_=load_dataset(session,security_datasets[0],new_only)
  assert loaded["security_suite"]==reports[0]["suite"] and len(loaded["items"])==20
 if RUNTIME_GUARDS:
  from cutback.server.guardrail_db import GuardrailPolicyRow,GuardrailOperationRow
  from cutback.server.guardrail_controls import load_private_policy,binding_for
  from cutback.server.gateway_db import GatewayCallRow,GatewayKeyRow,GatewayPolicyRow
  runtime_policies=session.scalars(select(GuardrailPolicyRow)).all()
  runtime_checks=session.scalars(select(GuardrailOperationRow)).all()
  native_calls=session.scalars(select(GatewayCallRow)).all()
  assert len(runtime_policies)==1+int(GUARDED_TOOLS)+int(GUARDED_SECURITY)
  assert all(row.state=="DISABLED" for row in runtime_policies)
  assert sum(row.checks_used for row in runtime_policies)==9+16*GUARDED_TOOLS+14*GUARDED_SECURITY
  assert len(runtime_checks)==9+16*GUARDED_TOOLS+14*GUARDED_SECURITY
  assert all(row.state=="COMPLETED" and row.decision for row in runtime_checks)
  assert sum(row.decision["action"]=="deny" for row in runtime_checks)==3+4*GUARDED_TOOLS
  if GUARDED_TOOLS or GUARDED_SECURITY:
   guarded=[row for row in operations if row.guardrail_policy_id is not None]
   assert len(guarded)==8*GUARDED_TOOLS+7*GUARDED_SECURITY
   assert all(row.broker_intent=="STARTED" for row in guarded)
   assert sum(row.result_delivery=="WITHHELD" for row in guarded)==4*GUARDED_TOOLS
   assert all(row.state_changed is True for row in guarded if row.result_delivery=="WITHHELD")
   assert sum(row.result_delivery=="AUTHORIZED" for row in guarded)==(
       4*GUARDED_TOOLS+7*GUARDED_SECURITY)
  assert len(native_calls)==4
  assert all(row.state=="SETTLED" and row.cost_usd is not None for row in native_calls)
  assert all(row.revoked_at is not None for row in session.scalars(select(GatewayKeyRow)).all())
  assert all(row.state=="DISABLED" for row in session.scalars(select(GatewayPolicyRow)).all())
  for row in runtime_policies:
   restored_policy=load_private_policy(session,new_only,row,"bootstrap_admin","owned-recovery-check")
   assert restored_policy.policy_sha256==binding_for(row).policy_sha256
 assert plans
 for row in plans:
  before=original.get(row.project_id,row.specification["artifact_ref"])
  assert digest(json.loads(before))==row.specification["plan_hash"]
  rotating.rekey(row.project_id,row.specification["artifact_ref"])
  after=new_only.get(row.project_id,row.specification["artifact_ref"])
  assert before==after
 if JOINT_EXPECTED:
  from cutback.evals.compression import JointModelContextExecutionSpec
  from cutback.server.plan_artifacts import load_plan
  joint_plans=[row for row in plans if
   row.specification["public_specification"]["evaluator_version"]=="deterministic-v12"]
  assert len(joint_plans)==2
  joint_ids=[row.id for row in joint_plans]
  joint_runs=session.scalars(select(ExperimentRunRecord).where(
   ExperimentRunRecord.plan_id.in_(joint_ids))).all()
  assert len(joint_runs)==2 and all(row.status=="COMPLETED" for row in joint_runs)
  for row in joint_plans:
   restored=load_plan(new_only,row.project_id,row.specification)
   assert isinstance(restored.compression_execution,JointModelContextExecutionSpec)
   assert restored.compression_execution.producer_binding
  confirmed=next(row for row in joint_runs if row.results["plan"]["split"]=="test")
  assert confirmed.results["lineage"]["fresh_test_claim"] is True
  decision=confirmed.results["recommendations"][0]
  assert decision["joint_model_context_confirmation_eligible"] is True
print(json.dumps({"restored_and_rekeyed_objects":len(rows)+len(caches),"restored_and_rekeyed_cache_objects":len(caches),"restored_and_rekeyed_plans":len(plans),"semantic_qualified_catalog_restored":True,"isolated_tool_receipts_restored":bool(TOOLS_EXPECTED),"paired_security_suite_restored":bool(SECURITY_EXPECTED),"runtime_guardrail_receipts_restored":bool(RUNTIME_GUARDS),"database_pointers_preserved":True}))
engine.dispose()
""".replace("RECOVERED", repr(names[1]))
                    .replace("TOOLS_EXPECTED", repr(isolated_tools))
                    .replace("SECURITY_EXPECTED", repr(security_suite))
                    .replace("RUNTIME_GUARDS", repr(runtime_guardrails))
                    .replace("GUARDED_TOOLS", repr(guarded_tools))
                    .replace("GUARDED_SECURITY", repr(guarded_security))
                    .replace("JOINT_EXPECTED", repr(joint_model_context))
                )
                recovered = json.loads(inside(container, recovery, values).strip().splitlines()[-1])

                def counts(name):
                    with psycopg.connect(**{**kwargs, "dbname": name}) as conn:
                        tables = [
                            row[0]
                            for row in conn.execute(
                                "SELECT tablename FROM pg_tables "
                                "WHERE schemaname='public' ORDER BY tablename"
                            )
                        ]
                        return {
                            table: conn.execute(
                                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                            ).fetchone()[0]
                            for table in tables
                        }

                before, after = counts(names[0]), counts(names[1])
                assert before == after
                with psycopg.connect(**{**kwargs, "dbname": names[1]}) as connection:
                    revision = connection.execute(
                        "SELECT version_num FROM alembic_version"
                    ).fetchone()[0]
                assert revision == CURRENT_REVISION
                # Two concurrent tenants, replayed receipts and unknown pricing, over real HTTP.
                from concurrent.futures import ThreadPoolExecutor

                from cutback.core.models import LLMEvent

                projects = [
                    client.post(
                        "/v1/projects", json={"slug": "synthetic-load-" + uuid4().hex}
                    ).json()["id"]
                    for _ in range(2)
                ]
                tokens = [
                    client.post(f"/v1/projects/{project}/keys", json={"role": "ingest"}).json()[
                        "token"
                    ]
                    for project in projects
                ]

                def ingest(index):
                    project, token = projects[index], tokens[index]
                    for _ in range(10):
                        records = [
                            LLMEvent(
                                environment="staging",
                                service_name="owned-load-fixture",
                                provider="openai",
                                sdk_surface="responses.create",
                                requested_model="gpt-4.1-mini" if item % 2 else "unknown-synthetic",
                                response_model="gpt-4.1-mini" if item % 2 else "unknown-synthetic",
                                trace_id=uuid4().hex,
                                span_id=uuid4().hex[:16],
                                duration_ms=item + 1,
                                status="success",
                                input_tokens=100,
                                output_tokens=20,
                                cache_read_tokens=0,
                                reasoning_tokens=0,
                                usage_source="provider_reported",
                                metering_status="text_only",
                                traffic_kind="application",
                            ).model_dump(mode="json")
                            for item in range(100)
                        ]
                        for replay in (False, True):
                            response = client.post(
                                "/v1/ingest/events",
                                headers={"Authorization": "Bearer " + token},
                                json={"project_id": project, "events": records},
                            )
                            response.raise_for_status()
                            receipt = response.json()
                            assert receipt["accepted"] == (0 if replay else 100) and receipt[
                                "duplicate"
                            ] == (100 if replay else 0)
                    report = client.get(
                        f"/v1/projects/{project}/usage?traffic_kind=application"
                    ).json()
                    assert report["summary"]["observed_attempts"] == 1000
                    assert report["summary"]["unpriced_requests"] == 500
                    assert report["summary"]["modeled_cost_usd"] is None

                started = time.monotonic()
                with ThreadPoolExecutor(max_workers=2) as executor:
                    list(executor.map(ingest, range(2)))
                load_seconds = round(time.monotonic() - started, 3)
                otel_project = client.post(
                    "/v1/projects", json={"slug": "synthetic-otel-" + uuid4().hex}
                ).json()["id"]
                otel_key = client.post(
                    f"/v1/projects/{otel_project}/keys", json={"role": "ingest"}
                ).json()["token"]
                checked(
                    [
                        sys.executable,
                        str(ROOT / "examples/runnable/otel_json.py"),
                        "--endpoint",
                        endpoint + "/v1/ingest/otel/v1/traces",
                    ],
                    env={**os.environ, "CUTBACK_PROJECT_TOKEN": otel_key},
                )
                otel_report = client.get(f"/v1/projects/{otel_project}/usage").json()
                assert (
                    otel_report["summary"]["observed_attempts"] == 1
                    and otel_report["summary"]["external_otel_observations"] == 1
                )
                result = {
                    "image": image,
                    "image_id": image_id,
                    "uid": 10001,
                    "python": "3.11.18",
                    "migration": revision,
                    "sample_application_calls": 19 + 4 * runtime_guardrails,
                    "source_linked_support_desk_calls": 12,
                    "source_workflows": 2,
                    "application_workflows": 3 + runtime_guardrails,
                    "paid_provider_calls": 0,
                    "native_batch_fixture_restored": True,
                    "guarded_tool_effects_and_delivery_restored": guarded_tools,
                    "guarded_security_utility_and_policy_decisions_restored": guarded_security,
                    "joint_model_context_confirmation_and_source_binding_restored": (
                        joint_model_context
                    ),
                    "restored_tables": len(after),
                    "row_counts_match": True,
                    **recovered,
                    "production_changed": False,
                    "secret_delivery": "anonymous_stdin_pipes_and_scoped_memory_clients"
                    if isolated_tools or runtime_guardrails
                    else "anonymous_stdin_pipes",
                    "independent_otel_producer_and_replay": "passed",
                    "concurrent_load": {
                        "tenants": 2,
                        "accepted": 2000,
                        "duplicates": 2000,
                        "seconds": load_seconds,
                        "production_sla": False,
                    },
                }
                print(json.dumps(result))
                return result
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if server_process is not None:
            try:
                server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_process.kill()
                server_process.wait()
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            for name in names:
                conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--isolated-tools", action="store_true")
    parser.add_argument("--security-suite", action="store_true")
    parser.add_argument("--runtime-guardrails", action="store_true")
    parser.add_argument("--guarded-tools", action="store_true")
    parser.add_argument("--guarded-security", action="store_true")
    parser.add_argument("--joint-model-context", action="store_true")
    args = parser.parse_args()
    main(
        args.image,
        isolated_tools=args.isolated_tools,
        security_suite=args.security_suite,
        runtime_guardrails=args.runtime_guardrails,
        guarded_tools=args.guarded_tools,
        guarded_security=args.guarded_security,
        joint_model_context=args.joint_model_context,
    )
