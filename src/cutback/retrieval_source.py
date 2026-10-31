"""Inspect a qualified retrieval producer without importing it or parsing corpus content."""

import hashlib
from pathlib import Path

from cutback.core.request_profiles import safe_relative
from cutback.core.retrieval_producer import RetrievalProducerBinding, qualify
from cutback.retrieval_changes import inspect_configuration


def inspect_producer(root: Path, name: str):
    from cutback.application_checks import snapshot, source_root

    safe_relative(name)
    root = source_root(root)
    parent = Path(name).parent
    settings_file = (parent / "retrieval.json").as_posix()
    corpus_file = (parent / "corpus.json").as_posix()
    settings, binding = inspect_configuration(root, settings_file)
    tree = snapshot(root)
    if name not in tree.files or corpus_file not in tree.files:
        raise ValueError("RETRIEVAL_TRACKED_PRODUCER_AND_CORPUS_REQUIRED")
    path = root / name
    if path.stat().st_size > 65536 or (root / corpus_file).stat().st_size > 512000:
        raise ValueError("RETRIEVAL_PRODUCER_OR_CORPUS_BOUND")
    raw = path.read_bytes()
    values, _ = qualify(raw)
    hashed = hashlib.sha256(raw).hexdigest()
    if (
        tree.tree_hash != binding.source_tree_hash
        or tree.files.get(name) != hashed
        or snapshot(root) != tree
    ):
        raise ValueError("RETRIEVAL_SOURCE_CHANGED_DURING_INSPECTION")
    producer = RetrievalProducerBinding(
        file=name,
        sha256=hashed,
        source_tree_hash=tree.tree_hash,
        commit=tree.commit,
        model=values[0],
        max_output_tokens=values[1],
        corpus_file=corpus_file,
    )
    return settings, binding, producer
