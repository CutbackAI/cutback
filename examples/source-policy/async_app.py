from pathlib import Path

from cutback.source_policy import load_policy, responses_create_async

ROOT = Path(__file__).resolve().parent


async def classify(client, text):
    policy = load_policy(ROOT / "policy.json")
    return await responses_create_async(client, text, policy)
