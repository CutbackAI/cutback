"""The installed demo and independent example contain the same public synthetic fixture."""

from pathlib import Path

from cutback.core.routing_producer import qualify
from cutback.sample_app.routing_fixture import fixture


def test_installed_fixture_and_independent_source_example_are_identical():
    root = Path(__file__).resolve().parents[2] / "examples/source-routing"
    files = fixture()
    assert set(files) == {
        "app.py",
        "routing.json",
        "tickets.jsonl",
        "check_application.py",
        ".gitignore",
    }
    for name, content in files.items():
        assert (root / name).read_bytes() == content.encode("utf-8")
    qualify(files["app.py"].encode("utf-8"))
