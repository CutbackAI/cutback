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
from cutback.evals._logging import owned_sdk_request

MAX_POLICY_BYTES = SOURCE_POLICY_BYTES_BOUND


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
    if policy.cache_execution is None or scope_file is None:
        return client.responses.create(**request)
    from cutback.native_cache import responses_create_from_scope_file

    return responses_create_from_scope_file(
        client,
        scope_file=scope_file,
        expected_workflow=policy.workflow,
        expected_request_revision=policy.policy_sha256,
        expected_contract_sha256=fingerprint(policy.cache_execution.model_dump(mode="json")),
        **request,
    ).response
