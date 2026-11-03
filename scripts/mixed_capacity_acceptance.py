"""Bounded independent synthetic mixed-service acceptance, never production load.

The supervisor owns one new disposable local database and the complete child
process tree. A hung worker, interrupted response or missing receipt is failure;
only this supervisor's exact database may be force-dropped after termination.
Credentials never enter arguments, receipts or stdout.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from cutback.application_checks import WindowsJob, stop_process
from cutback.core.prompts import fingerprint
from cutback.core.workspace import prepare_private_output, write_private


def validate_child_receipt(value, profile_hash):
    # Self hashes are integrity checks, never external authenticity. Refuse
    # stale/partial receipts even if their internal hash is self-consistent.
    from support_mixed_capacity import KINDS

    document = dict(value)
    expected = document.pop("receipt_hash", None)
    if expected != fingerprint(document) or document.get("profile_hash") != profile_hash:
        raise ValueError("MIXED_CHILD_RECEIPT_IDENTITY_MISMATCH")
    if (
        document.get("contract") != "cutback.owned-mixed-capacity-receipt.v1"
        or document.get("paid_provider_calls") != 0
        or document.get("production_sla") is not False
    ):
        raise ValueError("MIXED_CHILD_RECEIPT_SCOPE_MISMATCH")
    if document.get("status") == "PASSED_DECLARED_SYNTHETIC_MIXED_ENVELOPE" and (
        document.get("cleanup_complete") is not True
        or document.get("tenants") != 2
        or set(document.get("metrics", {})) != set(KINDS)
        or not all(row.get("passed") is True for row in document["metrics"].values())
    ):
        raise ValueError("MIXED_CHILD_RECEIPT_ACCEPTANCE_INCOMPLETE")
    return value


def bounded_child(command, *, cwd, env, seconds):
    job = WindowsJob() if os.name == "nt" else None
    process = None
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            # check.py is still the child's sanitizer. No fixture payload or
            # diagnostic is retained by this process; canonical receipt only.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4
            if os.name == "nt"
            else 0,
        )
        if job:
            job.assign_and_resume(process)
        try:
            return process.wait(timeout=seconds), False
        except subprocess.TimeoutExpired:
            return None, True
    finally:
        if process is not None:
            stop_process(process, job)
        elif job:
            job.close()


def qualify(profile_path, destination):
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "tests"))
    from support_mixed_capacity import MixedProfile

    profile = MixedProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
    if destination.exists():
        raise ValueError("MIXED_RECEIPT_ALREADY_EXISTS")
    url = make_url(os.environ["CUTBACK_TEST_DATABASE_URL"])
    if url.host not in {"localhost", "127.0.0.1", "::1"} or url.get_backend_name() != "postgresql":
        raise ValueError("MIXED_REQUIRES_OWNED_LOCAL_POSTGRESQL")
    destination = prepare_private_output(destination)
    name = "cutback_mixed_" + uuid4().hex
    kwargs = dict(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        dbname=url.database,
        connect_timeout=3,
    )
    # Persist only the random owned identity before CREATE. An interrupted
    # supervisor or lost creation acknowledgement must remain reconcilable.
    # Existing intent refuses reuse of the same receipt path automatically.
    write_private(
        destination.with_name(destination.name + ".intent.json"),
        {
            "contract": "cutback.owned-mixed-database-intent.v1",
            "owned_database_name": name,
            "profile_hash": fingerprint(profile.model_dump(mode="json")),
            "state": "CREATE_PREPARED_NOT_PROOF_OF_CREATION",
        },
        exclusive=True,
    )
    created, cleaned, child_receipt = False, False, None
    code, timed_out = None, False
    failure = None
    try:
        with psycopg.connect(**kwargs, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = True
        # Migration is in a bounded child too. All credentials are environment
        # only and the exact database identity was generated above.
        env = os.environ.copy()
        owned_url = url.set(database=name).render_as_string(hide_password=False)
        env["CUTBACK_DATABASE_URL"] = env["CUTBACK_TEST_MIXED_OWNED_DATABASE_URL"] = owned_url
        code, timed_out = bounded_child(
            [sys.executable, "-m", "cutback.server.migrate"],
            cwd=root,
            env=env,
            seconds=90,
        )
        if code != 0 or timed_out:
            failure = "MIXED_MIGRATION_FAILED_OR_TIMED_OUT"
        else:
            with tempfile.TemporaryDirectory(prefix="cutback-owned-mixed-") as temporary:
                path = Path(temporary)
                selected, output = path / "profile.json", path / "receipt.json"
                write_private(selected, profile.model_dump(mode="json"), exclusive=True)
                env["CUTBACK_TEST_MIXED_CAPACITY_PROFILE"] = str(selected)
                env["CUTBACK_TEST_MIXED_CAPACITY_RECEIPT"] = str(output)
                code, timed_out = bounded_child(
                    [
                        sys.executable,
                        str(root / "scripts/check.py"),
                        "tests/end_to_end/test_mixed_capacity.py::test_explicit_sustained_mixed_profile",
                        "-q",
                    ],
                    cwd=root,
                    env=env,
                    seconds=profile.duration_seconds + profile.drain_seconds + 180,
                )
                if output.is_file() and output.stat().st_size <= 8 * 1024 * 1024:
                    # The local variable and the validation function have
                    # distinct names so an empty/failed result cannot bypass it.
                    child_receipt = validate_child_receipt(
                        json.loads(output.read_text(encoding="utf-8")),
                        fingerprint(profile.model_dump(mode="json")),
                    )
                if timed_out:
                    failure = "MIXED_CHILD_PROCESS_TREE_DEADLINE_EXCEEDED"
                elif code != 0:
                    failure = "MIXED_CHILD_TEST_FAILED"
                elif not child_receipt:
                    failure = "MIXED_CHILD_RECEIPT_MISSING"
    except Exception:
        # Do not stringify exceptions containing driver URLs/credentials.
        failure = "MIXED_SUPERVISOR_FAILED"
    finally:
        if created:
            try:
                with psycopg.connect(**kwargs, autocommit=True) as connection:
                    connection.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
                    )
                cleaned = True
            except Exception:
                failure = "MIXED_OWNED_DATABASE_CLEANUP_FAILED"
    passed = bool(
        created
        and cleaned
        and failure is None
        and child_receipt
        and child_receipt["status"] == "PASSED_DECLARED_SYNTHETIC_MIXED_ENVELOPE"
    )
    result = {
        "contract": "cutback.owned-mixed-supervisor.v1",
        "profile_hash": fingerprint(profile.model_dump(mode="json")),
        "status": "PASSED_DECLARED_SYNTHETIC_MIXED_ENVELOPE"
        if passed
        else "FAILED_OR_INCOMPLETE_MIXED_QUALIFICATION",
        "error": failure,
        "child_exit_code": code,
        "child_timed_out": timed_out,
        "owned_database_removed": cleaned,
        "owned_database_name": name,
        "child": child_receipt,
        "paid_provider_calls": 0,
        "production_sla": False,
    }
    write_private(destination, {**result, "receipt_hash": fingerprint(result)}, exclusive=True)
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    try:
        passed = qualify(args.profile, args.receipt)
    except Exception:
        print("MIXED_QUALIFICATION_CONFIGURATION_FAILED", file=sys.stderr)
        return 1
    print("MIXED_QUALIFICATION_PASSED" if passed else "MIXED_QUALIFICATION_FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
