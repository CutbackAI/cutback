"""Independent synthetic application using the fixed whole-policy consumer."""

from cutback.application_checks import git
from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.policy_producer import PROGRAM
from cutback.evals.experiments import ModelSpec


def application(path):
    root = path / "source-policy-application"
    root.mkdir()
    baseline = ModelSpec(
        key="reference",
        provider="openai",
        model="gpt-4.1",
        instruction="OWNED_PRIVATE_POLICY_INSTRUCTION",
        max_output_tokens=64,
    )
    policy = SourceImprovementPolicy(
        workflow="routing-task", max_input_bytes=512, baseline=baseline, strategy=baseline
    )
    (root / "app.py").write_text(PROGRAM, encoding="utf-8")
    (root / "policy.json").write_bytes(policy.configuration_bytes())
    (root / ".gitignore").write_text(".cutback/\n__pycache__/\n", encoding="utf-8")
    (root / "test_app.py").write_text(
        """import json
import unittest
import httpx
from openai import OpenAI
from openai.types.responses import Response
from app import classify

def handler(request):
    body = json.loads(request.content)
    output = "B" if body["model"] == "gpt-4.1-mini" and len(body["input"].encode()) > 100 else "A"
    return httpx.Response(200, json={
        "id": "resp_owned_application", "object": "response", "created_at": 1,
        "status": "completed", "model": body["model"],
        "output": [{"id": "msg_owned", "type": "message", "role": "assistant",
                    "status": "completed", "content": [{"type": "output_text", "text": output,
                                                          "annotations": []}]}],
        "usage": {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}},
        "error": None, "incomplete_details": None, "parallel_tool_calls": False,
        "tools": [], "tool_choice": "auto", "metadata": {}, "temperature": 1,
        "top_p": 1, "truncation": "disabled", "text": {"format": {"type": "text"}},
    })

class Contract(unittest.TestCase):
    def test_native_short_and_long(self):
        client = OpenAI(api_key="owned-synthetic-no-provider-key", max_retries=0,
                        http_client=httpx.Client(
                            transport=httpx.MockTransport(handler), trust_env=False))
        try:
            for text in ("owned short", "x" * 150):
                result = classify(client, text)
                self.assertIsInstance(result, Response)
                self.assertEqual(result.output_text, "A")
        finally:
            client.close()

if __name__ == "__main__":
    unittest.main()
""",
        encoding="utf-8",
    )
    git(root, "init", "--template=")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent source-policy application",
    )
    return root
