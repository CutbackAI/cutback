"""Wheel-native persistent loopback collector. Docker owns only its labelled database."""

import base64
import importlib.util
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from cutback.core.workspace import private_directory, read_private, workspace_lock, write_private

IMAGE = "postgres:17-alpine@sha256:eee6c6e1b0894fa4fe0075eb054af9fd1cf39a35321c2d4110626cfe506411fb"


def directory(root):
    from cutback.agent import workspace

    root, _ = workspace(root)
    return private_directory(root, ".cutback/local")


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, timeout=180, check=False)
    if result.returncode:
        raise ValueError("LOCAL_DOCKER_OPERATION_FAILED")
    return result.stdout.decode().strip()


def owned(config):
    import json

    value = json.loads(docker("inspect", config["container"]))[0]
    if value["Config"].get("Labels", {}).get("org.cutback.local.owner") != config["owner"]:
        raise ValueError("LOCAL_RESOURCE_OWNERSHIP_MISMATCH")
    return value


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def status(root):
    location = directory(root)
    if not (location / "config.json").exists():
        return {"status": "NOT_CONFIGURED", "provider_calls": 0}
    config = read_private(location / "config.json")
    ready = False
    try:
        import httpx

        response = httpx.get(
            config["endpoint"] + "/v1/health/ready",
            timeout=2,
            trust_env=False,
            headers={"Authorization": "Bearer " + config["admin"]},
        )
        ready = response.status_code == 200
    except (ImportError, OSError, ValueError):
        pass
    except httpx.HTTPError:
        pass
    return {
        "status": "READY" if ready else "STOPPED_OR_UNAVAILABLE",
        "endpoint": config["endpoint"],
        "dashboard": config["endpoint"] + "/dashboard/",
        "payload_storage_enabled": config["allow_payloads"],
        "database": "persistent_owned_volume",
        "provider_calls": 0,
    }


def up(root: Path, *, port=8080, allow_payloads=False):
    if any(importlib.util.find_spec(name) is None for name in ("fastapi", "psycopg", "uvicorn")):
        raise ValueError("LOCAL_STACK_REQUIRES_SERVER_EXTRA")
    if not 1024 <= port <= 65535:
        raise ValueError("INVALID_LOCAL_PORT")
    location = directory(root)
    with workspace_lock(location):
        config_path = location / "config.json"
        config = read_private(config_path) if config_path.exists() else None
        if config and status(root)["status"] == "READY":
            if config["endpoint"] != f"http://127.0.0.1:{port}":
                raise ValueError("STOP_LOCAL_STACK_BEFORE_CHANGING_PORT")
            if config["allow_payloads"] != allow_payloads:
                raise ValueError("STOP_LOCAL_STACK_BEFORE_CHANGING_PAYLOAD_PERMISSION")
            return status(root)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
        if not config:
            identity = uuid4().hex
            password = location / "db_password"
            with password.open("x", encoding="utf-8") as stream:
                stream.write(secrets.token_urlsafe(32))
            password.chmod(0o600)
            config = {
                "owner": identity,
                "container": "cutback-local-" + identity[:12],
                "volume": "cutback-local-" + identity[:12],
                "db_port": free_port(),
                "admin": secrets.token_urlsafe(48),
                "payload_key": base64.b64encode(os.urandom(32)).decode(),
                "allow_payloads": allow_payloads,
                "endpoint": f"http://127.0.0.1:{port}",
            }
            # Journal ownership before provisioning; a failed startup remains recoverable.
            write_private(config_path, config, exclusive=True)
        config.update(allow_payloads=allow_payloads, endpoint=f"http://127.0.0.1:{port}")
        write_private(config_path, config)
        existing = subprocess.run(
            ["docker", "inspect", config["container"]], capture_output=True, timeout=20, check=False
        )
        if existing.returncode == 0:
            owned(config)
            docker("start", config["container"])
        else:
            volumes = subprocess.run(
                ["docker", "volume", "inspect", config["volume"]],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if volumes.returncode:
                docker(
                    "volume",
                    "create",
                    "--label",
                    "org.cutback.local.owner=" + config["owner"],
                    config["volume"],
                )
            else:
                import json

                if (
                    json.loads(volumes.stdout)[0].get("Labels", {}).get("org.cutback.local.owner")
                    != config["owner"]
                ):
                    raise ValueError("LOCAL_RESOURCE_OWNERSHIP_MISMATCH")
            docker(
                "run",
                "--detach",
                "--name",
                config["container"],
                "--label",
                "org.cutback.local.owner=" + config["owner"],
                "--publish",
                f"127.0.0.1:{config['db_port']}:5432",
                "--mount",
                f"type=bind,source={(location / 'db_password').resolve()},"
                "target=/run/secrets/db_password,readonly",
                "--mount",
                f"type=volume,source={config['volume']},target=/var/lib/postgresql/data",
                "--env",
                "POSTGRES_PASSWORD_FILE=/run/secrets/db_password",
                "--env",
                "POSTGRES_USER=cutback",
                "--env",
                "POSTGRES_DB=cutback",
                IMAGE,
            )
        (location / "stop").unlink(missing_ok=True)
        (location / "failure.json").unlink(missing_ok=True)
        # Secret values never enter argv or a captured log. Child startup reports fixed codes only.
        options = (
            {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW")}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        subprocess.Popen(
            [sys.executable, "-m", "cutback.local_stack", str(location)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        )
        for _ in range(200):
            result = status(root)
            if result["status"] == "READY":
                return result
            if (location / "failure.json").exists():
                raise ValueError("LOCAL_COLLECTOR_STARTUP_FAILED")
            time.sleep(0.25)
        raise ValueError("LOCAL_COLLECTOR_STARTUP_TIMEOUT_INSPECT_STATUS")


def down(root):
    location = directory(root)
    with workspace_lock(location):
        config = read_private(location / "config.json")
        owned(config)
        (location / "stop").write_text(config["owner"], encoding="utf-8")
        for _ in range(40):
            if status(root)["status"] != "READY":
                break
            time.sleep(0.25)
        if status(root)["status"] == "READY":
            raise ValueError("LOCAL_COLLECTOR_STOP_TIMEOUT")
        docker("stop", "--time", "5", config["container"])
        return {"status": "STOPPED", "database": "preserved", "encrypted_artifacts": "preserved"}


def configured(root):
    location = directory(root)
    config = read_private(location / "config.json")
    return location, config


def database_url(location, config):
    from urllib.parse import quote

    password = (location / "db_password").read_text().strip()
    return (
        f"postgresql+psycopg://cutback:{quote(password, safe='')}"
        f"@127.0.0.1:{config['db_port']}/cutback"
    )


def serve(location):
    """Child process, independent from the invoking CLI lifetime; loopback only."""
    import threading
    from urllib.parse import urlsplit

    import psycopg
    import uvicorn

    from cutback.evals.artifacts import EncryptedFileStore
    from cutback.server.api import create_app
    from cutback.server.migrate import migrate

    config = read_private(location / "config.json")
    try:
        (location / "failure.json").unlink(missing_ok=True)
        url = database_url(location, config)
        for attempt in range(100):
            try:
                with psycopg.connect(
                    url.replace("postgresql+psycopg:", "postgresql:"), connect_timeout=1
                ):
                    break
            except psycopg.OperationalError:
                if attempt == 99:
                    raise ValueError("LOCAL_DATABASE_NOT_READY") from None
                time.sleep(0.25)
        os.environ["CUTBACK_DATABASE_URL"] = url
        os.environ["CUTBACK_ALLOW_PAYLOADS"] = "true" if config["allow_payloads"] else "false"
        migrate()
        application = create_app(
            database_url=url, admin_token=config["admin"], allow_insecure_http=True
        )
        if config["allow_payloads"]:
            application.state.artifact_store = EncryptedFileStore(
                location / "encrypted", base64.b64decode(config["payload_key"])
            )
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                host="127.0.0.1",
                port=urlsplit(config["endpoint"]).port,
                access_log=False,
                proxy_headers=False,
                log_level="critical",
            )
        )

        def watch():
            while not server.should_exit:
                stop = location / "stop"
                if stop.exists() and stop.read_text() == config["owner"]:
                    server.should_exit = True
                time.sleep(0.25)

        threading.Thread(target=watch, daemon=True).start()
        server.run()
    except Exception:
        write_private(location / "failure.json", {"code": "LOCAL_STACK_STARTUP_FAILED"})


if __name__ == "__main__":
    serve(Path(sys.argv[1]).resolve(strict=True))
