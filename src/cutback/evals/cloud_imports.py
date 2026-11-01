"""Offline projections of declared cloud text-evaluation exports.

No cloud authentication, fetch, evaluator execution or score normalization occurs.
Metric names remain authorized encrypted content; metadata gets scoped hashes.
"""

import json
from decimal import Decimal

from cutback.evals.datasets import Case, digest
from cutback.evals.importers import SourceImportError

CLOUD_PROFILES = (
    "bedrock-automated-model-jsonl.v1",
    "azure-ai-evaluation-1.18.10-results.v1",
    "vertexai-1.153.1-metrics-jsonl.v1",
)


def _text(value, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or (not value and not optional) or len(value.encode()) > 64000:
        raise SourceImportError("CLOUD_EXPLICIT_BOUNDED_TEXT_REQUIRED")
    return value


def _case(profile, ordinal, text, reference, expectation):
    text = _text(text)
    return Case(
        id="cloud:" + digest([profile, ordinal, text, reference, expectation])[:48],
        input=text,
        reference=_text(reference, optional=True),
        gold=_text(expectation, optional=True),
        group="cloud-input:" + digest(text),
    )


def _metric(case, profile, source_sha256, name, value):
    if not isinstance(name, str) or not 1 <= len(name.encode()) <= 256:
        raise SourceImportError("CLOUD_METRIC_NAME_BOUND_EXCEEDED")
    kind, score, boolean = "missing", None, None
    if type(value) is bool:
        kind, boolean = "boolean", value
    elif value is not None:
        if (
            not isinstance(value, (int, Decimal))
            or not Decimal(value).is_finite()
            or Decimal(value).copy_abs() > Decimal("1e20")
            or len(str(value)) > 128
        ):
            raise SourceImportError("CLOUD_FINITE_SCORE_BOOLEAN_OR_NULL_REQUIRED")
        kind, score = "numeric", str(value)
    return {
        "case_id": case.id,
        "outcome": "missing" if kind == "missing" else "reported",
        "score": score,
        "boolean_value": boolean,
        "value_kind": kind,
        "metric_id": "metric:" + digest([profile, source_sha256, name])[:48],
        "metric_name": name,
        "scale": None,
        "normalization": "none",
        "evidence": "externally_asserted",
        "evaluator": "amazon-bedrock"
        if profile == CLOUD_PROFILES[0]
        else "azure-ai-evaluation"
        if profile == CLOUD_PROFILES[1]
        else "vertexai-evaluation",
        "profile": profile,
        **({"error_code": "SOURCE_METRIC_MISSING"} if kind == "missing" else {}),
    }


def _declared_state(assertion, row, prefix, separator):
    """An explicit source execution error overrides even a stale numeric result."""
    status = row.get(prefix + separator + "status")
    error = row.get(prefix + separator + "error")
    if status is not None and (
        not isinstance(status, str) or status not in {"completed", "error", "skipped"}
    ):
        raise SourceImportError("CLOUD_DECLARED_METRIC_STATUS_UNSUPPORTED")
    failed = status == "error" or isinstance(error, (str, dict)) and bool(error)
    if failed or status == "skipped":
        assertion = {
            **assertion,
            "discarded_reported_score": assertion["score"],
            "discarded_reported_boolean": assertion["boolean_value"],
            "score": None,
            "boolean_value": None,
            "outcome": "error" if failed else "skipped",
            "error_code": "SOURCE_EVALUATOR_ERROR" if failed else "SOURCE_EVALUATOR_SKIPPED",
        }
    return assertion


def project_cloud_row(row, profile, ordinal, source_sha256):
    if not isinstance(row, dict):
        raise SourceImportError("CLOUD_RESULT_ROW_REQUIRED")
    if profile == CLOUD_PROFILES[0]:
        record, responses, result = (
            row.get("inputRecord"),
            row.get("modelResponses"),
            row.get("automatedEvaluationResult"),
        )
        if not isinstance(record, dict) or not isinstance(result, dict):
            raise SourceImportError("BEDROCK_AUTOMATED_MODEL_ENVELOPE_REQUIRED")
        if (
            not isinstance(responses, list)
            or len(responses) != 1
            or not isinstance(responses[0], dict)
        ):
            raise SourceImportError("BEDROCK_SINGLE_MODEL_RESPONSE_REQUIRED")
        text = _text(record.get("prompt"))
        if not isinstance(responses[0].get("response"), str):
            raise SourceImportError("BEDROCK_EXPLICIT_TEXT_MODEL_RESPONSE_REQUIRED")
        response = _text(responses[0]["response"], optional=True)
        case = _case(profile, ordinal, text, response, record.get("referenceResponse"))
        values = result.get("scores")
        if not isinstance(values, list) or not 1 <= len(values) <= 64:
            raise SourceImportError("CLOUD_ONE_TO_SIXTY_FOUR_ROW_METRICS_REQUIRED")
        assertions = []
        for metric in values:
            if not isinstance(metric, dict) or "result" not in metric:
                raise SourceImportError("BEDROCK_EXPLICIT_METRIC_RESULT_REQUIRED")
            assertions.append(
                _metric(case, profile, source_sha256, metric.get("metricName"), metric["result"])
            )
        identifier = responses[0].get("modelIdentifier")
        if identifier is not None:
            if not isinstance(identifier, str) or not 1 <= len(identifier.encode()) <= 1024:
                raise SourceImportError("CLOUD_SOURCE_MODEL_IDENTIFIER_BOUND_EXCEEDED")
            for assertion in assertions:
                assertion["source_model_identifier"] = identifier
        ignored = len(row.keys() - {"inputRecord", "modelResponses", "automatedEvaluationResult"})
        ignored += len(record.keys() - {"prompt", "referenceResponse"}) + len(
            responses[0].keys() - {"response", "modelIdentifier"}
        )
    elif profile == CLOUD_PROFILES[1]:
        text = _text(row.get("inputs.query"))
        if row.get("inputs.context") is not None:
            context = row["inputs.context"]
            if not isinstance(context, str):
                raise SourceImportError("CLOUD_TEXT_CONTEXT_REQUIRED")
            text = json.dumps(
                {"query": text, "context": context}, ensure_ascii=False, separators=(",", ":")
            )
        case = _case(
            profile, ordinal, text, row.get("inputs.response"), row.get("inputs.ground_truth")
        )
        values = [
            (key, value)
            for key, value in row.items()
            if key.startswith("outputs.")
            and (
                value is None
                or isinstance(value, (int, Decimal, bool))
                or key.endswith((".score", ".value", ".passed"))
            )
        ]
        if not 1 <= len(values) <= 64:
            raise SourceImportError("CLOUD_ONE_TO_SIXTY_FOUR_ROW_METRICS_REQUIRED")
        assertions = [
            _declared_state(
                _metric(case, profile, source_sha256, key[8:], value),
                row,
                key.rsplit(".", 1)[0],
                ".",
            )
            for key, value in values
        ]
        ignored = (
            len(row)
            - len(values)
            - sum(
                key in row
                for key in (
                    "inputs.query",
                    "inputs.context",
                    "inputs.response",
                    "inputs.ground_truth",
                )
            )
        )
    elif profile == CLOUD_PROFILES[2]:
        text = _text(row.get("prompt"))
        if row.get("context") is not None:
            if not isinstance(row["context"], str):
                raise SourceImportError("CLOUD_TEXT_CONTEXT_REQUIRED")
            text = json.dumps(
                {"prompt": text, "context": row["context"]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        case = _case(profile, ordinal, text, row.get("response"), row.get("reference"))
        values = [(key, value) for key, value in row.items() if key.endswith("/score")]
        if not 1 <= len(values) <= 64:
            raise SourceImportError("CLOUD_ONE_TO_SIXTY_FOUR_ROW_METRICS_REQUIRED")
        assertions = [
            _declared_state(
                _metric(case, profile, source_sha256, key[:-6], value), row, key[:-6], "/"
            )
            for key, value in values
        ]
        ignored = len(row.keys() - {"prompt", "context", "response", "reference"}) - len(values)
    else:
        raise SourceImportError("IMPORT_PROFILE_UNSUPPORTED")
    if len({value["metric_id"] for value in assertions}) != len(assertions):
        raise SourceImportError("CLOUD_DUPLICATE_ROW_METRIC")
    return case, ignored, assertions
