"""Discovery qualifies source structure without running an app or parsing its corpus."""

import socket
from pathlib import Path

import pytest
from support_retrieval_application import application, git

from cutback.core.retrieval_producer import qualify
from cutback.retrieval_source import inspect_producer


def source():
    return (
        Path(__file__).resolve().parents[2] / "examples/retrieval-optimization/app.py"
    ).read_bytes()


def test_qualified_program_extracts_only_native_model_and_output_literals():
    values, nodes = qualify(source())
    assert values == ("gpt-4.1-mini", 128)
    assert set(nodes) == {"model", "max_output_tokens"}
    assert qualify(source().replace(b"gpt-4.1-mini", b"gpt-4.1"))[0] == ("gpt-4.1", 128)


@pytest.mark.parametrize(
    "before,after",
    [
        (b"input=build_input(question)", b"input=question"),
        (b"corpus.json", b"../corpus.json"),
        (b"retrieval.json", b"other.json"),
        (b"store=False", b"store=True"),
        (b"max_output_tokens=128", b"max_output_tokens=8"),
        (b"max_output_tokens=128", b"max_output_tokens=True"),
        (b'model="gpt-4.1-mini"', b"model=chosen_model"),
        (b".output_text", b".id"),
    ],
)
def test_changed_runtime_contract_and_dynamic_literals_are_not_qualified(before, after):
    with pytest.raises(ValueError, match="RETRIEVAL_PRODUCER_PROGRAM_NOT_QUALIFIED"):
        qualify(source().replace(before, after))


def test_local_inspection_never_imports_the_app_or_parses_corpus_json(tmp_path, monkeypatch):
    root = application(tmp_path)
    (root / "corpus.json").write_text(
        "Invalid opaque corpus; do not parse for discovery", encoding="utf-8"
    )
    git(root, "add", "corpus.json")
    git(root, "commit", "-m", "Opaque discovery fixture")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("OFFLINE_ONLY"))
    settings, binding, producer = inspect_producer(root, "app.py")
    assert settings == binding.settings
    assert producer.corpus_file == "corpus.json" and producer.model == "gpt-4.1-mini"
    assert producer.source_tree_hash == binding.source_tree_hash
    assert producer.commit == binding.commit
    assert "Invalid opaque corpus" not in producer.model_dump_json()


def test_dirty_source_does_not_receive_a_producer_binding(tmp_path):
    root = application(tmp_path)
    (root / "app.py").write_bytes(source() + b"\n# changed\n")
    with pytest.raises(ValueError, match="CLEAN_TRACKED_WORKSPACE"):
        inspect_producer(root, "app.py")
