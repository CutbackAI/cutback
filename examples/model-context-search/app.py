"""Independent support-policy application; the caller supplies its native SDK client."""

import json
from pathlib import Path

from cutback.core.compression import CompressionSettings, ContextInput, compress_context

ROOT = Path(__file__).resolve().parent


def build_input(query, context, *, settings=None):
    settings = settings or CompressionSettings.model_validate(
        json.loads((ROOT / "compression.json").read_text("utf-8"))
    )
    return compress_context(ContextInput(query=query, context=context), settings).render()


def answer(client, query, context):
    return client.responses.create(
        model="gpt-4.1", input=build_input(query, context), max_output_tokens=128, store=False
    ).output_text


if __name__ == "__main__":
    value = json.loads((ROOT / "question.json").read_text("utf-8"))
    print(build_input(value["query"], value["context"]))
