"""Encrypted local Compose backup/restore drill. Credentials and plaintext never enter logs."""

import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra/compose/docker-compose.yml"


def backup(destination: Path, key: bytes, command: list[str]):
    if len(key) != 32:
        raise ValueError("backup requires an independent 32-byte encryption key")
    destination.parent.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(b"CUTBACK-BACKUP-1")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    created = False
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"CBK1" + nonce)
            while chunk := process.stdout.read(65536):
                stream.write(cipher.update(chunk))
            if process.wait(timeout=60):
                raise ValueError("database backup failed; details suppressed")
            stream.write(cipher.finalize())
            stream.write(cipher.tag)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        process.kill()
        process.wait()
        if created:
            destination.unlink(missing_ok=True)
        raise
    finally:
        process.stdout.close()


def restore(source: Path, key: bytes, command: list[str]):
    # Authenticate the entire dump before passing any plaintext SQL to pg_restore.
    with tempfile.TemporaryFile() as plaintext, source.open("rb") as stream:
        header = stream.read(16)
        if len(header) != 16 or header[:4] != b"CBK1":
            raise ValueError("invalid backup envelope")
        stream.seek(-16, 2)
        tag = stream.read(16)
        remaining = stream.tell() - 32
        stream.seek(16)
        cipher = Cipher(algorithms.AES(key), modes.GCM(header[4:], tag)).decryptor()
        cipher.authenticate_additional_data(b"CUTBACK-BACKUP-1")
        while remaining:
            chunk = stream.read(min(remaining, 65536))
            if not chunk:
                raise ValueError("truncated backup")
            plaintext.write(cipher.update(chunk))
            remaining -= len(chunk)
        plaintext.write(cipher.finalize())
        plaintext.seek(0)
        result = subprocess.run(
            command,
            stdin=plaintext,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
        if result.returncode:
            raise ValueError("database restore failed; details suppressed")


def compose(*args):
    return ["docker", "compose", "-f", str(COMPOSE), "exec", "-T", "postgres", *args]


def drill():
    import psycopg
    from psycopg import sql

    password = (ROOT / "infra/compose/secrets/db_password").read_text().strip()
    kwargs = dict(host="127.0.0.1", port=55432, user="cutback", password=password, dbname="cutback")
    destination = ROOT / "artifacts" / ("backup-drill-" + uuid4().hex + ".cbk")
    key = os.urandom(32)
    restored = "cutback_restore_" + uuid4().hex
    with psycopg.connect(**kwargs, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restored)))
    try:
        backup(destination, key, compose("pg_dump", "-U", "cutback", "-d", "cutback", "-Fc"))
        restore(
            destination,
            key,
            compose("pg_restore", "-U", "cutback", "-d", restored, "--exit-on-error"),
        )

        def counts(database):
            with psycopg.connect(**{**kwargs, "dbname": database}) as connection:
                tables = [
                    row[0]
                    for row in connection.execute(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname='public' ORDER BY tablename"
                    )
                ]
                return {
                    table: connection.execute(
                        sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                    ).fetchone()[0]
                    for table in tables
                }

        original, recovered = counts("cutback"), counts(restored)
        if original != recovered:
            raise ValueError("restore row counts differ")
        print(
            f"Encrypted backup restored and verified: {len(original)} tables; dedicated local DB."
        )
        return {
            "status": "passed",
            "tables": len(original),
            "row_counts_match": True,
            "encrypted_backup": destination.name,
            "production_changed": False,
        }
    finally:
        with psycopg.connect(**kwargs, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(restored))
            )


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--drill", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(drill()))
