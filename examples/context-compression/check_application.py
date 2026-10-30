"""Run explicitly in an isolated check workspace; makes no provider or network call."""

import json
from pathlib import Path

from app import build_input


def main():
    value = json.loads((Path(__file__).parent / "question.json").read_text("utf-8"))
    framed = build_input(value["query"], value["context"])
    frame = json.loads(framed[framed.index("{") :])
    assert frame["query"] == value["query"]
    assert "deadline-days=14" in frame["context"]


if __name__ == "__main__":
    main()
