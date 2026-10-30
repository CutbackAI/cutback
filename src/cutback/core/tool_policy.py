"""Frozen authority for isolated record tools, independent from model instructions.

This module validates policy only. It neither starts a container nor executes a
tool, discovers credentials, accesses files, or opens a network connection.
"""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, JsonValue, StrictBool, field_validator, model_validator

from cutback.core.models import Identifier, StrictModel


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode()


def content_bound(value, maximum):
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 10000 or depth > 16:
            raise ValueError("ISOLATED_TOOL_JSON_STRUCTURE_BOUND")
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise ValueError("ISOLATED_TOOL_STRING_KEYS_REQUIRED")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    if len(canonical(value)) > maximum:
        raise ValueError("ISOLATED_TOOL_CONTENT_BYTE_BOUND")


def field_name(value):
    import re

    if (
        type(value) is not str
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", value)
        or value
        in {
            "constructor",
            "prototype",
            "__proto__",
            "project_id",
            "owner_id",
            "environment",
            "id",
            "version",
        }
    ):
        raise ValueError("ISOLATED_TOOL_FIELD_DENIED")
    return value


class FieldTransition(StrictModel):
    field: str
    before: JsonValue
    after: JsonValue

    @model_validator(mode="after")
    def bounded(self):
        field_name(self.field)
        content_bound(self.model_dump(mode="json"), 4096)
        if canonical(self.before) == canonical(self.after):
            raise ValueError("ISOLATED_TOOL_TRANSITION_MUST_CHANGE_STATE")
        return self


class RecordGrant(StrictModel):
    resource_id: UUID
    read_fields: tuple[str, ...] = Field(default=(), max_length=32)
    transitions: tuple[FieldTransition, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def bounded(self):
        for name in self.read_fields:
            field_name(name)
        if self.resource_id.int == 0 or len(set(self.read_fields)) != len(self.read_fields):
            raise ValueError("ISOLATED_TOOL_UNIQUE_RESOURCE_AND_FIELDS_REQUIRED")
        frozen = [canonical(change.model_dump(mode="json")) for change in self.transitions]
        if len(set(frozen)) != len(frozen):
            raise ValueError("ISOLATED_TOOL_DUPLICATE_TRANSITION")
        return self


class RecordSeed(StrictModel):
    id: UUID
    project_id: UUID
    owner_id: UUID
    environment: Identifier = "staging"
    version: int = Field(default=0, strict=True, ge=0, le=1000000)
    fields: dict[str, JsonValue] = Field(max_length=32)

    @model_validator(mode="after")
    def bounded(self):
        if any(value.int == 0 for value in (self.id, self.project_id, self.owner_id)):
            raise ValueError("ISOLATED_TOOL_NONZERO_RECORD_SCOPE_REQUIRED")
        for name in self.fields:
            field_name(name)
        content_bound(self.model_dump(mode="json"), 32000)
        return self


class IsolatedToolPolicy(StrictModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    version: Literal["cutback.isolated-record-tools.v1"] = "cutback.isolated-record-tools.v1"
    project_id: UUID
    principal_id: UUID
    environment: Identifier = "staging"
    workflow: Identifier = "isolated-record-task"
    session_id: UUID
    content_nonce: UUID = Field(default_factory=uuid4)
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    grants: tuple[RecordGrant, ...] = Field(min_length=1, max_length=64)
    write_approved: StrictBool = False
    max_calls: int = Field(default=16, strict=True, ge=1, le=100)
    max_wall_seconds: int = Field(default=60, strict=True, ge=1, le=120)
    max_result_bytes: int = Field(default=16000, strict=True, ge=256, le=64000)
    expires_at: datetime
    content_use_approved: Literal[True]

    @field_validator("content_use_approved", mode="before")
    @classmethod
    def explicit_content_approval(cls, value):
        if value is not True:
            raise ValueError("ISOLATED_TOOL_EXPLICIT_CONTENT_AUTHORITY_REQUIRED")
        return value

    @model_validator(mode="after")
    def authority(self):
        if any(
            value.int == 0
            for value in (self.project_id, self.principal_id, self.session_id, self.content_nonce)
        ):
            raise ValueError("ISOLATED_TOOL_NONZERO_SCOPE_REQUIRED")
        if self.expires_at.tzinfo is None:
            raise ValueError("ISOLATED_TOOL_AWARE_EXPIRY_REQUIRED")
        ids = [grant.resource_id for grant in self.grants]
        if len(set(ids)) != len(ids):
            raise ValueError("ISOLATED_TOOL_UNIQUE_GRANTS_REQUIRED")
        if not self.write_approved and any(grant.transitions for grant in self.grants):
            raise ValueError("ISOLATED_TOOL_WRITE_AUTHORITY_REQUIRED")
        content_bound(self.model_dump(mode="json"), 256000)
        return self

    @property
    def policy_sha256(self):
        return hashlib.sha256(canonical(self.model_dump(mode="json"))).hexdigest()


class RecordRead(StrictModel):
    resource_id: UUID


class RecordPatch(StrictModel):
    resource_id: UUID
    expected_version: int = Field(strict=True, ge=0, le=1000000)
    updates: dict[str, JsonValue] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def bounded(self):
        for name in self.updates:
            field_name(name)
        content_bound(self.model_dump(mode="json"), 32000)
        return self


def native_patch_arguments(arguments):
    """Closed native schema transports the bounded update object as JSON text.

    This changes no grant: parsed values still pass the ordinary CAS/transition
    validator. Duplicate keys and nonfinite/overly nested values are refused.
    """
    if type(arguments) is not dict or "updates_json" not in arguments:
        return arguments
    if (
        set(arguments) != {"resource_id", "expected_version", "updates_json"}
        or type(arguments["updates_json"]) is not str
    ):
        raise ValueError("ISOLATED_TOOL_NATIVE_PATCH_SCHEMA_DENIED")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE")
            result[key] = value
        return result

    try:
        updates = json.loads(arguments["updates_json"], object_pairs_hook=unique)
        content_bound(updates, 32000)
        return {
            "resource_id": arguments["resource_id"],
            "expected_version": arguments["expected_version"],
            "updates": updates,
        }
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise ValueError("ISOLATED_TOOL_NATIVE_PATCH_JSON_DENIED") from None


class ToolContainmentScope(StrictModel):
    """Content-free identity retained for cleanup after content expiry or restart."""

    session_id: UUID
    project_id: UUID
    principal_id: UUID
    broker_id: UUID
    environment: Identifier
    workflow: Identifier
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ToolPolicyMetadata(StrictModel):
    """The only policy fields retained by a closed broker/driver session."""

    project_id: UUID
    principal_id: UUID
    environment: Identifier
    workflow: Identifier
    session_id: UUID
    image_id: str
    expires_at: datetime
    max_calls: int
    max_wall_seconds: int
    policy_sha256: str

    @classmethod
    def capture(cls, policy):
        return cls.model_validate({name: getattr(policy, name) for name in cls.model_fields})
