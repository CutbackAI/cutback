"""Versioned scanner contract. Static evidence can never contain measured usage."""

from typing import Any, Literal

from cutback.core.models import StrictModel


class PromptReference(StrictModel):
    argument: str
    file: str
    line: int
    kind: str
    symbol: str | None
    content: None = None
    template_path: str | None = None
    static_characters: int | None = None


class CallSite(StrictModel):
    call_site_id: str
    semantic_fingerprint: str
    provider: Literal["openai", "anthropic"]
    sdk_surface: str
    requested_model: str | None
    model_reference: dict[str, Any]
    model_expression: str | None
    prompt_template_refs: list[PromptReference]
    source_file: str
    source_symbol: str
    line: int
    workflow_hint: str
    workflow_attribution: Literal["heuristic", "unknown", "exact"]
    confidence: Literal["high", "medium", "low"]
    unresolved: list[str]
    evidence: list[str]
    price: dict[str, Any]
    upstream_callers: list[str]
    entry_points: list[dict[str, Any]]
    configured_max_output_tokens: int | None = None
    evidence_status: Literal["discovered"] = "discovered"
    actual_cost: None = None
    actual_tokens: None = None
    actual_latency: None = None


class Inventory(StrictModel):
    version: Literal["1.0"]
    scanner_version: str
    project: dict[str, str | None]
    coverage: dict[str, int]
    summary: dict[str, Any]
    call_sites: list[CallSite]
    warnings: list[dict[str, Any]]
    unknowns: list[dict[str, Any]]
    limitations: list[str]
