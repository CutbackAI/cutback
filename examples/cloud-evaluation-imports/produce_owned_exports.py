"""Produce synthetic local SDK exports; invoked only by the reviewed launcher."""

import contextlib
import hashlib
import importlib.metadata as metadata
import io
import json
import socket
import sys
from pathlib import Path

root = Path(sys.argv[1])
versions = {
    name: metadata.version(name)
    for name in ("azure-ai-evaluation", "google-cloud-aiplatform", "pandas")
}
assert versions == {
    "azure-ai-evaluation": "1.18.10",
    "google-cloud-aiplatform": "1.153.1",
    "pandas": "2.3.3",
}
original_create, original_connect, original_connect_ex = (
    socket.create_connection,
    socket.socket.connect,
    socket.socket.connect_ex,
)
blocked = []


def loopback(address):
    if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
        blocked.append(True)
        raise RuntimeError("FIXTURE_EXTERNAL_NETWORK_FORBIDDEN")


def create(address, *args, **kwargs):
    loopback(address)
    return original_create(address, *args, **kwargs)


def connect(self, address):
    loopback(address)
    return original_connect(self, address)


def connect_ex(self, address):
    loopback(address)
    return original_connect_ex(self, address)


socket.create_connection, socket.socket.connect, socket.socket.connect_ex = (
    create,
    connect,
    connect_ex,
)


def owned_accuracy(query: str, response: str, ground_truth: str):
    return {"score": float(response == ground_truth), "passed": response == ground_truth}


rows = [
    {
        "query": "Synthetic ticket: restore the unavailable test service",
        "response": "Urgent",
        "ground_truth": "Urgent",
    },
    {
        "query": "Synthetic ticket: explain the sample usage guide",
        "response": "Urgent",
        "ground_truth": "Normal",
    },
]
input_file = root / "input.jsonl"
input_file.write_text(
    "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8", newline="\n"
)
diagnostics = io.StringIO()
with contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics):
    from azure.ai.evaluation import evaluate

    result = evaluate(data=str(input_file), evaluators={"owned_accuracy": owned_accuracy})
    import pandas as pd
    from vertexai.evaluation import EvalResult

    table = EvalResult(
        summary_metrics={"exact_match/mean": 0.5},
        metrics_table=pd.DataFrame(
            [
                {
                    "prompt": row["query"],
                    "response": row["response"],
                    "reference": row["ground_truth"],
                    "exact_match/score": float(row["response"] == row["ground_truth"]),
                }
                for row in rows
            ]
        ),
    )
    table.metrics_table.to_json(
        root / "vertexai-1.153.1-metrics.jsonl", orient="records", lines=True
    )
    unknown = EvalResult(
        summary_metrics={},
        metrics_table=pd.DataFrame(
            [
                {
                    "prompt": "Synthetic unknown metric",
                    "response": "Normal",
                    "exact_match/score": None,
                }
            ]
        ),
    )
    unknown.metrics_table.to_json(
        root / "vertexai-1.153.1-missing.jsonl", orient="records", lines=True
    )
assert not blocked, "FIXTURE_UPSTREAM_ATTEMPTED_EXTERNAL_NETWORK"
assert len(result["rows"]) == 2
# Root run URLs, configuration, diagnostics and paths never enter the fixtures.
(root / "azure-ai-evaluation-1.18.10-results.json").write_text(
    json.dumps({key: result[key] for key in ("rows", "metrics")}, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
    newline="\n",
)
names = [
    "azure-ai-evaluation-1.18.10-results.json",
    "vertexai-1.153.1-metrics.jsonl",
    "vertexai-1.153.1-missing.jsonl",
]
print(
    json.dumps(
        {
            "versions": versions,
            "files": {
                name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names
            },
            "provider_calls": 0,
            "cloud_evaluation_service_invoked": False,
            "external_connection_attempts": 0,
            "basis": "synthetic_local_callable_and_result_table_only_not_cloud_model_quality",
        }
    )
)
