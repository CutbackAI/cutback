"""Native synchronous execution of an explicit bounded source policy.

This runtime does not choose improvements, approve inference or retry/switch a
target. Ordinary native retries remain the application's choice. Optional exact
caching uses the existing separately scoped, encrypted, one-shot cache adapter.
"""

import json
from importlib.metadata import version
from pathlib import Path

from cutback.core.improvement_policy import SOURCE_POLICY_BYTES_BOUND, SourceImprovementPolicy
from cutback.core.request_profiles import fingerprint
from cutback.core.sdk_compat import OPENAI_TEXT_SDKS
from cutback.evals._logging import owned_async_sdk_request, owned_sdk_request

MAX_POLICY_BYTES = SOURCE_POLICY_BYTES_BOUND


def _activity(policy, requested=False):
    from cutback.instrumentation import _begin_policy_activity

    cache = policy.cache_execution
    return _begin_policy_activity(
        workflow=policy.workflow,
        policy_sha256=policy.policy_sha256,
        cache_configured=cache is not None,
        cache_requested=cache is not None and requested,
        cache_operation_upper_usd=cache.operation_cost_upper_usd if cache else None,
        cache_cost_basis_ref=cache.operation_cost_basis_ref if cache else None,
    )


def parse_policy(raw: bytes):
    def unique(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError("SOURCE_POLICY_DUPLICATE_CONFIGURATION_KEY")
            values[key] = value
        return values

    try:
        if type(raw) is not bytes or len(raw) > MAX_POLICY_BYTES:
            raise ValueError
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        return SourceImprovementPolicy.model_validate(value)
    except (OSError, ValueError, RecursionError, TypeError):
        raise ValueError("SOURCE_POLICY_CONFIGURATION_UNAVAILABLE_OR_INVALID") from None


def load_policy(path: Path):
    path = Path(path).absolute()
    try:
        if (
            path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents)
            or not path.is_file()
            or any(
                getattr(item.lstat(), "st_file_attributes", 0) & 1024
                for item in (path, *path.parents)
            )
        ):
            raise ValueError
    except (OSError, ValueError):
        raise ValueError("SOURCE_POLICY_ORDINARY_CONFIGURATION_FILE_REQUIRED") from None
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_POLICY_BYTES + 1)
    except OSError:
        raise ValueError("SOURCE_POLICY_CONFIGURATION_UNAVAILABLE_OR_INVALID") from None
    return parse_policy(raw)


@owned_sdk_request
def responses_create(client, text: str, policy: SourceImprovementPolicy, *, scope_file=None):
    from openai import OpenAI

    if type(client) is not OpenAI or version("openai") not in OPENAI_TEXT_SDKS:
        raise ValueError("SOURCE_POLICY_QUALIFIED_SYNCHRONOUS_CLIENT_REQUIRED")
    if str(client.base_url) != "https://api.openai.com/v1/":
        raise ValueError("SOURCE_POLICY_OFFICIAL_NATIVE_ENDPOINT_REQUIRED")
    # Revalidate public data, including callers holding a modified model instance.
    try:
        policy = SourceImprovementPolicy.model_validate(policy.model_dump(mode="json"))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("SOURCE_POLICY_CURRENT_QUALIFIED_POLICY_REQUIRED") from None
    request = policy.request(text)
    from cutback.instrumentation import _finish_policy_activity

    activity = _activity(policy, scope_file is not None)
    try:
        if policy.cache_execution is None or scope_file is None:
            response = client.responses.create(**request)
            _finish_policy_activity(activity, completion="completed")
            return response
        from cutback.native_cache import responses_create_from_scope_file

        result = responses_create_from_scope_file(
            client,
            scope_file=scope_file,
            expected_workflow=policy.workflow,
            expected_request_revision=policy.policy_sha256,
            expected_contract_sha256=fingerprint(policy.cache_execution.model_dump(mode="json")),
            **request,
        )
        _finish_policy_activity(activity, completion="completed", receipt=result.receipt)
        return result.response
    except BaseException as error:
        _finish_policy_activity(
            activity, completion="failed" if isinstance(error, Exception) else "cancelled"
        )
        raise


@owned_async_sdk_request
async def responses_create_async(client, text: str, policy: SourceImprovementPolicy):
    """Borrow an exact native async client; uncached, nonstreaming and no fallback."""
    from openai import AsyncOpenAI

    if type(client) is not AsyncOpenAI or version("openai") not in OPENAI_TEXT_SDKS:
        raise ValueError("SOURCE_POLICY_QUALIFIED_ASYNCHRONOUS_CLIENT_REQUIRED")
    if str(client.base_url) != "https://api.openai.com/v1/":
        raise ValueError("SOURCE_POLICY_OFFICIAL_NATIVE_ENDPOINT_REQUIRED")
    try:
        policy = SourceImprovementPolicy.model_validate(policy.model_dump(mode="json"))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("SOURCE_POLICY_CURRENT_QUALIFIED_POLICY_REQUIRED") from None
    if policy.cache_execution is not None:
        raise ValueError("POLICY_SOURCE_ASYNC_CACHE_RUNTIME_UNQUALIFIED")
    request = policy.request(text)
    from cutback.instrumentation import _finish_policy_activity

    activity = _activity(policy)
    try:
        response = await client.responses.create(**request)
        _finish_policy_activity(activity, completion="completed")
        return response
    except BaseException as error:
        _finish_policy_activity(
            activity, completion="failed" if isinstance(error, Exception) else "cancelled"
        )
        raise
