import os
from pathlib import Path

from cutback.source_policy import load_policy, responses_create

ROOT = Path(__file__).resolve().parent


def classify(client, text):
    policy = load_policy(ROOT / "policy.json")
    scope_file = (
        os.environ.get("CUTBACK_NATIVE_CACHE_SCOPE_FILE") if policy.cache_execution else None
    )
    return responses_create(client, text, policy, scope_file=scope_file)
