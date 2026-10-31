"""Explicit collector destination and private scoped credential setup for agent jobs."""

import os
from pathlib import Path
from typing import Any
from uuid import UUID

from cutback.core.config import validate_endpoint
from cutback.core.workspace import read_private, write_private


class Control:
    def __init__(self, endpoint, token, *, client=None):
        self.endpoint = validate_endpoint(endpoint)
        self.token = token
        self.client = client

    def __call__(self, method, path, body=None):
        import httpx

        call = self.client.request if self.client else httpx.request
        options: dict[str, Any] = (
            {} if self.client else {"trust_env": False, "follow_redirects": False, "timeout": 30}
        )
        try:
            response = call(
                method,
                self.endpoint + path,
                headers={"Authorization": "Bearer " + self.token},
                **({"json": body} if body is not None else {}),
                **options,
            )
        except httpx.HTTPError:
            raise ValueError("COLLECTOR_RESPONSE_UNCERTAIN_INSPECT_SAVED_STATE") from None
        if response.status_code >= 300:
            code = response.json().get("code", "COLLECTOR_REQUEST_FAILED")
            if not isinstance(code, str) or not code.replace("_", "").isalnum() or len(code) > 120:
                code = "COLLECTOR_REQUEST_FAILED"
            raise ValueError(code)
        return response.json()


def connect(root: Path, *, local=False, endpoint=None, project=None):
    from cutback.agent import workspace

    _, location = workspace(root)
    project = str(UUID(str(project))) if project is not None else None
    connection = location / "connection.json"
    if connection.exists():
        value = read_private(connection)
        from cutback.agent_bootstrap import validate_connection

        validate_connection(root, value)
        if (endpoint and endpoint != value["endpoint"]) or (
            project and project != value["project"]
        ):
            raise ValueError("SAVED_COLLECTOR_DESTINATION_CHANGED")
        token = value.get("token") or os.getenv("CUTBACK_CONTROL_TOKEN")
        if not token:
            raise ValueError("NO_CONTROL_CREDENTIAL")
        return Control(value["endpoint"], token), value["project"]
    if local:
        from cutback.local_stack import configured, status

        _, config = configured(root)
        if status(root)["status"] != "READY" or not config["allow_payloads"]:
            raise ValueError("START_LOCAL_COLLECTOR_WITH_EXPLICIT_ALLOW_PAYLOADS")
        endpoint = config["endpoint"]
        control = Control(endpoint, config["admin"])
        from cutback.agent_bootstrap import bootstrap_connection

        value = bootstrap_connection(root, control, owner=config["owner"])
        return Control(endpoint, value["token"]), value["project"]
    if not endpoint or not project or not os.getenv("CUTBACK_CONTROL_TOKEN"):
        raise ValueError("EXPLICIT_COLLECTOR_AND_PROJECT_OR_LOCAL_REQUIRED")
    write_private(
        connection, {"endpoint": validate_endpoint(endpoint), "project": project}, exclusive=True
    )
    return Control(endpoint, os.environ["CUTBACK_CONTROL_TOKEN"]), project
