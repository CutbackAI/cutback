from pathlib import Path

import yaml


def test_publish_waits_for_exact_revision_security_and_ci():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/publish.yml").read_text())
    assert set(workflow["jobs"]["build"]["needs"]) == {"validate", "security"}
    for job, file in (("validate", "ci.yml"), ("security", "security.yml")):
        assert workflow["jobs"][job]["uses"] == "./.github/workflows/" + file
    assert workflow["jobs"]["publish"]["environment"]["name"] == "pypi"
    security = yaml.safe_load((root / ".github/workflows/security.yml").read_text())
    steps = security["jobs"]["codeql"]["steps"]
    analyze = next(
        index
        for index, step in enumerate(steps)
        if "codeql-action/analyze@" in step.get("uses", "")
    )
    gate = next(
        index
        for index, step in enumerate(steps)
        if "check_codeql_results.py" in step.get("run", "")
    )
    assert analyze < gate
    assert steps[analyze]["with"]["output"] == ".cutback/codeql-results"
    assert not steps[gate].get("continue-on-error")
    for file in (root / ".github/workflows").glob("*.yml"):
        assert "0.12.13" not in file.read_text()
    docker = (root / "infra/compose/Dockerfile").read_text()
    assert "0.13.6@sha256:" in docker and "UV_PYTHON=3.11.18" in docker
