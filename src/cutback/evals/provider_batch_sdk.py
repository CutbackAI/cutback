"""Pinned OpenAI batch adapter. No retries and no polling loop inside an API request."""

from importlib.metadata import version
from time import monotonic

from cutback.core.sdk_compat import OPENAI_TEXT_SDKS
from cutback.evals._logging import owned_sdk_request
from cutback.evals.provider_batch import (
    MAX_INPUT_BYTES,
    MAX_RESULT_BYTES,
    STATUSES,
    file_sha256,
    opaque_id,
    provider_id,
)


class OpenAIBatchAdapter:
    synthetic_transport = False

    def __init__(self, *, api_key, http_client=None):
        if type(api_key) is not str or not api_key or version("openai") not in OPENAI_TEXT_SDKS:
            raise ValueError("BATCH_EXPLICIT_KEY_AND_PINNED_SDK_REQUIRED")
        import httpx
        from openai import OpenAI

        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=30,
            http_client=http_client or httpx.Client(trust_env=False, follow_redirects=False),
        )
        self.client.organization = self.client.project = None
        self.processing_deadline = None

    def _timeout(self):
        remaining = (
            30 if self.processing_deadline is None else self.processing_deadline - monotonic()
        )
        if remaining <= 0:
            raise ValueError("BATCH_ACTIVE_PROCESSING_DEADLINE_EXPIRED")
        return min(30, remaining)

    @owned_sdk_request
    def upload(self, blob, *, operation_id):
        if type(blob) is not bytes or not 1 <= len(blob) <= MAX_INPUT_BYTES:
            raise ValueError("BATCH_UPLOAD_BYTE_BOUND")
        filename = "cutback-" + opaque_id(operation_id) + ".jsonl"
        result = self.client.files.create(
            file=(filename, blob, "application/jsonl"),
            purpose="batch",
            expires_after={"anchor": "created_at", "seconds": 172800},
            timeout=self._timeout(),
        )
        identifier = provider_id(result.id, "file")
        if result.purpose != "batch" or result.bytes != len(blob) or result.filename != filename:
            raise ValueError("BATCH_UPLOADED_FILE_SCOPE_MISMATCH")
        return {"file_id": identifier, "sha256": file_sha256(blob), "bytes": len(blob)}

    @staticmethod
    def _input_metadata(result, operation_id, byte_count):
        if (
            result.purpose != "batch"
            or result.bytes != byte_count
            or result.filename != "cutback-" + opaque_id(operation_id) + ".jsonl"
        ):
            raise ValueError("BATCH_INPUT_FILE_IDENTITY_MISMATCH")
        return {"file_id": provider_id(result.id, "file"), "bytes": result.bytes}

    @owned_sdk_request
    def input_metadata(self, file_id, *, operation_id, byte_count):
        result = self.client.files.retrieve(provider_id(file_id, "file"), timeout=self._timeout())
        snapshot = self._input_metadata(result, operation_id, byte_count)
        if snapshot["file_id"] != file_id:
            raise ValueError("BATCH_INPUT_FILE_IDENTITY_MISMATCH")
        return snapshot

    @owned_sdk_request
    def find_lost_upload(self, operation_id, byte_count):
        matches, after = [], None
        filename = "cutback-" + opaque_id(operation_id) + ".jsonl"
        for _ in range(5):
            page = self.client.files.list(
                purpose="batch",
                limit=100,
                order="desc",
                timeout=self._timeout(),
                **({"after": after} if after else {}),
            )
            for result in page.data:
                if result.filename == filename:
                    matches.append(self._input_metadata(result, operation_id, byte_count))
                    if len(matches) > 1:
                        raise ValueError("BATCH_MULTIPLE_INPUT_FILES_REQUIRE_MANUAL_RECONCILIATION")
            if not page.has_more:
                return matches[0] if matches else None
            if not page.data:
                raise ValueError("BATCH_UPLOAD_RECONCILIATION_PAGE_INVALID")
            after = provider_id(page.data[-1].id, "file")
        raise ValueError("BATCH_UPLOAD_RECONCILIATION_SEARCH_INCOMPLETE")

    @owned_sdk_request
    def submit(self, file_id, operation_id):
        # The worker must durably reserve every item and record intent before
        # reaching this method. A lost acknowledgement may already be billable.
        result = self.client.batches.create(
            input_file_id=provider_id(file_id, "file"),
            completion_window="24h",
            endpoint="/v1/responses",
            metadata={"cutback_operation_id": opaque_id(operation_id)},
            output_expires_after={"anchor": "created_at", "seconds": 172800},
            timeout=self._timeout(),
        )
        return self._snapshot(result, file_id=file_id, operation_id=operation_id)

    @staticmethod
    def _snapshot(result, *, file_id, operation_id):
        identifier = provider_id(result.id, "batch")
        if (
            result.input_file_id != provider_id(file_id, "file")
            or result.endpoint != "/v1/responses"
            or result.completion_window != "24h"
            or not result.metadata
            or result.metadata.get("cutback_operation_id") != opaque_id(operation_id)
            or result.status not in STATUSES
        ):
            raise ValueError("BATCH_PROVIDER_JOB_SCOPE_MISMATCH")
        counts = result.request_counts
        if counts is not None and any(
            type(value) is not int or not 0 <= value <= 1000
            for value in (counts.total, counts.completed, counts.failed)
        ):
            raise ValueError("BATCH_PROVIDER_JOB_COUNTS_INVALID")
        if counts is not None and counts.completed + counts.failed > counts.total:
            raise ValueError("BATCH_PROVIDER_JOB_COUNTS_INVALID")
        return {
            "batch_id": identifier,
            "status": result.status,
            "output_file_id": provider_id(result.output_file_id, "file")
            if result.output_file_id
            else None,
            "error_file_id": provider_id(result.error_file_id, "file")
            if result.error_file_id
            else None,
            "total": counts.total if counts else None,
            "completed": counts.completed if counts else None,
            "failed": counts.failed if counts else None,
        }

    @owned_sdk_request
    def poll(self, batch_id, *, file_id, operation_id):
        result = self.client.batches.retrieve(
            provider_id(batch_id, "batch"), timeout=self._timeout()
        )
        snapshot = self._snapshot(result, file_id=file_id, operation_id=operation_id)
        if snapshot["batch_id"] != batch_id:
            raise ValueError("BATCH_PROVIDER_JOB_SCOPE_MISMATCH")
        return snapshot

    @owned_sdk_request
    def cancel(self, batch_id, *, file_id, operation_id):
        result = self.client.batches.cancel(provider_id(batch_id, "batch"), timeout=self._timeout())
        snapshot = self._snapshot(result, file_id=file_id, operation_id=operation_id)
        if snapshot["batch_id"] != batch_id:
            raise ValueError("BATCH_PROVIDER_JOB_SCOPE_MISMATCH")
        return snapshot

    @owned_sdk_request
    def find_lost_submission(self, file_id, operation_id):
        """Read-only bounded lookup; never creates or resubmits a provider job.

        An incomplete search cannot prove that no job exists. Multiple matches
        likewise require operator reconciliation and cannot choose arbitrarily.
        """
        file_id, operation_id = provider_id(file_id, "file"), opaque_id(operation_id)
        matches, after = [], None
        for _ in range(5):
            page = self.client.batches.list(
                limit=100, timeout=self._timeout(), **({"after": after} if after else {})
            )
            for result in page.data:
                if result.metadata and result.metadata.get("cutback_operation_id") == operation_id:
                    matches.append(
                        self._snapshot(result, file_id=file_id, operation_id=operation_id)
                    )
                    if len(matches) > 1:
                        raise ValueError("BATCH_MULTIPLE_SUBMISSIONS_REQUIRE_MANUAL_RECONCILIATION")
            if not page.has_more:
                return matches[0] if matches else None
            if not page.data:
                raise ValueError("BATCH_RECONCILIATION_PAGE_INVALID")
            after = provider_id(page.data[-1].id, "batch")
        raise ValueError("BATCH_RECONCILIATION_SEARCH_INCOMPLETE")

    @owned_sdk_request
    def download(self, file_id):
        # Streaming prevents an unbounded provider body from being read by the
        # eager legacy files.content interface. The caller parses all files
        # together before settling any canonical reservations.
        with self.client.files.with_streaming_response.content(
            provider_id(file_id, "file"), timeout=self._timeout()
        ) as response:
            chunks, size = [], 0
            for part in response.iter_bytes(chunk_size=65536):
                self._timeout()
                size += len(part)
                if size > MAX_RESULT_BYTES:
                    raise ValueError("BATCH_RESULT_FILE_BYTE_BOUND")
                chunks.append(part)
        return chunks

    @owned_sdk_request
    def delete_file(self, file_id):
        identifier = provider_id(file_id, "file")
        from openai import NotFoundError

        try:
            result = self.client.files.delete(identifier, timeout=self._timeout())
        except NotFoundError:
            return  # Exact scoped native file is already absent after a lost deletion ACK.
        if result.id != identifier or result.deleted is not True:
            raise ValueError("BATCH_PROVIDER_DELETION_UNCONFIRMED")

    def close(self):
        self.client.close()
