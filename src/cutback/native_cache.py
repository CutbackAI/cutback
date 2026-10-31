"""Explicit encrypted response caching for qualified synchronous Responses calls.

This optional runtime adapter does not patch the SDK, infer permission to retain
content, retry a provider call, or claim that cached usage is a new provider bill.
The collector rechecks the current policy on every lookup and write.
"""

import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    ConfigDict,
    Field,
    SecretBytes,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from cutback.core.config import validate_endpoint
from cutback.core.exact_cache import EXACT_CACHE_CONTRACT, ExactCacheExecution
from cutback.core.model_identity import reported_model_identifier
from cutback.core.models import Identifier, StrictModel
from cutback.core.sdk_compat import OPENAI_TEXT_SDKS
from cutback.evals._logging import owned_sdk_request


class NativeCacheContext(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    project_id: UUID
    policy_id: UUID
    request_hmac_key: SecretBytes
    workflow: Identifier
    environment: Identifier
    prompt_revision: Identifier
    freshness_ref: Identifier
    ttl_seconds: int = Field(strict=True, ge=1, le=2592000)
    content_retention_authorized: Literal[True]
    immutable_or_safely_invalidated: Literal[True]

    @field_validator(
        "content_retention_authorized", "immutable_or_safely_invalidated", mode="before"
    )
    @classmethod
    def explicit_authority(cls, value):
        if value is not True:
            raise ValueError("NATIVE_CACHE_EXPLICIT_BOOLEAN_AUTHORITY_REQUIRED")
        return value

    @model_validator(mode="after")
    def credentials(self):
        if (
            len(self.request_hmac_key.get_secret_value()) < 32
            or self.project_id.int == 0
            or self.policy_id.int == 0
        ):
            raise ValueError("NATIVE_CACHE_EXPLICIT_SCOPE_AND_SECRET_MATERIAL_REQUIRED")
        return self


class NativeCacheScope(NativeCacheContext):
    endpoint: str
    token: SecretStr

    @field_validator("endpoint")
    @classmethod
    def destination(cls, value):
        return validate_endpoint(value)

    @field_validator("token")
    @classmethod
    def scoped_token(cls, value):
        if len(value.get_secret_value()) < 32:
            raise ValueError("NATIVE_CACHE_SCOPED_TOKEN_REQUIRED")
        return value


CacheWrite = Literal[
    "not_attempted", "created", "conflict", "rejected", "uncertain", "not_eligible"
]


@dataclass(frozen=True)
class NativeCacheReceipt:
    origin: Literal["native_provider", "encrypted_cache"]
    native_sdk_calls: Literal[0, 1]
    cache_write: CacheWrite
    elapsed_ms: float
    # SDK usage on a hit describes the original response. It is not a new meter.
    # A caller-supplied HTTP transport can have its own behavior; one SDK call
    # alone cannot prove how many vendor attempts occurred or what was invoiced.
    usage_origin: Literal["native_response", "historical_cached_response"]
    invoice_verified_cost_usd: None = None


@dataclass(frozen=True)
class NativeCacheResult:
    response: Any = field(repr=False)
    receipt: NativeCacheReceipt


class NativeResponseCacheExecution:
    """Shared native semantics; each backend must enforce current authority."""

    def __init__(self, context: NativeCacheContext):
        self.scope = context

    def _post(self, path, body):
        raise NotImplementedError("NATIVE_CACHE_AUTHORIZED_BACKEND_REQUIRED")

    @staticmethod
    def _eligible(response):
        return (
            response.status == "completed"
            and bool(response.output)
            and all(
                item.type == "message"
                and item.status == "completed"
                and item.content
                and all(part.type == "output_text" for part in item.content)
                for item in response.output
            )
        )

    @staticmethod
    def _native_response(value):
        from openai.types.responses import Response

        try:
            return Response.model_validate(value)
        except ValidationError as error:
            # The pinned SDK permits incomplete usage metadata. SDK3 added a
            # required cache-write token field that a server can still omit.
            # Preserve its native unknown value rather than inventing zero.
            errors = error.errors(include_input=False, include_context=False)
            if not errors or any(
                item["type"] != "missing" or item["loc"][:1] != ("usage",) for item in errors
            ):
                raise ValueError("NATIVE_CACHE_RESPONSE_SCHEMA_INVALID") from None
            from openai._models import construct_type

            return construct_type(type_=Response, value=value)

    @owned_sdk_request
    def create(self, client, *, dispatch_timeout_seconds=None, **request):
        from openai import OpenAI
        from openai._types import Omit

        started = time.monotonic()
        if dispatch_timeout_seconds is not None and (
            type(dispatch_timeout_seconds) not in {int, float}
            or not math.isfinite(dispatch_timeout_seconds)
            or not 0 < dispatch_timeout_seconds <= 30
        ):
            raise ValueError("NATIVE_CACHE_BOUNDED_DISPATCH_TIMEOUT_REQUIRED")
        sdk_version = version("openai")
        if sdk_version not in OPENAI_TEXT_SDKS or not isinstance(client, OpenAI):
            raise ValueError("NATIVE_CACHE_REQUIRES_QUALIFIED_SYNC_RESPONSES_SDK")
        if (
            client.max_retries != 0
            or str(client.base_url).rstrip("/") != "https://api.openai.com/v1"
        ):
            raise ValueError("NATIVE_CACHE_REQUIRES_EXPLICIT_NO_RETRY_NATIVE_DESTINATION")
        if (
            not isinstance(client.api_key, str)
            or getattr(client, "_api_key_provider", None) is not None
        ):
            raise ValueError("NATIVE_CACHE_REQUIRES_STABLE_PROVIDER_CREDENTIAL_BINDING")
        if any(
            value is not None and not isinstance(value, str)
            for value in (client.organization, client.project)
        ):
            raise ValueError("NATIVE_CACHE_PROVIDER_ACCOUNT_REQUIRES_REVIEW")
        provider_context = {
            "organization": client.organization,
            "project": client.project,
            "credential_hmac": hmac.new(
                self.scope.request_hmac_key.get_secret_value(),
                b"cutback-native-cache-provider:" + client.api_key.encode(),
                hashlib.sha256,
            ).hexdigest(),
        }
        headers = {}
        for name, value in client.default_headers.items():
            if isinstance(value, Omit):
                continue
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValueError("NATIVE_CACHE_PROVIDER_HEADERS_REQUIRE_REVIEW")
            headers[name.lower()] = value
        allowed = {
            "model",
            "input",
            "instructions",
            "max_output_tokens",
            "store",
            "temperature",
            "top_p",
            "stream",
        }
        if (
            set(request) - allowed
            or not isinstance(request.get("model"), str)
            or not isinstance(request.get("input"), str)
            or request.get("store") is not False
            or request.get("stream", False) is not False
            or type(request.get("max_output_tokens")) is not int
            or not 16 <= request["max_output_tokens"] <= 16384
            or request.get("instructions") is not None
            and not isinstance(request["instructions"], str)
        ):
            raise ValueError("NATIVE_CACHE_REQUIRES_BOUNDED_STATELESS_TEXT_WITH_STORE_FALSE")
        try:
            canonical = json.dumps(
                {
                    "sdk_version": sdk_version,
                    "provider_headers": headers,
                    "provider_context": provider_context,
                    "request": request,
                },
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError, RecursionError):
            raise ValueError("NATIVE_CACHE_REQUEST_NOT_CANONICAL_JSON") from None
        if len(canonical) > 65536:
            raise ValueError("NATIVE_CACHE_REQUEST_BOUND_EXCEEDED")
        if reported_model_identifier(request["model"]) != request["model"]:
            raise ValueError("NATIVE_CACHE_MODEL_IDENTIFIER_REQUIRES_REVIEW")
        key = hmac.new(
            self.scope.request_hmac_key.get_secret_value(), canonical, hashlib.sha256
        ).hexdigest()
        body = {
            **self.scope.model_dump(
                mode="json",
                include={
                    "project_id",
                    "policy_id",
                    "workflow",
                    "environment",
                    "prompt_revision",
                    "freshness_ref",
                },
            ),
            "provider": "openai",
            "contract": EXACT_CACHE_CONTRACT,
            "model": request["model"],
            "key_sha256": key,
        }
        status, found = self._post("/v1/ingest/cache/lookup", body)
        if status in {401, 403}:
            raise ValueError("NATIVE_CACHE_CURRENT_POLICY_AND_SCOPED_KEY_REQUIRED")
        if status != 200 or not isinstance(found, dict) or type(found.get("hit")) is not bool:
            raise ValueError("NATIVE_CACHE_LOOKUP_UNAVAILABLE_NO_PROVIDER_REQUEST")
        if found.get("contract") != EXACT_CACHE_CONTRACT:
            raise ValueError("NATIVE_CACHE_COLLECTOR_CONTRACT_REQUIRED_NO_PROVIDER_REQUEST")
        if found["hit"]:
            try:
                expires = datetime.fromisoformat(found["expires_at"])
                if expires.tzinfo is None or expires <= datetime.now(UTC):
                    raise ValueError("expired")
                stored = json.loads(found["response"])
                if (
                    stored["version"] != "cutback.native-response-cache.v1"
                    or stored["request_hmac"] != key
                    or stored["sdk_version"] != sdk_version
                ):
                    raise ValueError("binding")
                response = self._native_response(stored["response"])
                if not self._eligible(response):
                    raise ValueError("ineligible")
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                raise ValueError(
                    "NATIVE_CACHE_STORED_RESPONSE_INVALID_NO_PROVIDER_REQUEST"
                ) from None
            return NativeCacheResult(
                response,
                NativeCacheReceipt(
                    "encrypted_cache",
                    0,
                    "not_attempted",
                    (time.monotonic() - started) * 1000,
                    "historical_cached_response",
                ),
            )
        if found.get("response") is not None:
            raise ValueError("NATIVE_CACHE_MISS_REPLY_INVALID_NO_PROVIDER_REQUEST")
        if found.get("reason") is not None:
            raise ValueError("NATIVE_CACHE_CONTENT_UNAVAILABLE_NO_PROVIDER_REQUEST")
        # Exactly one native invocation. Its exceptions propagate without a retry;
        # a failed/uncertain call is not a cache miss that may be replayed.
        dispatch = dict(request)
        if dispatch_timeout_seconds is not None:
            remaining = dispatch_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise ValueError("NATIVE_CACHE_WALL_EXPIRED_BEFORE_NATIVE_DISPATCH")
            dispatch["timeout"] = remaining
        response = client.responses.create(**dispatch)
        write: CacheWrite = "not_eligible"
        encoded = None
        try:
            native = self._native_response(response.model_dump(mode="json", exclude_unset=True))
            if self._eligible(native):
                encoded = json.dumps(
                    {
                        "version": "cutback.native-response-cache.v1",
                        "request_hmac": key,
                        "sdk_version": sdk_version,
                        "response": native.model_dump(mode="json", exclude_unset=True),
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                )
                if len(encoded.encode()) > 64000:
                    encoded = None
        except (ValueError, TypeError, AttributeError, RecursionError):
            encoded = None
        if encoded is not None:
            try:
                status, acknowledgement = self._post(
                    "/v1/ingest/cache/put",
                    {
                        **body,
                        "response": encoded,
                        "ttl_seconds": self.scope.ttl_seconds,
                        "immutable_or_safely_invalidated": True,
                    },
                )
                write = (
                    "created"
                    if status == 201
                    and isinstance(acknowledgement, dict)
                    and acknowledgement.get("contract") == EXACT_CACHE_CONTRACT
                    else "conflict"
                    if status == 409
                    else "rejected"
                    if 400 <= status < 500
                    else "uncertain"
                )
            except (ValueError, TypeError, AttributeError, RecursionError):
                # The native response already exists. Never raise an ambiguous
                # write error that could encourage a second, potentially paid call.
                write = "uncertain"
        return NativeCacheResult(
            response,
            NativeCacheReceipt(
                "native_provider",
                1,
                write,
                (time.monotonic() - started) * 1000,
                "native_response",
            ),
        )


class NativeResponsesCache(NativeResponseCacheExecution):
    scope: NativeCacheScope

    def __init__(self, scope: NativeCacheScope, *, http_client=None, timeout_seconds=3.0):
        import httpx

        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 10
        ):
            raise ValueError("NATIVE_CACHE_BOUNDED_TIMEOUT_REQUIRED")
        self.scope = scope
        self.timeout = timeout_seconds
        self.owned_client = http_client is None
        self.http = http_client or httpx.Client(trust_env=False, follow_redirects=False)

    def close(self):
        if self.owned_client:
            self.http.close()

    def _post(self, path, body):
        import httpx

        try:
            with self.http.stream(
                "POST",
                self.scope.endpoint + path,
                json=body,
                headers={"Authorization": "Bearer " + self.scope.token.get_secret_value()},
                timeout=self.timeout,
                follow_redirects=False,
            ) as response:
                status = response.status_code
                if status not in {200, 201}:
                    return status, None
                chunks = []
                length = 0
                for chunk in response.iter_bytes():
                    length += len(chunk)
                    if length > 131072:
                        raise ValueError("NATIVE_CACHE_RESPONSE_BOUND_EXCEEDED")
                    chunks.append(chunk)
            return status, json.loads(b"".join(chunks))
        except (httpx.HTTPError, ValueError, TypeError):
            raise ValueError("NATIVE_CACHE_REPLY_UNAVAILABLE_OR_INVALID") from None


class NativeCacheRuntimeFile(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    version: Literal["cutback.native-cache-runtime.v1"] = "cutback.native-cache-runtime.v1"
    contract: ExactCacheExecution
    scope: NativeCacheScope


def responses_create_from_scope_file(
    client,
    *,
    scope_file,
    expected_workflow,
    expected_request_revision,
    expected_contract_sha256,
    **request,
):
    """Explicit opt-in runtime configuration; preserve the native response object.

    The generated application helper calls this only when its owner sets the
    private configuration path. No application or collector credential is in
    the proposed source. The borrowed native HTTP client remains open.
    """
    from cutback.core.request_profiles import fingerprint

    try:
        path = Path(scope_file)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
            raise ValueError
        with path.open("rb") as stream:
            contents = stream.read(16385)
        if len(contents) > 16384:
            raise ValueError
        configuration = NativeCacheRuntimeFile.model_validate_json(contents)
        scope, contract = configuration.scope, configuration.contract
        if (
            fingerprint(contract.model_dump(mode="json")) != expected_contract_sha256
            or scope.workflow != expected_workflow
            or scope.prompt_revision != expected_request_revision
            or scope.freshness_ref != contract.freshness_ref
            or scope.ttl_seconds != contract.ttl_seconds
        ):
            raise ValueError
    except (ValueError, TypeError, OSError):
        raise ValueError("NATIVE_CACHE_PRIVATE_RUNTIME_CONFIGURATION_INVALID") from None
    # The source contract permits only an omitted store value or literal False.
    # Do not mutate the application's shared SDK client or enable its retries.
    if request.get("store", False) is not False:
        raise ValueError("NATIVE_CACHE_RUNTIME_REQUIRES_STORE_FALSE")
    request = {**request, "store": False}
    from openai import OpenAI

    if not isinstance(client, OpenAI):
        raise ValueError("NATIVE_CACHE_RUNTIME_REQUIRES_QUALIFIED_SYNC_CLIENT")
    borrowed = client.with_options(max_retries=0)
    cache = NativeResponsesCache(scope)
    try:
        return cache.create(borrowed, dispatch_timeout_seconds=30, **request)
    finally:
        cache.close()
