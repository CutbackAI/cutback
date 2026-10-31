"""Independent synthetic source fixture shared by installed demos and examples."""

import json
from pathlib import Path


def fixture():
    from cutback.core.routing_producer import PROGRAM
    from cutback.demo_routing import sample_cases
    from cutback.evals.experiments import ModelSpec, RouteSpec

    baseline = ModelSpec(
        key="application-baseline",
        provider="openai",
        model="gpt-4.1",
        instruction="Classify the declared ticket priority. Return only Urgent or Normal.",
        max_output_tokens=64,
    )
    policy = ModelSpec(
        key="application-policy",
        provider="router",
        model="input-size-v1",
        routes=[RouteSpec(max_input_bytes=4000, target=baseline)],
    )
    return {
        "app.py": PROGRAM,
        "routing.json": policy.model_dump_json(indent=2) + "\n",
        "tickets.jsonl": "".join(json.dumps(row) + "\n" for row in sample_cases()),
        "check_application.py": Path(__file__).with_name("routing_check.py").read_text("utf-8"),
        ".gitignore": "__pycache__/\n.cutback/\n.venv/\n",
    }
