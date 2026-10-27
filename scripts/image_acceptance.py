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
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
import psycopg
from backup_restore import backup, compose, restore
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]


def checked(command, *, source=None, timeout=180, env=None):
    result = subprocess.run(
        command, input=source, capture_output=True, text=True, timeout=timeout, env=env
    )
    if result.returncode:
        raise ValueError("IMAGE_ACCEPTANCE_COMMAND_FAILED_DETAILS_SUPPRESSED")
    return result.stdout


def main(image):
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
    try:
        with tempfile.TemporaryDirectory(prefix="cutback-image-acceptance-") as temporary:
            directory = Path(temporary)
            values = {
                "database": (
                    f"postgresql+psycopg://cutback:{quote(password, safe='')}"
                    f"@postgres:5432/{names[0]}"
                ),
                "admin": admin,
                "payload": base64.b64encode(os.urandom(32)).decode(),
            }
            for name, value in values.items():
                path = directory / name
                path.write_text(value, encoding="utf-8")
                path.chmod(0o600)
            checked(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-d",
                    "--name",
                    container,
                    "--network",
                    network,
                    "-p",
                    f"127.0.0.1:{port}:8080",
                    "--mount",
                    f"type=bind,source={directory.resolve()},target=/run/cutback-secrets,readonly",
                    "-e",
                    "CUTBACK_DATABASE_URL_FILE=/run/cutback-secrets/database",
                    "-e",
                    "CUTBACK_ADMIN_TOKEN_FILE=/run/cutback-secrets/admin",
                    "-e",
                    "CUTBACK_PAYLOAD_KEY_FILE=/run/cutback-secrets/payload",
                    "-e",
                    "CUTBACK_PAYLOAD_DIR=/tmp/encrypted",
                    "-e",
                    "CUTBACK_ALLOW_PAYLOADS=true",
                    "-e",
                    "CUTBACK_DEV_HTTP=true",
                    image,
                ]
            )
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
manifest=seed_support_desk(app,"http://127.0.0.1:8080",os.environ["CUTBACK_ADMIN_TOKEN"],Path("/tmp/owned-support-desk"))
print(json.dumps({"project_id":manifest["project_id"],"paid_provider_calls":manifest["paid_provider_calls"]}))
"""
                manifest = json.loads(
                    checked(
                        ["docker", "exec", "-i", container, "/app/.venv/bin/python", "-"],
                        source=code,
                    )
                    .strip()
                    .splitlines()[-1]
                )
                base = "/v1/projects/" + manifest["project_id"]
                usage = client.get(
                    base + "/usage?traffic_kind=application&group_by=workflow"
                ).json()
                assert usage["summary"]["observed_attempts"] == 12 and len(usage["groups"]) == 2
                assert all("app.py" in group["label"] for group in usage["groups"])
                assert client.get(base + "/changes").json()
                with psycopg.connect(**kwargs, autocommit=True) as conn:
                    key = os.urandom(32)
                    dump = directory / "database.cbk"
                    backup(dump, key, compose("pg_dump", "-U", "cutback", "-d", names[0], "-Fc"))
                    restore(
                        dump,
                        key,
                        compose("pg_restore", "-U", "cutback", "-d", names[1], "--exit-on-error"),
                    )
                recovery = """import json,os,shutil
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.engine import make_url
from cutback.server.runtime import configure_secrets
from cutback.server.db import database,PayloadSample
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
print(json.dumps({"restored_and_rekeyed_objects":len(rows),"database_pointers_preserved":True}))
engine.dispose()
""".replace("RECOVERED", repr(names[1]))
                recovered = json.loads(
                    checked(
                        ["docker", "exec", "-i", container, "/app/.venv/bin/python", "-"],
                        source=recovery,
                    )
                    .strip()
                    .splitlines()[-1]
                )

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
                    "uid": 10001,
                    "python": "3.11.18",
                    "migration": "0006",
                    "sample_application_calls": 12,
                    "source_workflows": 2,
                    "paid_provider_calls": 0,
                    "restored_tables": len(after),
                    "row_counts_match": True,
                    **recovered,
                    "production_changed": False,
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
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            for name in names:
                conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    main(parser.parse_args().image)
