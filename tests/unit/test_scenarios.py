import json
import runpy
from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

from cutback.cli import app
from cutback.evals.datasets import DatasetImport, prepare_dataset
from cutback.evals.scenarios import generate_scenarios


def owned_source():
    sample = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "examples/agent-evaluation/sample.py")
    )
    return DatasetImport(
        policy_id=str(uuid4()),
        workflow="synthetic.agent",
        source_type="synthetic_fixture",
        provenance_ref="owned",
        cases=[sample["support_case"](), sample["rag_case"]()],
    )


def test_scenario_generator_preserves_source_anchors_lineage_and_unverified_labels():
    source = owned_source()
    draft = generate_scenarios(source)
    assert draft["paid_calls_made"] == 0 and draft["review_required"]
    body = DatasetImport.model_validate(draft["dataset"])
    assert not body.independent_labels and not body.representative
    prepared = prepare_dataset(body)
    assert len(prepared["items"]) == 7 and prepared["group_count"] == 2
    assert {row["variant"] for row in draft["mapping"]} == {
        "source_anchor",
        "repeat_after_followup",
        "unauthorized_tool_injection",
        "retrieved_instruction_injection",
    }
    for source_case in source.cases:
        keys = {
            row["case_id"] for row in draft["mapping"] if row["source_case_id"] == source_case.id
        }
        family = [item for item in prepared["items"] if item["case"]["id"] in keys]
        assert len({item["group_hash"] for item in family}) == 1
        assert len({item["split"] for item in family}) == 1
        assert any(item["case"]["input"] == source_case.input for item in family)


def test_offline_episode_cli_generates_explicit_draft_and_sanitizes_bad_inputs(tmp_path):
    source = tmp_path / "owned.json"
    destination = tmp_path / "draft.json"
    source.write_text(owned_source().model_dump_json(), encoding="utf-8")
    result = CliRunner().invoke(
        app, ["episodes", "generate", str(source), "--output", str(destination)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(destination.read_text())["review_required"]
    source.write_text('{"PRIVATE_BAD_CASE":"secret"}', encoding="utf-8")
    result = CliRunner().invoke(
        app, ["episodes", "generate", str(source), "--output", str(destination)]
    )
    assert result.exit_code == 2 and "EPISODE_GENERATION_INPUT_INVALID" in result.output
    assert "PRIVATE_BAD_CASE" not in result.output and "secret" not in result.output


def test_independent_agent_and_rag_example_detects_failures_without_vendor_network():
    sample = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "examples/agent-evaluation/sample.py")
    )
    report = sample["demonstrate"]()
    assert report["status"] == "COMPLETED" and report["requests"] == 8
    assert report["models"][0]["quality"]["value"] == 1
    assert report["models"][1]["quality"]["value"] == 0
    assert report["models"][1]["failed_checks"]["literal_citation_support"] == 1
    assert report["transport_evidence"] == "scripted_test_only" and not report["recommendations"]
