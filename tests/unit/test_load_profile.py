import asyncio

import httpx
import pytest
from typer.testing import CliRunner

from cutback.cli import app
from cutback.load_qualification import LocalLoadProfile, latency_summary, qualify_local


def test_latency_quantiles_require_raw_samples_and_declared_minimum():
    samples = [i * 1000000 for i in range(1, 101)]
    result = latency_summary(samples, minimum=20)
    assert result["p50_ms"] == 50 and result["p95_ms"] == 95
    assert result["population_coverage"] == "unknown"
    assert latency_summary(samples[:2], minimum=20)["p95_ms"] is None
    for values in ([True], [-1], [2**63], {"p95_ms": 12}, [1] * 10001):
        with pytest.raises(ValueError):
            latency_summary(values, minimum=20)
    for update in (
        {"events": True},
        {"events": 201},
        {"concurrency": 17},
        {"p95_query_ms_ceiling": float("nan")},
    ):
        with pytest.raises(ValueError):
            LocalLoadProfile(**update)


def test_load_qualification_refuses_remote_destinations_and_requires_explicit_execution(tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(
            qualify_local(
                "http://127.0.0.1:1",
                "must-not-be-used",
                LocalLoadProfile().model_copy(update={"events": 1000000}),
            )
        )
    for endpoint in (
        "https://example.com",
        "http://localhost:8080",
        "http://127.0.0.1:8080/private",
        "http://key@127.0.0.1:8080",
    ):
        with pytest.raises(ValueError, match="OWNED_NUMERIC_LOOPBACK"):
            asyncio.run(qualify_local(endpoint, "must-not-be-used", LocalLoadProfile()))
    result = CliRunner().invoke(
        app, ["qualify", "load", "--profile", str(tmp_path / "not-read.json")]
    )
    assert result.exit_code == 2 and "EXPLICIT_LOCAL_EXECUTION_REQUIRED" in result.stdout


@pytest.mark.parametrize("mode", ["redirect", "oversized", "timeout"])
def test_local_load_http_failures_are_bounded_private_and_never_follow_redirects(monkeypatch, mode):
    native = httpx.AsyncClient
    requested = []

    def respond(request):
        requested.append(request.url)
        if mode == "timeout":
            raise httpx.ReadTimeout("OWNED_PRIVATE_TRANSPORT_DETAIL", request=request)
        if mode == "redirect":
            return httpx.Response(
                307,
                headers={"location": "https://example.invalid/forbidden"},
                content=b"OWNED_PRIVATE_TRANSPORT_DETAIL",
            )
        return httpx.Response(200, content=b"x" * 2097153)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: native(**kwargs, transport=httpx.MockTransport(respond)),
    )
    receipt = asyncio.run(
        qualify_local("http://127.0.0.1:1", "OWNED_PRIVATE_TOKEN", LocalLoadProfile())
    )
    assert receipt["status"] == "FAILED_OR_INCOMPLETE_QUALIFICATION"
    assert len(requested) == 1 and requested[0].host == "127.0.0.1"
    assert "OWNED_PRIVATE" not in str(receipt) and receipt["provider_calls"] == 0
    assert not receipt["cleanup_complete"]
