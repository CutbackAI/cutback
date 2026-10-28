from cutback.server.quality_metrics import judge_verdict, output_contract


def test_contract_missing_invalid_and_processing_limits_are_distinct():
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
        "additionalProperties": False,
    }
    assert output_contract(None, schema)[0] == "unknown"
    assert output_contract('{"label":"A"}', schema)[0] == "passed"
    assert output_contract('{"label":2}', schema)[0] == "failed"
    assert output_contract('{"label":"A","label":"B"}', schema)[0] == "failed"
    assert output_contract('{"label":NaN}', schema)[0] == "failed"
    assert output_contract("[" * 20 + "0" + "]" * 20, schema)[0] == "error"
    assert output_contract('{"label":1e9999}', schema)[0] == "error"
    assert output_contract(
        '{"label":"A"}', {"$ref": "https://unqualified.example.test/schema"}
    ) == ("error", "OUTPUT_SCHEMA_PROFILE_INVALID")


def test_judge_errors_never_become_application_failures_or_independent_truth():
    assert judge_verdict(
        '{"outcome":"passed","evidence_quote":"urgent"}', "urgent input", "reply"
    ) == ("passed", "MODEL_JUDGE_NOT_INDEPENDENT_GOLD")
    assert (
        judge_verdict('{"outcome":"failed","evidence_quote":"invented"}', "urgent input", "reply")[
            0
        ]
        == "error"
    )
    assert (
        judge_verdict('{"outcome":[],"evidence_quote":"urgent"}', "urgent input", "reply")[0]
        == "error"
    )
    assert (
        judge_verdict('{"outcome":"unknown","evidence_quote":""}', "urgent input", "reply")[0]
        == "unknown"
    )
    assert (
        judge_verdict(
            '{"outcome":"passed","evidence_quote":"urgent","rationale":"not allowed"}',
            "urgent input",
            "reply",
        )[0]
        == "error"
    )
