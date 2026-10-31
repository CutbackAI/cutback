"""Independent applications for ordinary agent onboarding; no demo seeding."""

import json


def fresh_application(root, kind="classifier", *, model=None, output_cap=None):
    if kind == "classifier":
        source = """from openai import OpenAI
client = OpenAI()
def classify(text):
    return client.responses.create(model="gpt-4.1", input=text,
        instructions="Owned synthetic classification task", max_output_tokens=64)
"""
    elif kind == "extractor":
        source = """from openai import OpenAI
client = OpenAI()
def extract(text):
    return client.chat.completions.create(model="gpt-4.1",
        messages=[{"role":"system","content":"Owned synthetic extraction task"},
                  {"role":"user","content":text}], max_tokens=64,
        response_format={"type":"json_object"})
"""
    elif kind == "messages":
        source = """from anthropic import Anthropic
client = Anthropic()
def classify(text):
    return client.messages.create(model="claude-haiku-4-5", max_tokens=16,
        system="Owned synthetic classification task",
        messages=[{"role":"user", "content":text}])
"""
    else:
        (root / "config.py").write_text('MODEL = "gpt-4.1"\n', encoding="utf-8")
        source = """from openai import OpenAI
from config import MODEL
client = OpenAI()
def shared(text):
    return client.responses.create(model=MODEL, input=text,
        instructions="Owned synthetic shared task", max_output_tokens=64)
def route(text):
    return shared(text)
def classify(text):
    return shared(text)
"""
        if kind == "namespace":
            source = source.replace("from config import MODEL", "import config as cfg").replace(
                "model=MODEL", "model=cfg.MODEL"
            )
    if model is not None:
        assert kind == "classifier"
        source = source.replace('model="gpt-4.1"', f'model="{model}"')
    if output_cap is not None:
        assert kind == "classifier"
        source = source.replace("max_output_tokens=64", f"max_output_tokens={output_cap}")
    (root / "app.py").write_text(source, encoding="utf-8")
    (root / "cases.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "id": f"case-{i}",
                    "input": f"Owned synthetic {chr(65 + i // 26)}{chr(65 + i % 26)} topic",
                    "gold": {"category": "A"} if kind == "extractor" else "A",
                }
            )
            for i in range(200)
        ),
        encoding="utf-8",
    )


def native_response(request):
    import httpx

    body = json.loads(request.content)
    text = '{"category":"A"}' if "messages" in body else "A"
    if "messages" in body:
        return httpx.Response(
            200,
            json={
                "id": "chat_owned",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 2,
                    "total_tokens": 22,
                    "prompt_tokens_details": {"cached_tokens": 0},
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )
    return httpx.Response(
        200,
        json={
            "id": "resp_owned",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": body["model"],
            "output": [
                {
                    "id": "msg_owned",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            ],
            "usage": {
                "input_tokens": 20,
                "output_tokens": 2,
                "total_tokens": 22,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        },
    )


def prepare_fresh(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    kind="classifier",
    *,
    search=False,
    automatic=False,
    for_campaign=False,
    campaign_generator_provider=None,
    retain_case_outputs=False,
    fixture_transport=False,
    min_test_samples=30,
    model=None,
    output_cap=None,
    endpoint="http://127.0.0.1",
):
    import os

    from cutback.agent import analyze, prepare
    from cutback.agent_control import Control
    from cutback.evals.artifacts import EncryptedFileStore

    root = tmp_path / "application"
    root.mkdir()
    fresh_application(root, kind, model=model, output_cap=output_cap)
    state = analyze(root)
    client, admin, application = api
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    application.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    control = Control(endpoint, admin["Authorization"][7:], client=client)
    workflow = state["profiles"][0]["binding"]["workflow_id"]
    args = dict(
        files=["cases.jsonl"],
        rights_basis="synthetic",
        rights_ref="owned-agent-fixtures",
        max_usd="5",
        max_requests=1000,
        workflow=workflow,
        minimum=0.7,
        independent_labels=True,
        search=search,
        automatic=automatic,
        for_campaign=for_campaign,
        campaign_generator_provider=campaign_generator_provider,
        retain_case_outputs=retain_case_outputs,
        fixture_transport=fixture_transport,
        min_test_samples=min_test_samples,
    )
    result = prepare(root, control, tenant[0], **args)
    return root, control, result, args
