import json

import pytest

from cutback.evals.datasets import discover_fixtures
from cutback.evals.records import read_records


def test_csv_jsonl_records_preserve_references_without_inventing_gold(tmp_path):
    path = tmp_path / "cases.csv"
    path.write_text(
        'input,gold,output\r\n"example, one","{""a"":1}","line one\nline ""two"""\r\n',
        encoding="utf-8",
    )
    (case,) = read_records(path)
    assert case.input == "example, one" and case.gold == {"a": 1}
    assert case.reference == 'line one\nline "two"'
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({"prompt": "example", "response": "teacher"}), encoding="utf-8")
    (case,) = read_records(path)
    assert case.gold is None and case.reference == "teacher"
    path.write_text('{"input":"x","id":"same"}\n{"input":"y","id":"same"}', encoding="utf-8")
    with pytest.raises(ValueError, match="DUPLICATE_EXAMPLE"):
        read_records(path)


def test_discovery_is_bounded_metadata_only_and_respects_nested_ignores(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / ".gitignore").write_text("private.csv\n", encoding="utf-8")
    (examples / "private.csv").write_text("NEVER READ", encoding="utf-8")
    for name in ("one.json", "two.jsonl", "three.csv"):
        (examples / name).write_text("INTENTIONALLY NOT VALID DATA", encoding="utf-8")
    found = discover_fixtures(tmp_path)
    assert len(found) == 3 and all("private" not in row["path"] for row in found)
    assert len(discover_fixtures(tmp_path, max_files=1)) == 1
    assert not discover_fixtures(tmp_path, max_entries=1)


def test_malformed_records_and_unsafe_headers_do_not_silently_overwrite_labels(tmp_path):
    for text in ("input,input\na,b", "input,gold\na,b,c", 'input\n"unfinished'):
        path = tmp_path / "bad.csv"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            read_records(path)
