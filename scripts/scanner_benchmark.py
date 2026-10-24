"""Annotated synthetic syntax corpus; scores are corpus-specific, never whole-repository recall."""

import json
import tempfile
from pathlib import Path

from cutback.scanner import scan

CORPUS = [
    (
        "direct",
        'from openai import OpenAI\nc=OpenAI()\nc.responses.create(model="gpt-4.1",input="x")',
        1,
        "gpt-4.1",
    ),
    (
        "alias",
        "from openai import OpenAI as AI\nc=AI()\n"
        'c.chat.completions.create(model="gpt-4.1-mini",messages=[])',
        1,
        "gpt-4.1-mini",
    ),
    (
        "async",
        "from openai import AsyncOpenAI\nc=AsyncOpenAI()\nasync def call():\n"
        ' return await c.responses.create(model="gpt-4.1",input="x")',
        1,
        "gpt-4.1",
    ),
    (
        "anthropic",
        "from anthropic import Anthropic\nc=Anthropic()\n"
        'c.messages.create(model="claude-sonnet-4-6",messages=[],max_tokens=16)',
        1,
        "claude-sonnet-4-6",
    ),
    (
        "dynamic",
        "import os\nfrom openai import OpenAI\nc=OpenAI()\n"
        'c.responses.create(model=os.getenv("MODEL_NAME"),input="x")',
        1,
        None,
    ),
    (
        "wrapper",
        "from openai import OpenAI\nc=OpenAI()\ndef wrap(x):\n"
        ' return c.responses.create(model="gpt-4.1",input=x)\ndef route(x):\n return wrap(x)',
        1,
        "gpt-4.1",
    ),
    (
        "unrelated",
        'class Store:\n def create(self,**kw):pass\ns=Store()\ns.create(model="gpt-4.1")',
        0,
        None,
    ),
    (
        "shadow",
        'class OpenAI:pass\nc=OpenAI()\nc.responses.create(model="gpt-4.1",input="x")',
        0,
        None,
    ),
    (
        "comment",
        '# from openai import OpenAI\n# OpenAI().responses.create(model="gpt-4.1")',
        0,
        None,
    ),
    ("literal", 'text="client.responses.create(model=123)"', 0, None),
]


def benchmark():
    tp = fp = fn = resolved = expected_resolved = 0
    details = []
    for name, source, expected, model in CORPUS:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(source, encoding="utf-8")
            result = scan(root)
        found = len(result["call_sites"])
        tp += min(found, expected)
        fp += max(0, found - expected)
        fn += max(0, expected - found)
        if expected:
            expected_resolved += 1
            resolved += int(found == 1 and result["call_sites"][0]["requested_model"] == model)
        details.append({"case": name, "expected_sites": expected, "detected_sites": found})
    return {
        "evidence_kind": "ANNOTATED_SYNTHETIC_SYNTAX_CORPUS",
        "cases": details,
        "call_site_precision": tp / (tp + fp) if tp + fp else None,
        "call_site_recall": tp / (tp + fn) if tp + fn else None,
        "model_resolution_accuracy": resolved / expected_resolved,
        "production_coverage": None,
        "scope": "10 initial syntaxes only; broader scanner/security fixtures are in pytest",
    }


if __name__ == "__main__":
    result = benchmark()
    print(json.dumps(result, indent=2))
    if result["call_site_precision"] != 1 or result["call_site_recall"] != 1:
        raise SystemExit(1)
