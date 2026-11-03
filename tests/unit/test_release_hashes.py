import hashlib
import runpy
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
verify = runpy.run_path(str(ROOT / "scripts/check_release_hashes.py"))["verify"]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


@pytest.fixture
def artifacts(tmp_path):
    wheel = tmp_path / f"cutback-{VERSION}-py3-none-any.whl"
    sdist = tmp_path / f"cutback-{VERSION}.tar.gz"
    wheel.write_bytes(b"qualified complete wheel including metadata")
    sdist.write_bytes(b"qualified source archive")
    return (
        tmp_path,
        hashlib.sha256(wheel.read_bytes()).hexdigest(),
        hashlib.sha256(sdist.read_bytes()).hexdigest(),
    )


def test_qualified_complete_distributions_are_required(artifacts):
    directory, wheel_hash, sdist_hash = artifacts
    assert verify(directory, VERSION, wheel_hash, sdist_hash)["verified_files"] == 2


@pytest.mark.parametrize(
    "name", [f"cutback-{VERSION}-py3-none-any.whl", f"cutback-{VERSION}.tar.gz"]
)
def test_changed_distribution_cannot_publish(artifacts, name):
    directory, wheel_hash, sdist_hash = artifacts
    with (directory / name).open("ab") as handle:
        handle.write(b"changed metadata or content")
    with pytest.raises(ValueError, match="QUALIFIED_RELEASE_BYTES_MISMATCH"):
        verify(directory, VERSION, wheel_hash, sdist_hash)


@pytest.mark.parametrize("value", [None, "", "0" * 63, "g" * 64, "A" * 64])
def test_absent_or_invalid_qualification_hash_refuses_publication(artifacts, value):
    directory, _, sdist_hash = artifacts
    with pytest.raises(ValueError, match="QUALIFIED_RELEASE_HASH_REQUIRED"):
        verify(directory, VERSION, value, sdist_hash)


@pytest.mark.parametrize("change", ["missing", "extra", "wrong-version", "directory"])
def test_distribution_set_must_be_exact(artifacts, change):
    directory, wheel_hash, sdist_hash = artifacts
    wheel = directory / f"cutback-{VERSION}-py3-none-any.whl"
    if change == "missing":
        wheel.unlink()
    elif change == "extra":
        (directory / "unapproved.whl").write_bytes(b"unapproved")
    elif change == "wrong-version":
        wheel.rename(directory / "cutback-0.1.0.dev5-py3-none-any.whl")
    else:
        wheel.unlink()
        wheel.mkdir()
    with pytest.raises(ValueError, match="RELEASE_DISTRIBUTION_(SET_MISMATCH|INVALID)"):
        verify(directory, VERSION, wheel_hash, sdist_hash)


def test_workflow_binds_build_and_downloaded_bytes_to_the_approved_hashes():
    workflow = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())
    inputs = workflow.get("on", workflow.get(True))["workflow_dispatch"]["inputs"]
    assert inputs["wheel_sha256"]["required"] is True
    assert inputs["sdist_sha256"]["required"] is True
    assert workflow["env"]["SOURCE_DATE_EPOCH"] == "315532800"
    assert workflow["env"]["CUTBACK_RELEASE_VERSION"] == VERSION
    assert workflow["env"]["CUTBACK_EXPECTED_WHEEL_SHA256"] == "${{ inputs.wheel_sha256 }}"
    assert workflow["env"]["CUTBACK_EXPECTED_SDIST_SHA256"] == "${{ inputs.sdist_sha256 }}"
    for job, destination in (
        ("build", "actions/upload-artifact@"),
        ("publish", "pypa/gh-action-pypi-publish@"),
    ):
        steps = workflow["jobs"][job]["steps"]
        guard = next(
            index
            for index, step in enumerate(steps)
            if "scripts/check_release_hashes.py" in step.get("run", "")
        )
        mutation = next(
            index for index, step in enumerate(steps) if destination in step.get("uses", "")
        )
        assert guard < mutation and not steps[guard].get("continue-on-error")
    checkout = workflow["jobs"]["publish"]["steps"][0]
    assert checkout["with"]["persist-credentials"] is False
