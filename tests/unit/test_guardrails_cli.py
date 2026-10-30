import json

from typer.testing import CliRunner
from unit.test_guardrails import policy

from cutback.cli import app


def test_policy_validation_prints_only_frozen_public_metadata_and_no_approval(tmp_path):
    definition = policy()
    path = tmp_path / "owned-private-policy.json"
    path.write_text(definition.model_dump_json(), encoding="utf-8")
    result = CliRunner().invoke(app, ["guardrails", "validate", str(path)])
    assert result.exit_code == 0
    receipt = json.loads(result.stdout)
    assert receipt["policy"]["policy_sha256"] == definition.policy_sha256
    assert receipt["execution"] == "not_executed" and receipt["approval"] == "not_granted"
    for hidden in (
        "SYNTHETIC_DENIED_LITERAL_OWNED",
        "private-owner-rule",
        "content_nonce",
        str(definition.content_nonce),
    ):
        assert hidden not in result.stdout


def test_missing_nonce_or_bad_scope_cannot_silently_change_the_reviewed_hash(tmp_path):
    value = policy().model_dump(mode="json")
    path = tmp_path / "owned-invalid-policy.json"
    for change in ("nonce", "scope"):
        bad = value.copy()
        if change == "nonce":
            del bad["content_nonce"]
        else:
            bad["project_id"] = "PRIVATE_SCOPE_DETAIL"
        path.write_text(json.dumps(bad), encoding="utf-8")
        result = CliRunner().invoke(app, ["guardrails", "validate", str(path)])
        assert result.exit_code == 2
        assert json.loads(result.stdout)["code"] == "GUARDRAIL_POLICY_INVALID"
        assert (
            "PRIVATE_SCOPE_DETAIL" not in result.stdout
            and "SYNTHETIC_DENIED_LITERAL_OWNED" not in result.stdout
        )
