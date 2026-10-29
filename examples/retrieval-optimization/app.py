"""An independent, local support RAG application with an injectable native SDK client."""

import json
from pathlib import Path

from cutback.core.retrieval import LocalRetrievalIndex, RetrievalCorpus, RetrievalSettings

ROOT = Path(__file__).resolve().parent


def build_input(question, *, settings=None):
    corpus = RetrievalCorpus.model_validate(json.loads((ROOT / "corpus.json").read_text("utf-8")))
    settings = settings or RetrievalSettings.model_validate(
        json.loads((ROOT / "retrieval.json").read_text("utf-8"))
    )
    return LocalRetrievalIndex(corpus, settings).query(question).render(question)


def answer(client, question):
    """Caller supplies a separately authorized native client, including its retry policy."""
    return client.responses.create(
        model="gpt-4.1-mini", input=build_input(question), max_output_tokens=128, store=False
    ).output_text


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Render local retrieval input; no provider call.")
    parser.add_argument(
        "question", nargs="?", default="What is the refund return deadline for amber?"
    )
    print(build_input(parser.parse_args().question))
