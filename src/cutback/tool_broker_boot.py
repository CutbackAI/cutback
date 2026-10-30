"""Dedicated operator-started broker. No collector/worker Docker socket is required."""

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from cutback.core.models import StrictModel
from cutback.tool_broker import BrokerClient, create_tool_broker
from cutback.tool_isolation import discover_local_endpoint, local_endpoint


class ToolBrokerConfig(StrictModel):
    broker_id: UUID
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    docker_endpoint: str | None = Field(default=None, max_length=512)
    clients: list[BrokerClient] = Field(min_length=1, max_length=100)
    host: str = Field(default="127.0.0.1", pattern=r"^(127\.0\.0\.1|::1|0\.0\.0\.0|::)$")
    port: int = Field(default=8091, strict=True, ge=1024, le=65535)
    tls_certfile: str | None = Field(default=None, max_length=4096)
    tls_keyfile: str | None = Field(default=None, max_length=4096)
    allow_insecure_loopback: StrictBool = False
    max_active_sessions: int = Field(default=4, strict=True, ge=1, le=8)
    max_retained_sessions: int = Field(default=1000, strict=True, ge=1, le=1000)

    @model_validator(mode="after")
    def transport(self):
        if self.broker_id.int == 0:
            raise ValueError("TOOL_BROKER_NONZERO_STABLE_ID_REQUIRED")
        if self.docker_endpoint is not None:
            local_endpoint(self.docker_endpoint)
        tls = bool(self.tls_certfile and self.tls_keyfile)
        if bool(self.tls_certfile) != bool(self.tls_keyfile) or not (
            tls or self.allow_insecure_loopback and self.host in {"127.0.0.1", "::1"}
        ):
            raise ValueError("TOOL_BROKER_TLS_OR_EXPLICIT_LOOPBACK_REQUIRED")
        return self


@contextmanager
def broker_singleton(identifier):
    """Hold one process per stable local broker identity through shutdown."""
    directory = Path.home() / ".cutback" / "broker-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(directory / (str(identifier) + ".lock"), flags, 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.config.open("rb") as stream:
            raw = stream.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("BOUND")
        config = ToolBrokerConfig.model_validate(json.loads(raw))
        endpoint = config.docker_endpoint or discover_local_endpoint()
        app = create_tool_broker(
            image_id=config.image_id,
            broker_id=config.broker_id,
            endpoint=endpoint,
            clients=config.clients,
            allow_insecure_loopback=config.allow_insecure_loopback,
            max_active_sessions=config.max_active_sessions,
            max_retained_sessions=config.max_retained_sessions,
        )
        import uvicorn

        with broker_singleton(config.broker_id):
            uvicorn.run(
                app,
                host=config.host,
                port=config.port,
                ssl_certfile=config.tls_certfile,
                ssl_keyfile=config.tls_keyfile,
                access_log=False,
                log_level="critical",
                server_header=False,
                proxy_headers=False,
                limit_concurrency=16,
                backlog=16,
                timeout_keep_alive=2,
                h11_max_incomplete_event_size=16384,
            )
        return 0
    except Exception:
        print("Tool broker unavailable; configuration and credentials suppressed.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
