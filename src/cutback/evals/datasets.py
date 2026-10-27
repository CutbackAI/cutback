"""Rights-provenanced, immutable datasets with duplicate-group holdout separation."""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from cutback.core.models import Identifier, StrictModel


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class CapturePolicy(StrictModel):
    enabled: bool = False
    approval_ref: Identifier
    rights_basis: Literal["synthetic", "owner_authorized", "licensed"]
    environments: Annotated[list[Identifier], Field(min_length=1, max_length=20)]
    workflows: Annotated[list[Identifier], Field(min_length=1, max_length=100)]
    allowed_providers: Annotated[
        list[Literal["openai", "anthropic", "local"]], Field(min_length=1, max_length=3)
    ]
    expires_at: datetime
    retention_days: int = Field(default=7, ge=1, le=30)
    sample_rate: float = Field(default=0.01, ge=0, le=1)
    max_samples: int = Field(default=100, ge=1, le=10000)
    max_payload_bytes: int = Field(default=64000, ge=1, le=512000)
    never_capture_fields: list[Identifier] = Field(default_factory=list, max_length=100)
    redaction_version: Literal["email-secret-v1"] = "email-secret-v1"

    @model_validator(mode="after")
    def aware(self):
        if not self.expires_at.tzinfo:
            raise ValueError("capture expiry must include UTC offset")
        return self

    def permits(self, environment, workflow):
        return (
            self.enabled
            and datetime.now(UTC) < self.expires_at
            and environment in self.environments
            and workflow in self.workflows
        )


class Sample(StrictModel):
    sample_id: UUID
    event_id: UUID
    policy_id: UUID
    environment: Identifier
    workflow: Identifier
    provider: Literal["openai", "anthropic"]
    input: str = Field(max_length=256000)
    reference: str | None = Field(default=None, max_length=256000)


def redact(value, denied: list[str]):
    """Defense in depth, not a guarantee of deidentification; policy approval is still required."""
    if isinstance(value, dict):
        return {k: redact(v, denied) for k, v in value.items() if k not in denied}
    if isinstance(value, list):
        return [redact(v, denied) for v in value]
    if isinstance(value, str):
        if denied and value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
                clean = redact(parsed, denied)
                if clean != parsed:
                    return json.dumps(clean, ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
        value = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[EMAIL]", value)
        return re.sub(r"(?i)\b(?:sk|cb)[_-][A-Za-z0-9_-]{16,}\b", "[SECRET]", value)
    return value


class Case(StrictModel):
    id: Identifier
    input: Annotated[str, Field(min_length=1, max_length=64000)]
    gold: str | list[str] | dict | None = None
    reference: str | None = Field(default=None, max_length=64000)
    group: Identifier | None = None
    stratum: Identifier = "default"


class DatasetImport(StrictModel):
    workflow: Identifier
    environment: Identifier = "staging"
    policy_id: str
    source_type: Literal["synthetic_fixture", "human_labeled", "application_records", "sdk_capture"]
    independent_labels: bool = False
    representative: bool = False
    provenance_ref: Identifier
    cases: Annotated[list[Case], Field(min_length=1, max_length=10000)]
    split_seed: int = Field(default=17, ge=0, le=2**31 - 1)


def infer_task(body: DatasetImport) -> dict:
    """Conservative local shape inference; no labels or customer text in metadata."""
    labels = [case.gold for case in body.cases]
    independent = body.independent_labels and all(label is not None for label in labels)
    if independent and all(isinstance(label, (dict, list)) for label in labels):
        kind, metric, basis = "structured_extraction", "field_f1", "independent_structured_gold"
    elif independent and all(isinstance(label, str) for label in labels):
        # Short repeated labels are evidence for classification; arbitrary prose is not.
        text_labels = [label for label in labels if isinstance(label, str)]
        classes = set(text_labels)
        if all(len(label) <= 128 for label in text_labels) and len(classes) <= min(
            100, len(labels) / 2
        ):
            kind, metric, basis = "classification", "exact_label", "repeated_short_gold_labels"
        else:
            kind, metric, basis = "unknown", "nonempty", "text_gold_needs_explicit_rubric"
    elif all(case.reference is not None for case in body.cases):
        kind, metric, basis = "reference_comparison", "reference_match", "references_are_not_gold"
    else:
        kind, metric, basis = "unknown", "nonempty", "insufficient_independent_task_evidence"
    return {
        "kind": kind,
        "suggested_metric": metric,
        "basis": basis,
        "status": "heuristic_requires_review",
        "semantic_quality": "NOT VERIFIED",
    }


def prepare_dataset(body: DatasetImport) -> dict:
    ids = [case.id for case in body.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    normalized = []
    for case in body.cases:
        # Numbers, whitespace and case variations share a group. Supplied customer/user groups
        # are unioned with duplicate groups, preventing the caller from splitting duplicates.
        text = unicodedata.normalize("NFKC", case.input).casefold()
        text = re.sub(r"\d+", "#", re.sub(r"\s+", " ", text)).strip()
        normalized.append(digest(text))
    parents = list(range(len(body.cases)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    seen: dict[str, int] = {}
    for i, case in enumerate(body.cases):
        keys = ["duplicate:" + normalized[i]]
        if case.group:
            keys.append("group:" + case.group)
        for key in keys:
            if key in seen:
                parents[find(i)] = find(seen[key])
            seen[key] = i
    components: dict[int, list[int]] = {}
    for i in range(len(parents)):
        components.setdefault(find(i), []).append(i)
    assignments = {}
    strata: dict[str, list] = {}
    for members in components.values():
        group_hash = digest(sorted(normalized[i] for i in members))
        stratum = digest(
            sorted(
                {
                    (
                        body.cases[i].stratum,
                        str(body.cases[i].gold)
                        if body.independent_labels and isinstance(body.cases[i].gold, str)
                        else "",
                    )
                    for i in members
                }
            )
        )
        strata.setdefault(stratum, []).append((group_hash, members))
    rare = False
    for groups in strata.values():
        groups.sort(key=lambda pair: digest([body.split_seed, pair[0]]))
        n = len(groups)
        test_count = max(1, n // 5)
        validation_count = max(1, n // 5) if n >= 3 else 0
        rare |= n < 3
        for index, (group_hash, members) in enumerate(groups):
            split = (
                "test"
                if index < test_count
                else "validation"
                if index < test_count + validation_count
                else "train"
            )
            for i in members:
                assignments[i] = (group_hash, split)
    items = [
        {"case": case.model_dump(), "group_hash": assignments[i][0], "split": assignments[i][1]}
        for i, case in enumerate(body.cases)
    ]
    counts = Counter(item["split"] for item in items)
    return {
        "schema_version": "1.0",
        "task_inference": infer_task(body),
        "immutable_hash": digest(body.model_dump(mode="json")),
        "items": items,
        "split_counts": dict(counts),
        "group_count": len(components),
        "strata": dict(Counter(case.stratum for case in body.cases)),
        "split_strategy": "seeded_stratified_duplicate_and_customer_group_union_v1",
        "warnings": (["INSUFFICIENT_SPLIT_GROUPS"] if len(counts) < 3 else [])
        + (["RARE_STRATA_HAVE_INSUFFICIENT_SPLIT_GROUPS"] if rare else []),
    }


def discover_fixtures(root, *, max_files=1000, max_entries=20000, max_seconds=5):
    """Bounded metadata-only discovery; no fixture payload, .env or application code read."""
    import os
    import time
    from pathlib import Path

    from pathspec import GitIgnoreSpec

    from cutback.scanner.python import SKIP

    root = Path(root).resolve(strict=True)
    found: list[dict] = []
    visited, entries = set(), 0
    deadline = time.monotonic() + max_seconds
    specs = {}

    def load_ignore(directory):
        path = directory / ".gitignore"
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 65536:
            try:
                specs[directory] = GitIgnoreSpec.from_lines(
                    path.read_text(encoding="utf-8").splitlines()
                )
            except (OSError, UnicodeError):
                pass

    def ignored(path, directory=False):
        result = False
        for parent in reversed(path.parents):
            if parent in specs:
                match = specs[parent].check_file(
                    path.relative_to(parent).as_posix() + ("/" if directory else "")
                )
                if match.include is not None:
                    result = match.include
        return result

    load_ignore(root)
    for base in (root,):
        for directory, dirs, files in os.walk(base, followlinks=False):
            folder = Path(directory)
            entries += len(dirs) + len(files)
            if entries > max_entries or time.monotonic() > deadline:
                return found
            load_ignore(folder)
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in SKIP
                and not d.startswith(".")
                and d.lower() not in {"secrets", "credentials"}
                and not (folder / d).is_symlink()
                and not ignored(folder / d, True)
            )
            for filename in sorted(files):
                path = folder / filename
                hints = {
                    "test",
                    "tests",
                    "fixtures",
                    "examples",
                    "data",
                    "datasets",
                    "eval",
                    "evals",
                    "benchmarks",
                    "samples",
                }
                suggestive = (
                    path.suffix.lower() in {".jsonl", ".ndjson"}
                    or any(part.lower() in hints for part in path.relative_to(root).parts[:-1])
                    or any(
                        word in path.stem.lower()
                        for word in ("case", "fixture", "dataset", "sample", "gold", "eval")
                    )
                )
                if (
                    path.suffix.lower() not in {".json", ".jsonl", ".ndjson", ".csv"}
                    or not suggestive
                    or filename.startswith(".")
                    or path.is_symlink()
                    or ignored(path)
                    or not path.resolve().is_relative_to(root)
                    or path in visited
                ):
                    continue
                visited.add(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                found.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "format": path.suffix.lstrip("."),
                        "size": size,
                        "within_import_bound": size <= 1048576,
                        "status": "unreviewed_no_content_read_or_uploaded",
                        "mapping": "prepare_locally_after_content_permission",
                    }
                )
                if len(found) >= max_files:
                    return found
    return found
