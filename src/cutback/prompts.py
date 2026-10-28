"""Explicit prompt resolution with local variable rendering; no provider or disk cache."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from cutback.core.config import validate_endpoint
from cutback.core.prompts import PromptDocument, PromptError, PromptGraph, fingerprint


@dataclass(frozen=True)
class ResolvedPrompt:
    graph: PromptGraph
    version_hash: str
    expires_at: datetime
    alias_receipt: dict | None

    def render(self, variables):
        if self.expires_at <= datetime.now(UTC):
            raise PromptError("RESOLVED_PROMPT_EXPIRED")
        return self.graph.render(variables)


class PromptClient:
    """Use a dedicated prompt_reader credential, independently of telemetry ingestion."""

    def __init__(
        self, endpoint: str, project_id: UUID | str, token: str, *, timeout=10, transport=None
    ):
        import httpx

        if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,8192}", token):
            raise PromptError("PROMPT_READER_CREDENTIAL_INVALID")
        if not 0 < timeout <= 30:
            raise PromptError("PROMPT_CLIENT_TIMEOUT_INVALID")
        self._project = UUID(str(project_id))
        self._endpoint = validate_endpoint(endpoint)
        self._client = httpx.Client(
            headers={"Authorization": "Bearer " + token},
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def resolve(
        self, key, *, environment, workflow, provider, purpose, version_id=None, alias=None
    ):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", key):
            raise PromptError("PROMPT_KEY_INVALID")
        if (version_id is None) == (alias is None):
            raise PromptError("SELECT_ONE_PINNED_PROMPT_OR_ALIAS")
        body = {
            "environment": environment,
            "workflow": workflow,
            "provider": provider,
            "purpose": purpose,
            "version_id": str(UUID(str(version_id))) if version_id else None,
            "alias": alias,
        }
        url = f"{self._endpoint}/v1/projects/{self._project}/prompts/resolve/{key}"
        try:
            with self._client.stream("POST", url, json=body) as response:
                if response.status_code != 200:
                    # Neither a provider error body nor a content-bearing URL enters diagnostics.
                    raise PromptError("PROMPT_RESOLUTION_DENIED_OR_UNAVAILABLE")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 1048576:
                        raise PromptError("PROMPT_RESPONSE_BYTES_EXCEEDED")
                    chunks.append(chunk)
                raw = json.loads(b"".join(chunks))
        except PromptError:
            raise
        except Exception:
            raise PromptError("PROMPT_RESOLUTION_UNAVAILABLE") from None
        try:
            root = UUID(raw["root_version_id"])
            if version_id and root != UUID(str(version_id)):
                raise ValueError()
            docs, hashes, receipts = (
                raw["documents"],
                raw["version_hashes"],
                raw["content_receipts"],
            )
            if (
                not isinstance(docs, dict)
                or not 1 <= len(docs) <= 32
                or set(docs) != set(hashes)
                or set(docs) != set(receipts)
            ):
                raise ValueError()
            documents = {}
            for key, value in docs.items():
                receipt = receipts[key]
                if set(receipt) != {"receipt_nonce", "request_fingerprint"}:
                    raise ValueError()
                document = PromptDocument.model_validate(value)
                if (
                    document.model_dump(mode="json") != value
                    or fingerprint({"document": value, **receipt}) != hashes[key]
                ):
                    raise ValueError()
                documents[UUID(key)] = document
            graph = PromptGraph(root, documents)
            if set(graph.inspect()["version_ids"]) != set(documents):
                raise ValueError()
            expiry = datetime.fromisoformat(raw["expires_at"])
            if (
                not expiry.tzinfo
                or expiry <= datetime.now(UTC)
                or hashes[str(root)] != raw["version_hash"]
            ):
                raise ValueError()
            return ResolvedPrompt(graph, raw["version_hash"], expiry, raw.get("alias"))
        except Exception:
            raise PromptError("PROMPT_RESPONSE_INTEGRITY_OR_EXPIRY_INVALID") from None

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
