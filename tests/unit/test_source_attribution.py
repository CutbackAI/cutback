"""A fresh consumer's reviewed bootstrap attributes shared calls without payloads."""

import asyncio
import json
import runpy
from collections import Counter
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from cutback.cli import app
from cutback.core.observation import observation_scope
from cutback.scanner import scan


class MemoryExporter:
    def __init__(self, **kwargs):
        self.events = []
        self.counters = Counter()

    def emit(self, event):
        self.events.append(event)

    def count(self, key, amount=1):
        self.counters[key] += amount

    def health(self):
        return dict(self.counters)

    def flush(self, timeout_seconds=0.3):
        return True

    close = flush


def test_source_map_replacement_matches_universal_newlines_on_every_platform(tmp_path):
    from cutback.instrumentation.attribution import SourceMap, build_source_map

    source = tmp_path / "app.py"
    text = (
        'from openai import OpenAI\nc=OpenAI()\nc.responses.create(model="gpt-4.1",input="owned")\n'
    )
    source.write_bytes(text.encode())
    inventory = scan(tmp_path)
    directory = tmp_path / ".cutback"
    directory.mkdir()
    for ending in ("\n", "\r\n"):
        replacement = text.replace("\n", ending)
        mapping = build_source_map(tmp_path, inventory, replacements={"app.py": replacement})
        source.write_bytes(replacement.encode())
        path = directory / "source-map.json"
        path.write_text(json.dumps(mapping))
        assert len(SourceMap(path).sites) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_generated_bootstrap_links_two_workflows_and_preserves_native_results(
    tmp_path, monkeypatch, asynchronous, streaming
):
    monkeypatch.setattr("cutback.instrumentation.openai.Exporter", MemoryExporter)
    for key, value in {
        "CUTBACK_ENABLED": "true",
        "CUTBACK_SERVICE_NAME": "synthetic-consumer",
        "CUTBACK_ENVIRONMENT": "staging",
        "CUTBACK_ENDPOINT": "http://127.0.0.1:8080/v1/ingest/events",
        "CUTBACK_PROJECT_ID": str(uuid4()),
        "CUTBACK_PROJECT_TOKEN": "synthetic-" + "x" * 40,
    }.items():
        monkeypatch.setenv(key, value)
    reply = {
        "id": "resp_synthetic",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "gpt-4.1",
        "output": [],
        "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
    }
    response = f"httpx.Response(200, json={reply!r})"
    if streaming:
        data = (
            "data: "
            + json.dumps({"type": "response.completed", "sequence_number": 0, "response": reply})
            + "\n\ndata: [DONE]\n\n"
        )
        response = (
            f"httpx.Response(200, text={data!r}, headers={{'content-type':'text/event-stream'}})"
        )
    constructor = "AsyncOpenAI" if asynchronous else "OpenAI"
    client = "AsyncClient" if asynchronous else "Client"
    prefix, wait = ("async ", "await ") if asynchronous else ("", "")
    source = f"""import httpx
from openai import {constructor}
client = {constructor}(api_key="synthetic-key", max_retries=0,
    http_client=httpx.{client}(transport=httpx.MockTransport(
        lambda request: {response})))
{prefix}def shared(text):
    return {wait}client.responses.create(model="gpt-4.1", input=text, stream={streaming})
{prefix}def first(text):
    return {wait}shared(text)
{prefix}def second(text):
    return {wait}shared(text)
"""
    file = tmp_path / "app.py"
    file.write_text(source)
    (site,) = scan(tmp_path)["call_sites"]
    proposed = CliRunner().invoke(
        app, ["instrument", "--target", str(tmp_path), "--plan", "--json"]
    )
    assert proposed.exit_code == 0, proposed.exception
    assert file.read_text() == source and not (tmp_path / ".cutback/source-map.json").exists()
    applied = CliRunner().invoke(
        app, ["instrument", "--target", str(tmp_path), "--apply", "--json"]
    )
    assert applied.exit_code == 0, applied.exception
    namespace = runpy.run_path(str(file))
    observer = namespace["_cutback_observer"]

    async def invoke_async(name):
        result = await namespace[name]("synthetic private content")
        if streaming:
            import openai

            assert isinstance(result, openai.AsyncStream)
            async with result:
                assert len([chunk async for chunk in result]) == 1
        else:
            assert result.id == "resp_synthetic"

    try:
        for name in ("first", "second"):
            if asynchronous:
                asyncio.run(invoke_async(name))
            else:
                result = namespace[name]("synthetic private content")
                if streaming:
                    import openai

                    assert isinstance(result, openai.Stream)
                    with result:
                        assert len(list(result)) == 1
                else:
                    assert result.id == "resp_synthetic"
        events = observer.exporter.events
        assert len(events) == 2
        assert {event.call_site_id for event in events} == {site["call_site_id"]}
        assert len({event.workflow_id for event in events}) == 2 and all(
            event.workflow_id for event in events
        )
        assert all(
            event.input_tokens == 10 and event.traffic_kind == "application" for event in events
        )
        assert "private content" not in json.dumps(
            [event.model_dump(mode="json") for event in events]
        )
        with observation_scope("judge", run_id=uuid4()):
            if asynchronous:
                asyncio.run(invoke_async("first"))
            else:
                result = namespace["first"]("synthetic")
                if streaming:
                    list(result)
        assert events[-1].traffic_kind == "judge" and events[-1].experiment_run_id
    finally:
        observer.close()


def test_changed_source_map_file_never_claims_old_identity(tmp_path):
    from cutback.instrumentation.attribution import SourceMap, build_source_map

    source = tmp_path / "app.py"
    source.write_text(
        "from openai import OpenAI\nc=OpenAI()\ndef work():\n"
        " return c.responses.create(model='gpt-4.1',input='owned fixture')\n"
    )
    body = build_source_map(tmp_path, scan(tmp_path))
    directory = tmp_path / ".cutback"
    directory.mkdir()
    path = directory / "source-map.json"
    path.write_text(json.dumps(body))
    source.write_text(source.read_text() + "\n# changed revision\n")
    mapping = SourceMap(path)
    assert not mapping.sites and mapping.resolve() == {}


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("alias_conflict", [False, True])
def test_src_package_workflows_keep_scanner_identity(
    tmp_path, monkeypatch, asynchronous, streaming, alias_conflict
):
    import importlib
    import sys

    from cutback.core.inventory import workflow_identity
    from cutback.instrumentation.attribution import SourceMap, build_source_map
    from cutback.instrumentation.openai import OpenAIInstrumentation

    monkeypatch.setattr("cutback.instrumentation.openai.Exporter", MemoryExporter)
    package = tmp_path / "src/synthetic_tickets"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    reply = {
        "id": "resp_synthetic",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "gpt-4.1",
        "output": [],
        "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
    }
    response = f"httpx.Response(200, json={reply!r})"
    if streaming:
        data = (
            "data: "
            + json.dumps({"type": "response.completed", "sequence_number": 0, "response": reply})
            + "\n\ndata: [DONE]\n\n"
        )
        response = (
            f"httpx.Response(200, text={data!r}, headers={{'content-type':'text/event-stream'}})"
        )
    constructor = "AsyncOpenAI" if asynchronous else "OpenAI"
    client = "AsyncClient" if asynchronous else "Client"
    prefix, wait = ("async ", "await ") if asynchronous else ("", "")
    (package / "native.py").write_text(
        f"import httpx\nfrom openai import {constructor}\n"
        f"client={constructor}(api_key='synthetic-key',max_retries=0,"
        f"http_client=httpx.{client}(transport=httpx.MockTransport(lambda request:{response})))\n"
        f"{prefix}def classify(text):\n"
        f" return {wait}client.responses.create(model='gpt-4.1',input=text,stream={streaming})\n"
    )
    (package / "api.py").write_text(
        "from .native import classify\n"
        f"{prefix}def first(text):\n return {wait}classify(text)\n"
        f"{prefix}def second(text):\n return {wait}classify(text)\n"
    )
    inventory = scan(tmp_path)
    (site,) = inventory["call_sites"]
    expected = {
        workflow_identity(entry["file"], entry["symbol"])[0] for entry in site["entry_points"]
    }
    assert len(expected) == 2
    private = tmp_path / ".cutback"
    private.mkdir()
    path = private / "source-map.json"
    mapping = build_source_map(tmp_path, inventory)
    if alias_conflict:
        for entry in mapping["files"]:
            if entry["path"] == "src/synthetic_tickets/api.py":
                entry["workflows"] += [
                    {
                        "symbol": item["symbol"].removeprefix("synthetic_tickets.api."),
                        "workflow_id": "workflow:" + "0" * 24,
                    }
                    for item in entry["workflows"]
                ]
    path.write_text(json.dumps(mapping))
    monkeypatch.syspath_prepend(str(tmp_path / "src"))
    api = importlib.import_module("synthetic_tickets.api")
    observer = OpenAIInstrumentation(
        service_name="synthetic-consumer",
        environment="staging",
        endpoint="http://127.0.0.1:8080/v1/ingest/events",
        project_id=str(uuid4()),
        token="synthetic-" + "x" * 40,
        identity=("synthetic-package",),
        attribution=SourceMap(path),
    )

    async def call_async():
        for function in (api.first, api.second):
            result = await function("SYNTHETIC_PRIVATE_TICKET_CANARY")
            if streaming:
                async with result:
                    assert len([chunk async for chunk in result]) == 1

    try:
        if asynchronous:
            asyncio.run(call_async())
        else:
            for function in (api.first, api.second):
                result = function("SYNTHETIC_PRIVATE_TICKET_CANARY")
                if streaming:
                    with result:
                        assert len(list(result)) == 1
        events = observer.exporter.events
        assert {event.call_site_id for event in events} == {site["call_site_id"]}
        assert {event.workflow_id for event in events} == ({None} if alias_conflict else expected)
        assert "SYNTHETIC_PRIVATE_TICKET_CANARY" not in json.dumps(
            [event.model_dump(mode="json") for event in events]
        )
    finally:
        observer.close()
        if asynchronous:
            asyncio.run(api.classify.__globals__["client"].close())
        else:
            api.classify.__globals__["client"].close()
        for name in tuple(sys.modules):
            if name == "synthetic_tickets" or name.startswith("synthetic_tickets."):
                sys.modules.pop(name, None)
