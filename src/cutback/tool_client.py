"""Scoped TLS client for the dedicated tool broker; never starts local processes.

There is no transport retry. An uncertain mutation must be reconciled by identity,
and never replaced with a new operation or session automatically.
"""

import ipaddress
import json
import re
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from cutback.core.tool_policy import ToolContainmentScope, canonical, content_bound


class ToolBrokerUnavailable(ValueError):
    def __init__(self):
        super().__init__("TOOL_BROKER_UNAVAILABLE_CONTENT_SUPPRESSED")


class ToolBrokerUncertain(ValueError):
    def __init__(self):
        super().__init__("TOOL_OPERATION_UNCERTAIN_RECONCILE_NO_AUTOMATIC_RETRY")


def broker_origin(value, *, allow_insecure_loopback=False):
    if (
        type(value) is not str
        or len(value) > 2048
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ValueError("TOOL_BROKER_FIXED_TLS_ORIGIN_REQUIRED")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise ValueError("TOOL_BROKER_FIXED_TLS_ORIGIN_REQUIRED") from None
    try:
        loopback = ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if (
        type(value) is not str
        or len(value) > 2048
        or any(character.isspace() or ord(character) < 32 for character in value)
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or not (
            parts.scheme == "https"
            or parts.scheme == "http"
            and allow_insecure_loopback
            and loopback
        )
    ):
        raise ValueError("TOOL_BROKER_FIXED_TLS_ORIGIN_REQUIRED")
    try:
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError("PORT")
    except ValueError:
        raise ValueError("TOOL_BROKER_FIXED_TLS_ORIGIN_REQUIRED") from None
    return value.rstrip("/")


class ToolBrokerClient:
    def __init__(
        self,
        origin,
        token,
        *,
        project_id,
        principal_id,
        broker_id,
        image_id,
        allow_insecure_loopback=False,
        timeout_seconds=125,
        transport=None,
    ):
        origin = broker_origin(origin, allow_insecure_loopback=allow_insecure_loopback)
        if (
            type(token) is not str
            or not 32 <= len(token) <= 256
            or any(ord(character) < 33 or ord(character) > 126 for character in token)
            or not 1 <= timeout_seconds <= 125
        ):
            raise ValueError("TOOL_BROKER_SCOPED_KEY_AND_FINITE_TIMEOUT_REQUIRED")
        self.project_id, self.principal_id, self.image_id = (
            UUID(str(project_id)),
            UUID(str(principal_id)),
            image_id,
        )
        self.broker_id = UUID(str(broker_id))
        self._pending_containment: dict[UUID, ToolContainmentScope] = {}
        if (
            self.project_id.int == 0
            or self.principal_id.int == 0
            or self.broker_id.int == 0
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
        ):
            raise ValueError("TOOL_BROKER_FIXED_IMAGE_AND_NONZERO_SCOPE_REQUIRED")
        self._client = httpx.Client(
            base_url=origin,
            headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(timeout_seconds, connect=5, pool=5),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self):
        self._client.close()

    def _request(self, method, path, body=None):
        kwargs = (
            {}
            if body is None
            else {"content": canonical(body), "headers": {"Content-Type": "application/json"}}
        )
        try:
            with self._client.stream(method, path, **kwargs) as response:
                if response.status_code not in {200, 202}:
                    raise ToolBrokerUnavailable()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ToolBrokerUnavailable()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if len(raw) + len(chunk) > 300000:
                        raise ToolBrokerUnavailable()
                    raw.extend(chunk)
                value = json.loads(raw)
                if type(value) is not dict:
                    raise ToolBrokerUnavailable()
                content_bound(value, 300000)
                return value
        except (httpx.HTTPError, OSError, ValueError, TypeError, RecursionError):
            raise ToolBrokerUnavailable() from None

    def identity(self):
        value = self._request("GET", "/v1/identity")
        try:
            generation = UUID(value["broker_generation"])
            if (
                value["project_id"] != str(self.project_id)
                or value["principal_id"] != str(self.principal_id)
                or value["image_id"] != self.image_id
                or value["profile"] != "cutback.isolated-record-tools.v1"
                or value.get("broker_id") != str(self.broker_id)
                or generation.int == 0
            ):
                raise ValueError("MISMATCH")
            return generation
        except (KeyError, ValueError, TypeError):
            raise ToolBrokerUnavailable() from None

    def _path(self, policy):
        if (policy.project_id, policy.principal_id, policy.image_id) != (
            self.project_id,
            self.principal_id,
            self.image_id,
        ):
            raise ValueError("TOOL_BROKER_POLICY_OUTSIDE_CONFIGURED_CLIENT_SCOPE")
        return "/v1/projects/" + str(self.project_id) + "/sessions/" + str(policy.session_id)

    def _receipt(self, value, policy):
        if any(
            value.get(key) != expected
            for key, expected in {
                "session_id": str(policy.session_id),
                "project_id": str(self.project_id),
                "principal_id": str(self.principal_id),
                "image_id": self.image_id,
                "environment": policy.environment,
                "workflow": policy.workflow,
                "policy_sha256": policy.policy_sha256,
            }.items()
        ):
            raise ToolBrokerUnavailable()
        return value

    def start(self, policy, seeds, approval_hash, generation):
        path = self._path(policy).rsplit("/", 1)[0]
        self._pending_containment[policy.session_id] = ToolContainmentScope(
            session_id=policy.session_id,
            project_id=policy.project_id,
            principal_id=policy.principal_id,
            broker_id=self.broker_id,
            environment=policy.environment,
            workflow=policy.workflow,
            image_id=policy.image_id,
            policy_sha256=policy.policy_sha256,
        )
        try:
            value = self._receipt(
                self._request(
                    "POST",
                    path,
                    {
                        "broker_generation": str(generation),
                        "policy": policy.model_dump(mode="json"),
                        "seeds": [seed.model_dump(mode="json") for seed in seeds],
                        "approval_hash": approval_hash,
                    },
                ),
                policy,
            )
            if value.get("approval_hash") != approval_hash or value.get("state") != "READY":
                raise ToolBrokerUnavailable()
            return value
        except ToolBrokerUnavailable:
            raise ToolBrokerUncertain() from None

    def execute(self, policy, operation_id, tool, arguments):
        try:
            value = self._request(
                "POST",
                self._path(policy) + "/calls",
                {
                    "operation_id": str(operation_id),
                    "policy_sha256": policy.policy_sha256,
                    "tool": tool,
                    "arguments": arguments,
                },
            )
            if (
                value.get("operation_id") != str(operation_id)
                or value.get("policy_sha256") != policy.policy_sha256
                or value.get("status") not in {"returned", "denied"}
                or type(value.get("state_changed")) is not bool
                or type(value.get("replayed")) is not bool
            ):
                raise ToolBrokerUnavailable()
            content_bound(value, policy.max_result_bytes + 2048)
            return value
        except ToolBrokerUnavailable:
            raise ToolBrokerUncertain() from None

    def snapshot(self, policy, *, purpose):
        value = self._request("POST", self._path(policy) + "/snapshot", {"purpose": purpose})
        if (
            value.get("session_id") != str(policy.session_id)
            or value.get("policy_sha256") != policy.policy_sha256
            or value.get("purpose") != purpose
            or value.get("content") != "explicit_authorized_evaluator_channel"
            or type(value.get("state")) is not dict
        ):
            raise ToolBrokerUnavailable()
        return value["state"]

    def status(self, policy):
        return self._receipt(self._request("GET", self._path(policy)), policy)

    def contain(self, policy):
        scope = ToolContainmentScope(
            session_id=policy.session_id,
            project_id=policy.project_id,
            principal_id=policy.principal_id,
            broker_id=self.broker_id,
            environment=policy.environment,
            workflow=policy.workflow,
            image_id=policy.image_id,
            policy_sha256=policy.policy_sha256,
        )
        value = self._receipt(
            self._request(
                "POST", self._path(policy) + "/containment", scope.model_dump(mode="json")
            ),
            policy,
        )
        if (
            value.get("broker_id") != str(self.broker_id)
            or value.get("state") != "CLOSED"
            or value.get("cleanup_confirmed") is not True
        ):
            raise ToolBrokerUncertain()
        self._pending_containment.pop(policy.session_id, None)
        return value

    def pending_containment_scopes(self):
        """Owned metadata only, for an explicit local launcher's final cleanup."""
        return list(self._pending_containment.values())
