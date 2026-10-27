"""Explicit, bounded JSON/JSONL/CSV record preparation. No implicit upload or gold inference."""

import csv
import io
import json
from pathlib import Path
from typing import Any

from cutback.evals.datasets import Case


def read_records(path: Path, *, mapping: dict | None = None) -> list[Case]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576:
        raise ValueError("EXAMPLE_FILE_UNSAFE_OR_EXCEEDS_ONE_MIB")
    text = path.read_text(encoding="utf-8-sig")
    if len(text.encode()) > 1048576:
        raise ValueError("EXAMPLE_FILE_EXCEEDS_ONE_MIB")
    rows: Any
    if mapping is not None and (
        not isinstance(mapping, dict)
        or "input" not in mapping
        or any(not isinstance(value, str) or len(value) > 250 for value in mapping.values())
    ):
        raise ValueError("INVALID_RECORD_MAPPING")

    def unique(pairs):
        row = {}
        for key, value in pairs:
            if key in row:
                raise ValueError("DUPLICATE_RECORD_FIELD")
            row[key] = value
        return row

    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            headers = next(reader)
            if len(headers) != len(set(headers)) or any(
                h in {"__proto__", "prototype", "constructor"} for h in headers
            ):
                raise ValueError("DUPLICATE_OR_UNSAFE_RECORD_HEADERS")
            rows = []
            for fields in reader:
                if not fields:
                    continue
                if len(fields) != len(headers):
                    raise ValueError("RECORD_COLUMN_COUNT_MISMATCH")
                rows.append(dict(zip(headers, fields, strict=True)))
        elif suffix in {".jsonl", ".ndjson"}:
            rows = [
                json.loads(line, object_pairs_hook=unique)
                for line in text.splitlines()
                if line.strip()
            ]
        elif suffix == ".json":
            rows = json.loads(text, object_pairs_hook=unique)
            if isinstance(rows, dict):
                rows = rows.get("cases")
        else:
            raise ValueError("SUPPORTED_RECORD_FORMATS_JSON_JSONL_CSV")
    except (json.JSONDecodeError, csv.Error, StopIteration, RecursionError):
        raise ValueError("MALFORMED_EXAMPLE_FILE") from None
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValueError("ONE_TO_TEN_THOUSAND_EXAMPLES_REQUIRED")
    cases = []

    def selected(row, field):
        parts = field.split(".")
        if len(parts) > 8 or any(not part or part.startswith("__") for part in parts):
            raise ValueError("UNSAFE_RECORD_MAPPING")
        for part in parts:
            if not isinstance(row, dict) or part not in row:
                raise ValueError("RECORD_MAPPING_MISSING")
            row = row[part]
        return row

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("RECORD_OBJECT_REQUIRED")
        if mapping:
            if set(mapping) - {"id", "input", "gold", "reference", "group", "stratum"}:
                raise ValueError("UNKNOWN_RECORD_MAPPING")
            row = {key: selected(row, value) for key, value in mapping.items()}
        case = {
            "id": str(row.get("id") or f"case-{i + 1:05}"),
            "input": row.get("input", row.get("prompt", row.get("text"))),
        }
        if isinstance(case["input"], (dict, list)):
            case["input"] = json.dumps(case["input"], sort_keys=True, ensure_ascii=False)
        gold = row.get("gold", row.get("label", row.get("expected")))
        if gold is not None and gold != "":
            if isinstance(gold, str) and gold.strip().startswith(("{", "[")):
                try:
                    gold = json.loads(gold)
                except (ValueError, RecursionError):
                    raise ValueError("STRUCTURED_LABEL_PARSE_FAILED") from None
            case["gold"] = gold
        reference = row.get("reference", row.get("output", row.get("response")))
        if reference is not None:
            case["reference"] = reference
        for name in ("group", "stratum"):
            if row.get(name):
                case[name] = row[name]
        cases.append(Case.model_validate(case))
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("DUPLICATE_EXAMPLE_IDS")
    return cases
