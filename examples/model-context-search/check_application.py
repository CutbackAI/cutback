"""Explicit local consumer checks with an owned in-memory provider replacement."""

import json
from pathlib import Path
from types import SimpleNamespace

from app import answer


def main():
    cases = [json.loads(line) for line in Path("questions.jsonl").read_text("utf-8").splitlines()]
    calls = []

    def create(**request):
        assert request["store"] is False
        assert request["model"] in {"gpt-4.1", "gpt-4.1-mini"}
        assert 16 <= request["max_output_tokens"] <= 128
        text = request["input"]
        frame = json.loads(text[text.index("{") :])
        calls.append(frame)
        assert frame["query"] == value["query"]
        assert "deadline-days=" + case["gold"] in frame["context"]
        return SimpleNamespace(output_text=case["gold"])

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    for case in cases:
        value = json.loads(case["input"])
        assert answer(client, value["query"], value["context"]) == case["gold"]
    assert len(calls) == len(cases)


if __name__ == "__main__":
    main()
