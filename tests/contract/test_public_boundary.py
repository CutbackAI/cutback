import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def checker():
    spec = importlib.util.spec_from_file_location(
        "boundary", ROOT / "scripts/check_public_boundary.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_boundary_finds_history_only_disclosure_and_keeps_marker_out_of_logs(tmp_path):
    repo = tmp_path / "public"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    marker = "synthetic-private-business-name"
    policy = tmp_path / "outside-policy.json"
    policy.write_text(json.dumps([marker]), encoding="utf-8")
    file = repo / "README.md"
    for content in (marker, "Independent generic application"):
        file.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                "Synthetic fixture",
            ],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    module = checker()
    markers = module.markers_from(policy, repo)
    assert module.check_files(repo, markers) == (1, 0)
    assert module.check_history(repo, markers)[1] == 1
    result = subprocess.run(
        [
            __import__("sys").executable,
            str(ROOT / "scripts/check_public_boundary.py"),
            "--root",
            str(repo),
            "--markers-file",
            str(policy),
            "--history",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and marker not in result.stdout + result.stderr
    assert json.loads(result.stdout)["findings"] == 1


def test_public_boundary_checks_nested_archive_content_and_external_policy(tmp_path):
    module = checker()
    marker = b"synthetic-customer-marker"
    archive = tmp_path / "package.whl"
    with zipfile.ZipFile(archive, "w") as wheel:
        wheel.writestr("package/check.py", b"private = '" + marker + b"'")
    assert module.check_archive(archive, (marker,)) == (1, 1)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps([marker.decode()]), encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="outside"):
        module.markers_from(policy, tmp_path)
    assert module.private_content(b"/".join([b"C:", b"Users", b"Example", b"secret.txt"]), ())
