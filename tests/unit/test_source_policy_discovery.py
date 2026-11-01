"""An application agent discovers the qualified consumer without importing the app."""

from support_policy_application import application

from cutback.agent import analyze
from cutback.application_checks import git
from cutback.core.source_guidance import explain_source


def test_policy_consumer_discovery_is_parse_only_and_gives_a_concrete_whole_budget_action(tmp_path):
    root = application(tmp_path)
    (root / "never_import.py").write_text(
        "raise RuntimeError('DO_NOT_EXECUTE_SOURCE')\n", encoding="utf-8"
    )
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent parse-only guard",
    )
    result = analyze(root, workflow="routing-task")
    assert len(result["policy_producers"]) == 1
    assert result["policy_producers"][0]["source"]["workflow"] == "routing-task"
    assert result["next_action"]["tool"] == "cutback source-policy prepare"
    assert result["provider_calls"] == 0 and result["historical_spend_usd"] is None
    assert "request_contract_qualification" not in result["decisions_required"]
    assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in str(result)
    explained = explain_source(result)
    assert explained["qualified_source_policy_producers"] == 1
    assert explained["next_action"]["tool"] == "cutback source-policy prepare"
    assert analyze(root, workflow="routing-task")["id"] == result["id"]
