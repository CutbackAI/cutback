"""Fixed-origin guardrail checks. No redirects, retries, provider calls or exports."""

from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from cutback.core.episodes import bounded_episode_value
from cutback.core.guardrails import GuardrailBinding, GuardrailCheckReceipt, GuardrailDecision
from cutback.core.tool_policy import canonical
from cutback.evals._logging import owned_async_sdk_request, owned_sdk_request
from cutback.guardrails import strict_json

MAX_RECEIPT_BYTES = 16384
STAGES = {"input", "output", "tool_input", "tool_output"}


def guardrail_origin(value, *, allow_insecure_loopback=False):
    if (
        type(value) is not str
        or len(value) > 2048
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ValueError("GUARDRAIL_FIXED_TLS_ORIGIN_REQUIRED")
    try:
        parts = urlsplit(value)
        if (
            not parts.hostname
            or parts.username
            or parts.password
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or parts.port is not None
            and not 1 <= parts.port <= 65535
            or not (
                parts.scheme == "https"
                or parts.scheme == "http"
                and allow_insecure_loopback is True
                and parts.hostname == "127.0.0.1"
            )
        ):
            raise ValueError("ORIGIN")
    except ValueError:
        raise ValueError("GUARDRAIL_FIXED_TLS_ORIGIN_REQUIRED") from None
    return value.rstrip("/")


class _Scope:
    def __init__(self, binding, workflow):
        self._binding_json = canonical(binding.model_dump(mode="json"))
        captured = GuardrailBinding.model_validate_json(self._binding_json)
        if workflow not in captured.workflows:
            raise ValueError("GUARDRAIL_CLIENT_WORKFLOW_OUTSIDE_BINDING")
        self.workflow, self._closed = workflow, False

    @property
    def binding(self):
        return GuardrailBinding.model_validate_json(self._binding_json)

    def _decision(self, operation, stage, code, *, unavailable=False):
        scope = self.binding
        return GuardrailDecision(
            operation_id=operation,
            policy_id=scope.policy_id,
            policy_sha256=scope.policy_sha256,
            stage=stage,
            status="unavailable" if unavailable else "blocked",
            action="allow" if unavailable and scope.on_unavailable == "fail_open" else "deny",
            code=code,
            checks_completed=0,
            checks_expected=scope.stage_rule_counts.get(stage, 0),
            coverage_complete=False,
            duration_ms=None,
        )

    def _prepare(self, operation, stage, content):
        if not isinstance(operation, UUID) or not operation.int or stage not in STAGES:
            raise ValueError("GUARDRAIL_NONZERO_OPERATION_AND_QUALIFIED_STAGE_REQUIRED")
        scope, now = self.binding, datetime.now(UTC)
        if self._closed or stage not in scope.protected_stages or now < scope.starts_at:
            return self._decision(operation, stage, "GUARDRAIL_SCOPE_DENIED"), None
        if now >= scope.expires_at:
            return self._decision(operation, stage, "GUARDRAIL_EXPIRED"), None
        try:
            bounded_episode_value(content, max_bytes=scope.max_content_bytes)
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return self._decision(operation, stage, "GUARDRAIL_CONTENT_BOUND"), None
        return None, {
            "operation_id": str(operation),
            "policy_sha256": scope.policy_sha256,
            "environment": scope.environment,
            "workflow": self.workflow,
            "stage": stage,
            "content": content,
        }

    def _path(self):
        scope = self.binding
        return f"/v1/projects/{scope.project_id}/guardrails/{scope.policy_id}/check"

    def _identity(self, status, raw):
        if self._closed or status != 200:
            raise ValueError("GUARDRAIL_IDENTITY_UNAVAILABLE")
        try:
            value = strict_json(raw)
            if set(value) != {"service_id", "policy"}:
                raise ValueError("FIELDS")
            binding = GuardrailBinding(service_id=value["service_id"], **value["policy"])
            if binding != self.binding:
                raise ValueError("SCOPE")
            return binding
        except Exception:
            raise ValueError("GUARDRAIL_IDENTITY_UNAVAILABLE") from None

    def _unavailable(self, operation, stage):
        scope, now = self.binding, datetime.now(UTC)
        if self._closed or now < scope.starts_at:
            return self._decision(operation, stage, "GUARDRAIL_SCOPE_DENIED")
        if now >= scope.expires_at:
            return self._decision(operation, stage, "GUARDRAIL_EXPIRED")
        return self._decision(operation, stage, "GUARDRAIL_CHECK_UNAVAILABLE", unavailable=True)

    def _read(self, operation, stage, status, raw):
        if status in {400, 401, 403, 404, 405, 413, 415, 422} or 300 <= status < 400:
            return self._decision(operation, stage, "GUARDRAIL_SCOPE_DENIED")
        if status == 409:
            return self._decision(operation, stage, "GUARDRAIL_OPERATION_CONFLICT")
        if status != 200:
            return self._unavailable(operation, stage)
        try:
            receipt = GuardrailCheckReceipt.model_validate(strict_json(raw))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return self._unavailable(operation, stage)
        scope, decision = self.binding, receipt.decision
        if (
            receipt.service_id,
            receipt.project_id,
            receipt.principal_id,
            receipt.environment,
            receipt.workflow,
        ) != (
            scope.service_id,
            scope.project_id,
            scope.principal_id,
            scope.environment,
            self.workflow,
        ) or (
            decision.operation_id,
            decision.policy_id,
            decision.policy_sha256,
            decision.stage,
        ) != (operation, scope.policy_id, scope.policy_sha256, stage):
            return self._decision(operation, stage, "GUARDRAIL_SCOPE_DENIED")
        if decision.checks_expected != scope.stage_rule_counts[stage] or (
            decision.status == "unavailable"
            and decision.action != ("allow" if scope.on_unavailable == "fail_open" else "deny")
        ):
            return self._decision(operation, stage, "GUARDRAIL_SCOPE_DENIED")
        if datetime.now(UTC) >= scope.expires_at:
            return self._decision(operation, stage, "GUARDRAIL_EXPIRED")
        if self._closed:
            return self._decision(operation, stage, "GUARDRAIL_SCOPE_DENIED")
        return decision


def client_options(origin, token, allow_insecure_loopback, timeout_seconds):
    origin = guardrail_origin(origin, allow_insecure_loopback=allow_insecure_loopback)
    if (
        type(token) is not str
        or not 32 <= len(token) <= 256
        or any(ord(char) < 33 or ord(char) > 126 for char in token)
        or type(timeout_seconds) not in {int, float}
        or not 0.1 <= timeout_seconds <= 5
    ):
        raise ValueError("GUARDRAIL_SCOPED_TOKEN_AND_FINITE_DEADLINE_REQUIRED")
    return dict(
        base_url=origin,
        headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"},
        timeout=httpx.Timeout(
            timeout_seconds, connect=min(timeout_seconds, 2), pool=min(timeout_seconds, 2)
        ),
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        follow_redirects=False,
        trust_env=False,
    )


class GuardrailClient(_Scope):
    def __init__(
        self,
        origin,
        token,
        *,
        binding,
        workflow,
        transport=None,
        allow_insecure_loopback=False,
        timeout_seconds=3,
    ):
        super().__init__(binding, workflow)
        self._http = httpx.Client(
            transport=transport,
            **client_options(origin, token, allow_insecure_loopback, timeout_seconds),
        )

    @owned_sdk_request
    def identity(self):
        try:
            with self._http.stream("GET", self._path().removesuffix("/check")) as response:
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("ENCODING")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if len(raw) + len(chunk) > MAX_RECEIPT_BYTES:
                        raise ValueError("BOUND")
                    raw.extend(chunk)
                return self._identity(response.status_code, bytes(raw))
        except Exception:
            raise ValueError("GUARDRAIL_IDENTITY_UNAVAILABLE") from None

    @owned_sdk_request
    def check(self, *, operation_id, stage, content):
        decision, body = self._prepare(operation_id, stage, content)
        if decision is not None:
            return decision
        try:
            with self._http.stream("POST", self._path(), json=body) as response:
                if (
                    response.headers.get("content-encoding", "identity").strip().lower()
                    != "identity"
                ):
                    raise ValueError("GUARDRAIL_RECEIPT_ENCODING_UNQUALIFIED")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if len(raw) + len(chunk) > MAX_RECEIPT_BYTES:
                        raise ValueError("GUARDRAIL_RECEIPT_BOUND")
                    raw.extend(chunk)
                return self._read(operation_id, stage, response.status_code, bytes(raw))
        except Exception:
            return self._unavailable(operation_id, stage)

    def close(self):
        if not self._closed:
            self._closed = True
            self._http.headers.pop("Authorization", None)
            self._http.close()


class AsyncGuardrailClient(_Scope):
    def __init__(
        self,
        origin,
        token,
        *,
        binding,
        workflow,
        transport=None,
        allow_insecure_loopback=False,
        timeout_seconds=3,
    ):
        super().__init__(binding, workflow)
        self._http = httpx.AsyncClient(
            transport=transport,
            **client_options(origin, token, allow_insecure_loopback, timeout_seconds),
        )

    @owned_async_sdk_request
    async def check(self, *, operation_id, stage, content):
        decision, body = self._prepare(operation_id, stage, content)
        if decision is not None:
            return decision
        try:
            async with self._http.stream("POST", self._path(), json=body) as response:
                if (
                    response.headers.get("content-encoding", "identity").strip().lower()
                    != "identity"
                ):
                    raise ValueError("GUARDRAIL_RECEIPT_ENCODING_UNQUALIFIED")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_RECEIPT_BYTES:
                        raise ValueError("GUARDRAIL_RECEIPT_BOUND")
                    raw.extend(chunk)
                return self._read(operation_id, stage, response.status_code, bytes(raw))
        except Exception:
            return self._unavailable(operation_id, stage)

    async def close(self):
        if not self._closed:
            self._closed = True
            self._http.headers.pop("Authorization", None)
            await self._http.aclose()
