"""Frozen guardrail authority. Parse-only; no classifiers, providers or side effects."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.tool_policy import canonical, content_bound

GuardrailStage = Literal["input", "output", "tool_input", "tool_output"]


class LiteralDenyRule(StrictModel):
    kind: Literal["literal_deny"] = "literal_deny"
    key: Identifier
    stages: tuple[GuardrailStage, ...] = Field(min_length=1, max_length=4)
    terms: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = Field(
        min_length=1, max_length=64
    )
    normalization: Literal["exact", "nfkc_casefold"] = "exact"


class JSONShapeRule(StrictModel):
    kind: Literal["json_shape"] = "json_shape"
    key: Identifier
    stages: tuple[GuardrailStage, ...] = Field(min_length=1, max_length=4)
    schema_definition: dict[str, JsonValue]

    @model_validator(mode="after")
    def bounded_tree_schema(self):
        # A deliberately bounded structural subset. No reference resolver, regex,
        # external format checker, executable vocabulary or recursive schema.
        allowed = {
            "type",
            "properties",
            "required",
            "additionalProperties",
            "items",
            "minItems",
            "maxItems",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
            "enum",
            "const",
            "description",
            "title",
        }
        pending: list[tuple[Any, int]] = [(self.schema_definition, 0)]
        nodes = 0
        while pending:
            schema, depth = pending.pop()
            nodes += 1
            if type(schema) is not dict or depth > 8 or nodes > 128 or not set(schema) <= allowed:
                raise ValueError("GUARDRAIL_STRUCTURAL_SCHEMA_OUTSIDE_BOUNDED_SUBSET")
            properties = schema.get("properties", {})
            if type(properties) is not dict or len(properties) > 64:
                raise ValueError("GUARDRAIL_SCHEMA_PROPERTIES_BOUND")
            pending.extend((value, depth + 1) for value in properties.values())
            if "items" in schema:
                pending.append((schema["items"], depth + 1))
            if (
                "additionalProperties" in schema
                and type(schema["additionalProperties"]) is not bool
            ):
                raise ValueError("GUARDRAIL_SCHEMA_ADDITIONAL_PROPERTIES_BOOLEAN_REQUIRED")
        content_bound(self.schema_definition, 16384)
        return self


class ToolAllowlistRule(StrictModel):
    kind: Literal["tool_allowlist"] = "tool_allowlist"
    key: Identifier
    stages: tuple[Literal["tool_input"], ...] = ("tool_input",)
    names: tuple[Identifier, ...] = Field(min_length=1, max_length=64)


GuardrailRule = Annotated[
    LiteralDenyRule | JSONShapeRule | ToolAllowlistRule, Field(discriminator="kind")
]


class GuardrailPolicy(StrictModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    version: Literal["cutback.guardrail-policy.v1"] = "cutback.guardrail-policy.v1"
    policy_id: UUID
    project_id: UUID
    principal_id: UUID
    environment: Identifier
    workflows: tuple[Identifier, ...] = Field(min_length=1, max_length=100)
    content_nonce: UUID = Field(default_factory=uuid4)
    starts_at: datetime
    expires_at: datetime
    protected_stages: tuple[GuardrailStage, ...] = Field(min_length=1, max_length=4)
    rules: tuple[GuardrailRule, ...] = Field(min_length=1, max_length=64)
    on_unavailable: Literal["fail_closed", "fail_open"] = "fail_closed"
    max_content_bytes: int = Field(default=64000, strict=True, ge=256, le=1048576)
    max_evaluation_ms: int = Field(default=50, strict=True, ge=1, le=1000)
    content_use_approved: Literal[True]

    @field_validator("content_use_approved", mode="before")
    @classmethod
    def explicit_content_authority(cls, value):
        if value is not True:
            raise ValueError("GUARDRAIL_EXPLICIT_CONTENT_CONSENT_REQUIRED")
        return value

    @model_validator(mode="after")
    def explicit_finite_authority(self):
        if (
            any(
                value.int == 0
                for value in (
                    self.policy_id,
                    self.project_id,
                    self.principal_id,
                    self.content_nonce,
                )
            )
            or self.expires_at.tzinfo is None
            or self.starts_at.tzinfo is None
        ):
            raise ValueError("GUARDRAIL_NONZERO_SCOPE_AND_AWARE_EXPIRY_REQUIRED")
        if not timedelta(0) < self.expires_at - self.starts_at <= timedelta(days=31):
            raise ValueError("GUARDRAIL_FINITE_APPROVAL_WINDOW_REQUIRED")
        if any(
            len(set(values)) != len(values)
            for values in (
                self.workflows,
                self.protected_stages,
                tuple(rule.key for rule in self.rules),
            )
        ):
            raise ValueError("GUARDRAIL_UNIQUE_SCOPE_AND_RULE_KEYS_REQUIRED")
        stages = set(self.protected_stages)
        if {stage for rule in self.rules for stage in rule.stages} != stages:
            raise ValueError("GUARDRAIL_EVERY_PROTECTED_STAGE_REQUIRES_DECLARED_RULES")
        for rule in self.rules:
            if len(set(rule.stages)) != len(rule.stages):
                raise ValueError("GUARDRAIL_DUPLICATE_RULE_STAGE")
            if isinstance(rule, ToolAllowlistRule) and len(set(rule.names)) != len(rule.names):
                raise ValueError("GUARDRAIL_DUPLICATE_ALLOWED_TOOL")
        content_bound(self.model_dump(mode="json"), 65536)
        return self

    @property
    def policy_sha256(self):
        return hashlib.sha256(canonical(self.model_dump(mode="json"))).hexdigest()

    def public_metadata(self):
        return {
            "version": self.version,
            "policy_id": str(self.policy_id),
            "project_id": str(self.project_id),
            "principal_id": str(self.principal_id),
            "environment": self.environment,
            "workflows": list(self.workflows),
            "policy_sha256": self.policy_sha256,
            "starts_at": self.starts_at.astimezone(UTC).isoformat(),
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
            "protected_stages": list(self.protected_stages),
            "rule_count": len(self.rules),
            "stage_rule_counts": {
                stage: sum(stage in rule.stages for rule in self.rules)
                for stage in self.protected_stages
            },
            "on_unavailable": self.on_unavailable,
            "max_content_bytes": self.max_content_bytes,
            "max_evaluation_ms": self.max_evaluation_ms,
        }


class GuardrailDecision(StrictModel):
    version: Literal["cutback.guardrail-decision.v1"] = "cutback.guardrail-decision.v1"
    operation_id: UUID
    policy_id: UUID
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stage: GuardrailStage
    status: Literal["allowed", "blocked", "unavailable"]
    action: Literal["allow", "deny"]
    code: Literal[
        "GUARDRAIL_ALLOWED",
        "GUARDRAIL_LITERAL_DENIED",
        "GUARDRAIL_SHAPE_DENIED",
        "GUARDRAIL_TOOL_DENIED",
        "GUARDRAIL_SCOPE_DENIED",
        "GUARDRAIL_EXPIRED",
        "GUARDRAIL_CHECK_UNAVAILABLE",
        "GUARDRAIL_CONTENT_BOUND",
        "GUARDRAIL_OPERATION_CONFLICT",
    ]
    rule_refs: tuple[Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")], ...] = Field(
        default=(), max_length=64
    )
    checks_completed: int = Field(strict=True, ge=0, le=64)
    checks_expected: int = Field(strict=True, ge=0, le=64)
    coverage_complete: bool = Field(strict=True)
    duration_ms: int | None = Field(default=None, strict=True, ge=0)

    @model_validator(mode="after")
    def preserve_uncertainty(self):
        if self.operation_id.int == 0 or self.policy_id.int == 0:
            raise ValueError("GUARDRAIL_DECISION_NONZERO_IDENTITY_REQUIRED")
        if self.checks_completed > self.checks_expected:
            raise ValueError("GUARDRAIL_DECISION_COVERAGE_INVALID")
        if self.status == "allowed" and (
            self.action != "allow"
            or not self.coverage_complete
            or self.code != "GUARDRAIL_ALLOWED"
            or self.rule_refs
            or self.checks_completed != self.checks_expected
            or self.checks_expected == 0
        ):
            raise ValueError("GUARDRAIL_ALLOWED_REQUIRES_COMPLETE_KNOWN_CHECKS")
        if self.status == "blocked" and self.action != "deny":
            raise ValueError("GUARDRAIL_ACTUAL_DENIAL_CANNOT_FAIL_OPEN")
        if self.status == "blocked" and self.code in {
            "GUARDRAIL_ALLOWED",
            "GUARDRAIL_CHECK_UNAVAILABLE",
        }:
            raise ValueError("GUARDRAIL_BLOCK_REQUIRES_KNOWN_DENIAL_REASON")
        if self.status == "unavailable" and self.coverage_complete:
            raise ValueError("GUARDRAIL_UNAVAILABLE_COVERAGE_IS_UNKNOWN")
        if self.status == "unavailable" and (
            self.code != "GUARDRAIL_CHECK_UNAVAILABLE" or self.rule_refs
        ):
            raise ValueError("GUARDRAIL_UNAVAILABLE_CANNOT_ASSERT_A_KNOWN_RULE_RESULT")
        if self.code in {
            "GUARDRAIL_LITERAL_DENIED",
            "GUARDRAIL_SHAPE_DENIED",
            "GUARDRAIL_TOOL_DENIED",
        } and (not self.rule_refs or self.checks_completed == 0):
            raise ValueError("GUARDRAIL_RULE_DENIAL_REQUIRES_OBSERVED_CHECK")
        return self


class GuardrailBinding(StrictModel):
    """Public, approved execution scope; contains no rule literals or content nonce."""

    binding_version: Literal["cutback.guardrail-binding.v1"] = "cutback.guardrail-binding.v1"
    version: Literal["cutback.guardrail-policy.v1"] = "cutback.guardrail-policy.v1"
    service_id: UUID
    policy_id: UUID
    project_id: UUID
    principal_id: UUID
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment: Identifier
    workflows: tuple[Identifier, ...] = Field(min_length=1, max_length=100)
    starts_at: datetime
    expires_at: datetime
    protected_stages: tuple[GuardrailStage, ...] = Field(min_length=1, max_length=4)
    rule_count: int = Field(strict=True, ge=1, le=64)
    stage_rule_counts: dict[GuardrailStage, Annotated[int, Field(strict=True, ge=1, le=64)]]
    on_unavailable: Literal["fail_closed", "fail_open"] = "fail_closed"
    max_content_bytes: int = Field(strict=True, ge=256, le=1048576)
    max_evaluation_ms: int = Field(strict=True, ge=1, le=1000)

    @model_validator(mode="after")
    def precise_scope_and_coverage(self):
        if any(
            not value.int
            for value in (self.service_id, self.policy_id, self.project_id, self.principal_id)
        ):
            raise ValueError("GUARDRAIL_BINDING_NONZERO_SCOPE_REQUIRED")
        if (
            self.starts_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or not timedelta(0) < self.expires_at - self.starts_at <= timedelta(days=31)
        ):
            raise ValueError("GUARDRAIL_BINDING_FINITE_AWARE_WINDOW_REQUIRED")
        if len(set(self.workflows)) != len(self.workflows) or len(
            set(self.protected_stages)
        ) != len(self.protected_stages):
            raise ValueError("GUARDRAIL_BINDING_UNIQUE_SCOPE_REQUIRED")
        if set(self.stage_rule_counts) != set(self.protected_stages) or any(
            count > self.rule_count for count in self.stage_rule_counts.values()
        ):
            raise ValueError("GUARDRAIL_BINDING_EXACT_STAGE_COVERAGE_REQUIRED")
        return self


class GuardrailCheckReceipt(StrictModel):
    version: Literal["cutback.guardrail-receipt.v1"] = "cutback.guardrail-receipt.v1"
    service_id: UUID
    project_id: UUID
    principal_id: UUID
    environment: Identifier
    workflow: Identifier
    decision: GuardrailDecision
