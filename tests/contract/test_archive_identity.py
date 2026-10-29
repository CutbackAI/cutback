"""Release acceptance must reject stale, missing, injected and ambiguous package bytes."""

import io
import runpy
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECK = runpy.run_path(str(ROOT / "scripts/check_release_artifacts.py"))[
    "check_application_payload"
]


def archives(tmp_path, *, wheel_change=None, source_change=None):
    content = {
        "cutback/__init__.py": b"# synthetic package\n",
        "cutback/core/catalog.json": b'{"unknown": null}\n',
        "cutback/server/migrations/versions/0001_fixture.py": b"revision = '0001'\n",
    }
    for name, value in content.items():
        path = tmp_path / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    # Interpreter caches are not application publication inputs.
    cache = tmp_path / "src/cutback/__pycache__/__init__.cpython-311.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"synthetic cache")
    wheel_entries = list(content.items())
    source_entries = [("cutback-fixture/src/" + k, v) for k, v in content.items()]
    if wheel_change:
        wheel_change(wheel_entries)
    if source_change:
        source_change(source_entries)
    wheel, source = tmp_path / "fixture.whl", tmp_path / "fixture.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, value in wheel_entries:
            archive.writestr(name, value)
    with tarfile.open(source, "w:gz") as archive:
        for name, value in source_entries:
            member = tarfile.TarInfo(name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    return wheel, source


def test_matching_core_data_and_immutable_migration_bytes_are_accepted(tmp_path):
    wheel, source = archives(tmp_path)
    assert CHECK(wheel, source, tmp_path) == 3


@pytest.mark.parametrize("target", ["wheel", "sdist"])
@pytest.mark.parametrize("mutation", ["stale_migration", "missing_data", "extra_module"])
def test_archive_drift_cannot_be_presented_as_tested_source(tmp_path, target, mutation):
    def change(entries):
        if mutation == "stale_migration":
            name, _ = entries[-1]
            entries[-1] = (name, b"revision = 'stale'\r\n")
        elif mutation == "missing_data":
            entries.pop(1)
        else:
            prefix = "" if target == "wheel" else "cutback-fixture/src/"
            entries.append((prefix + "cutback/unqualified.py", b"# not reviewed\n"))

    wheel, source = archives(
        tmp_path,
        wheel_change=change if target == "wheel" else None,
        source_change=change if target == "sdist" else None,
    )
    with pytest.raises(ValueError, match="application payload differs"):
        CHECK(wheel, source, tmp_path)


@pytest.mark.parametrize("target", ["wheel", "sdist", "ambiguous_root"])
def test_archive_member_shadowing_is_refused(tmp_path, target):
    def change(entries):
        name, value = entries[0]
        if target == "ambiguous_root":
            name = name.replace("cutback-fixture/", "second-root/", 1)
        entries.append((name, value))

    if target == "wheel":
        with pytest.warns(UserWarning, match="Duplicate name"):
            wheel, source = archives(tmp_path, wheel_change=change)
    else:
        wheel, source = archives(tmp_path, source_change=change)
    with pytest.raises(ValueError, match="duplicate members|ambiguous application roots"):
        CHECK(wheel, source, tmp_path)
