"""Real transactional records used only inside the separately isolated tool runtime.

This is a bounded in-memory SQLite adapter, not a fixture-response dispatcher.
It has no file, shell, network or arbitrary SQL API. Runtime isolation is supplied
by the separate broker, not claimed merely by instantiating this class.
"""

import hashlib
import hmac
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID

from cutback.core.tool_policy import (
    IsolatedToolPolicy,
    RecordPatch,
    RecordRead,
    RecordSeed,
    canonical,
    content_bound,
    native_patch_arguments,
)


class IsolatedRecords:
    def __init__(self, policy: IsolatedToolPolicy, seeds, receipt_key: bytes):
        if (
            not isinstance(policy, IsolatedToolPolicy)
            or type(receipt_key) is not bytes
            or len(receipt_key) != 32
        ):
            raise ValueError("ISOLATED_TOOL_VALIDATED_POLICY_AND_RECEIPT_KEY_REQUIRED")
        # Capture values independently of mutable caller-owned nested objects.
        self._policy = IsolatedToolPolicy.model_validate_json(policy.model_dump_json())
        self._approved_hash = self._policy.policy_sha256
        self._receipt_key = receipt_key
        now = datetime.now(UTC)
        if not now < policy.expires_at <= now + timedelta(hours=1):
            raise ValueError("ISOLATED_TOOL_EXPIRY_OUTSIDE_SESSION_BOUND")
        self._deadline = monotonic() + min(
            policy.max_wall_seconds, (policy.expires_at - now).total_seconds()
        )
        if not isinstance(seeds, (list, tuple)) or not 1 <= len(seeds) <= 64:
            raise ValueError("ISOLATED_TOOL_RECORD_COUNT_BOUND")
        if any(not isinstance(seed, RecordSeed) for seed in seeds):
            raise ValueError("ISOLATED_TOOL_VALIDATED_RECORD_SEEDS_REQUIRED")
        records = [RecordSeed.model_validate_json(seed.model_dump_json()) for seed in seeds]
        if len({record.id for record in records}) != len(records):
            raise ValueError("ISOLATED_TOOL_DUPLICATE_RECORD_ID")
        content_bound([record.model_dump(mode="json") for record in records], 256000)
        self._records = sqlite3.connect(":memory:", isolation_level=None)
        self._records.execute("PRAGMA trusted_schema=OFF")
        self._records.execute(
            "CREATE TABLE records (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
            "owner_id TEXT NOT NULL, environment TEXT NOT NULL, "
            "version INTEGER NOT NULL, fields TEXT NOT NULL)"
        )
        self._records.execute(
            "CREATE TABLE effects (operation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, "
            "receipt TEXT NOT NULL)"
        )
        self._records.executemany(
            "INSERT INTO records VALUES (?,?,?,?,?,?)",
            [
                (
                    str(record.id),
                    str(record.project_id),
                    str(record.owner_id),
                    record.environment,
                    record.version,
                    canonical(record.fields).decode(),
                )
                for record in records
            ],
        )
        self._grants = {grant.resource_id: grant for grant in self._policy.grants}
        self._frames = 0
        self._closed = False

    def _digest(self, value):
        return hmac.new(self._receipt_key, canonical(value), hashlib.sha256).hexdigest()

    def _current(self):
        return (
            not self._closed
            and self._policy.policy_sha256 == self._approved_hash
            and datetime.now(UTC) < self._policy.expires_at
            and monotonic() < self._deadline
        )

    def execute(self, operation_id: UUID, tool: str, arguments: dict):
        """A retry can read an identical committed local effect; it never reapplies it."""
        if not isinstance(operation_id, UUID) or operation_id.int == 0:
            raise ValueError("ISOLATED_TOOL_OPERATION_UUID_REQUIRED")
        self._frames += 1
        if not self._current() or self._frames > self._policy.max_calls * 2:
            return self._receipt(
                operation_id, "denied", "ISOLATED_TOOL_SESSION_EXPIRED_OR_EXHAUSTED"
            )
        if type(tool) is not str or len(tool) > 64:
            return self._receipt(operation_id, "denied", "ISOLATED_TOOL_NOT_APPROVED")
        try:
            content_bound(arguments, 32000)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return self._receipt(operation_id, "denied", "ISOLATED_TOOL_ARGUMENTS_INVALID")
        request_hash = self._digest(
            {"policy": self._approved_hash, "tool": tool, "arguments": arguments}
        )
        self._records.execute("BEGIN IMMEDIATE")
        try:
            previous = self._records.execute(
                "SELECT request_hash,receipt FROM effects WHERE operation_id=?",
                (str(operation_id),),
            ).fetchone()
            if previous:
                if previous[0] != request_hash:
                    result = self._receipt(
                        operation_id, "denied", "ISOLATED_TOOL_OPERATION_CONTENT_CONFLICT"
                    )
                else:
                    result = {**json.loads(previous[1]), "replayed": True}
                self._records.execute("COMMIT")
                return result
            count = self._records.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
            if count >= self._policy.max_calls:
                result = self._receipt(operation_id, "denied", "ISOLATED_TOOL_CALL_LIMIT")
            else:
                result = self._perform(operation_id, tool, arguments)
                self._records.execute(
                    "INSERT INTO effects VALUES (?,?,?)",
                    (str(operation_id), request_hash, canonical(result).decode()),
                )
            self._records.execute("COMMIT")
            return result
        except BaseException:
            if self._records.in_transaction:
                self._records.execute("ROLLBACK")
            raise

    def _receipt(self, operation_id, status, code, response=None, *, changed=False):
        return {
            "operation_id": str(operation_id),
            "status": status,
            "code": code,
            "response": response,
            "state_changed": changed,
            "replayed": False,
            "policy_sha256": self._approved_hash,
            "tool_cost_usd": None,
            "complete_task_cost_usd": None,
        }

    def _perform(self, operation_id, tool, arguments):
        if tool not in {"records_read", "records_patch"}:
            return self._receipt(operation_id, "denied", "ISOLATED_TOOL_NOT_APPROVED")
        try:
            body = (
                RecordRead.model_validate(arguments)
                if tool == "records_read"
                else RecordPatch.model_validate(native_patch_arguments(arguments))
            )
        except ValueError:
            return self._receipt(operation_id, "denied", "ISOLATED_TOOL_ARGUMENTS_INVALID")
        grant = self._grants.get(body.resource_id)
        record = self._records.execute(
            "SELECT version,fields FROM records "
            "WHERE id=? AND project_id=? AND owner_id=? AND environment=?",
            (
                str(body.resource_id),
                str(self._policy.project_id),
                str(self._policy.principal_id),
                self._policy.environment,
            ),
        ).fetchone()
        if not grant or record is None:
            return self._receipt(operation_id, "denied", "ISOLATED_TOOL_RESOURCE_OR_SCOPE_DENIED")
        version, encoded = record
        fields = json.loads(encoded)
        changed = tool == "records_patch"
        if isinstance(body, RecordPatch):
            if not self._policy.write_approved or body.expected_version != version:
                return self._receipt(
                    operation_id,
                    "denied",
                    "ISOLATED_TOOL_CHANGE_NOT_AUTHORIZED_OR_PRECONDITION_FAILED",
                )
            for name, after in body.updates.items():
                if name not in fields or not any(
                    change.field == name
                    and canonical(change.before) == canonical(fields[name])
                    and canonical(change.after) == canonical(after)
                    for change in grant.transitions
                ):
                    return self._receipt(
                        operation_id,
                        "denied",
                        "ISOLATED_TOOL_CHANGE_NOT_AUTHORIZED_OR_PRECONDITION_FAILED",
                    )
            fields = {**fields, **body.updates}
            version += 1
        response = {
            "resource_id": str(body.resource_id),
            "version": version,
            "fields": {name: fields[name] for name in grant.read_fields if name in fields},
        }
        if len(canonical(response)) > self._policy.max_result_bytes:
            return self._receipt(operation_id, "denied", "ISOLATED_TOOL_RESPONSE_BYTE_BOUND")
        if changed:
            projected = self._snapshot()
            projected[str(body.resource_id)] = {
                **projected[str(body.resource_id)],
                "version": version,
                "fields": fields,
            }
            try:
                content_bound(fields, 32000)
                content_bound(projected, 256000)
            except ValueError:
                return self._receipt(operation_id, "denied", "ISOLATED_TOOL_STATE_BYTE_BOUND")
        if not self._current():
            return self._receipt(
                operation_id, "denied", "ISOLATED_TOOL_SESSION_EXPIRED_OR_EXHAUSTED"
            )
        if changed:
            self._records.execute(
                "UPDATE records SET version=?,fields=? "
                "WHERE id=? AND project_id=? AND owner_id=? AND environment=? AND version=?",
                (
                    version,
                    canonical(fields).decode(),
                    str(body.resource_id),
                    str(self._policy.project_id),
                    str(self._policy.principal_id),
                    self._policy.environment,
                    version - 1,
                ),
            )
        return self._receipt(
            operation_id, "returned", "ISOLATED_TOOL_OPERATION_COMPLETED", response, changed=changed
        )

    def owner_snapshot(self):
        """Trusted evaluator channel; never a model-visible tool definition."""
        if not self._current():
            raise ValueError("ISOLATED_TOOL_SESSION_EXPIRED_OR_EXHAUSTED")
        return self._snapshot()

    def _snapshot(self):
        return {
            identity: {
                "project_id": project,
                "owner_id": owner,
                "environment": environment,
                "version": version,
                "fields": json.loads(fields),
            }
            for identity, project, owner, environment, version, fields in self._records.execute(
                "SELECT id,project_id,owner_id,environment,version,fields FROM records ORDER BY id"
            )
        }

    def metadata(self):
        """No raw values, arguments, answers, record IDs or unkeyed content fingerprints."""
        if self._closed:
            raise ValueError("ISOLATED_TOOL_SESSION_CLOSED")
        return {
            "policy_sha256": self._approved_hash,
            "effects": self._records.execute("SELECT COUNT(*) FROM effects").fetchone()[0],
            "state_hmac_sha256": self._digest(self.owner_snapshot()),
            "tool_cost_usd": None,
            "complete_task_cost_usd": None,
        }

    def close(self):
        self._records.close()
        self._closed = True
