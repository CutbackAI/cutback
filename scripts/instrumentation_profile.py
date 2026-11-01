"""Profile actual pinned SDK hooks with closed provider/collector transports.

Run in a separate tool process. This measures ordinary synchronous metadata
calls, never real provider latency, cold-start performance or a production SLA.
"""

import argparse
import json
import math
import os
import platform
import sys
import time
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from pydantic import Field, StrictInt

from cutback.core.models import StrictModel
from cutback.core.prompts import fingerprint
from cutback.core.workspace import prepare_private_output, write_private
from cutback.load_qualification import latency_summary

PRIVATE_INPUT = "OWNED_PRIVATE_PROFILE_INPUT"
PRIVATE_OUTPUT = "OWNED_PRIVATE_PROFILE_OUTPUT"
PROVIDER_KEY = "owned-synthetic-provider-credential"


class Profile(StrictModel):
    contract: str = Field(
        default="cutback.native-hook-profile.v1", pattern=r"^cutback\.native-hook-profile\.v1$"
    )
    samples_per_trial: StrictInt = Field(default=200, ge=20, le=2000)
    warmup_per_trial: StrictInt = Field(default=10, ge=2, le=50)
    queue_capacity: StrictInt = Field(default=64, ge=8, le=512)
    median_added_ms_ceiling: float = Field(default=5.0, strict=True, gt=0, le=50)


def signed_quantiles(values):
    ordered = sorted(values)
    return {
        "p50_ms": ordered[math.ceil(len(values) * 0.5) - 1] / 1_000_000,
        "p95_ms": ordered[math.ceil(len(values) * 0.95) - 1] / 1_000_000,
        "negative_samples": sum(value < 0 for value in values),
    }


def native_client(surface):
    import httpx
    from openai import OpenAI

    response = {
        "id": "resp_profile",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "gpt-4.1-mini-2025-04-14",
        "output": [
            {
                "id": "msg_profile",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": PRIVATE_OUTPUT, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }
    if surface == "chat":
        response = {
            "id": "chatcmpl_profile",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4.1-mini-2025-04-14",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": PRIVATE_OUTPUT},
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
    expected = "https://api.openai.com/v1/" + (
        "responses" if surface == "responses" else "chat/completions"
    )
    if surface == "messages":
        import httpx2
        from anthropic import Anthropic

        response = {
            "id": "msg_profile",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": PRIVATE_OUTPUT}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 20,
                "output_tokens": 10,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }

        def message_reply(request):
            if str(request.url) != "https://api.anthropic.com/v1/messages":
                raise ValueError("PROFILE_UNEXPECTED_PROVIDER_ROUTE")
            return httpx2.Response(200, json=response)

        client = Anthropic(
            api_key=PROVIDER_KEY,
            base_url="https://api.anthropic.com",
            max_retries=0,
            http_client=httpx2.Client(
                transport=httpx2.MockTransport(message_reply), trust_env=False
            ),
        )

        def call():
            return client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=32,
                messages=[{"role": "user", "content": PRIVATE_INPUT}],
            )

        return client, call, "Message", "msg_profile"

    def reply(request):
        if str(request.url) != expected:
            raise ValueError("PROFILE_UNEXPECTED_PROVIDER_ROUTE")
        return httpx.Response(200, json=response)

    client = OpenAI(
        api_key=PROVIDER_KEY,
        base_url="https://api.openai.com/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(reply), trust_env=False),
    )
    if surface == "responses":

        def call():
            return client.responses.create(model="gpt-4.1-mini", input=PRIVATE_INPUT, store=False)

        return client, call, "Response", "resp_profile"

    def call():
        return client.chat.completions.create(
            model="gpt-4.1-mini",
            store=False,
            messages=[{"role": "user", "content": PRIVATE_INPUT}],
        )

    return client, call, "ChatCompletion", "chatcmpl_profile"


def trial(surface, *, observed, outage, profile):
    import httpx

    import cutback
    import cutback.instrumentation.exporter as exports

    token = "owned-profile-project-" + uuid4().hex
    transport_counts = {"event_batches": 0, "health_batches": 0, "privacy_failures": 0}
    original_httpx = exports.httpx

    def collector(request):
        if (
            request.url.host != "127.0.0.1"
            or request.url.port != 9
            or request.url.path
            not in {
                "/v1/ingest/events",
                "/v1/ingest/health",
            }
        ):
            raise ValueError("PROFILE_UNEXPECTED_COLLECTOR_ROUTE")
        raw = request.content.decode()
        if any(value in raw for value in (PRIVATE_INPUT, PRIVATE_OUTPUT, PROVIDER_KEY, token)):
            transport_counts["privacy_failures"] += 1
            raise ValueError("PROFILE_METADATA_PRIVACY_FAILED")
        kind = "event_batches" if request.url.path.endswith("events") else "health_batches"
        transport_counts[kind] += 1
        if outage:
            raise httpx.ConnectError("OWNED_PRIVATE_COLLECTOR_OUTAGE", request=request)
        body = json.loads(raw)
        return httpx.Response(200, json={"accepted": len(body.get("events", [])), "duplicate": 0})

    def export_client(**kwargs):
        return httpx.Client(**kwargs, transport=httpx.MockTransport(collector))

    observer, samples, failure, counters = None, [], None, None
    client, call, native_type, native_id = native_client(surface)
    try:
        if observed:
            # Change only the export module's reference; the actual SDK's httpx
            # classes/clients and the production exporter queue/thread stay real.
            exports.httpx = SimpleNamespace(Client=export_client, HTTPError=httpx.HTTPError)
            os.environ["CUTBACK_PROFILE_PROJECT_TOKEN"] = token
            observer = cutback.init(
                service_name="owned-native-profile",
                environment="staging",
                enabled=True,
                endpoint="http://127.0.0.1:9/v1/ingest/events",
                project_id=str(uuid4()),
                project_key_from_env="CUTBACK_PROFILE_PROJECT_TOKEN",
                capture_content=False,
                providers=("anthropic" if surface == "messages" else "openai",),
                capacity=profile.queue_capacity,
                batch_size=8,
                timeout_seconds=0.01,
            )
        for index in range(profile.warmup_per_trial + profile.samples_per_trial):
            started = time.perf_counter_ns()
            result = call()
            elapsed = time.perf_counter_ns() - started
            if type(result).__name__ != native_type or result.id != native_id:
                raise ValueError("PROFILE_NATIVE_RESPONSE_CHANGED")
            output = (
                result.output_text
                if surface == "responses"
                else result.choices[0].message.content
                if surface == "chat"
                else result.content[0].text
            )
            usage = result.usage
            inputs = usage.prompt_tokens if surface == "chat" else usage.input_tokens
            outputs = usage.completion_tokens if surface == "chat" else usage.output_tokens
            if output != PRIVATE_OUTPUT or inputs != 20 or outputs != 10:
                raise ValueError("PROFILE_NATIVE_CONTENT_OR_USAGE_CHANGED")
            if index >= profile.warmup_per_trial:
                samples.append(elapsed)
    except Exception:
        failure = "PROFILE_NATIVE_TRIAL_FAILED"
    finally:
        try:
            if observer:
                closed = observer.close(timeout_seconds=2)
                observer.exporter._thread.join(timeout=2)
                counters = observer.health()
                expected_count = profile.warmup_per_trial + profile.samples_per_trial
                if (
                    not closed
                    or observer.exporter._thread.is_alive()
                    or counters["observed"] != expected_count
                    or counters["normalization_failed"] != 0
                    or counters["exported"] + counters["dropped"] != counters["observed"]
                    or counters["queue_depth"] != 0
                ):
                    failure = "PROFILE_EXPORT_ACCOUNTING_OR_SHUTDOWN_FAILED"
                if outage and (counters["exported"] != 0 or counters["export_failed"] == 0):
                    failure = "PROFILE_OUTAGE_NOT_EXERCISED"
        except Exception:
            failure = "PROFILE_EXPORT_ACCOUNTING_OR_SHUTDOWN_FAILED"
        finally:
            exports.httpx = original_httpx
            try:
                client.close()
            except Exception:
                failure = "PROFILE_NATIVE_CLIENT_CLEANUP_FAILED"
    if transport_counts["privacy_failures"]:
        failure = "PROFILE_METADATA_PRIVACY_FAILED"
    return {
        "observed": observed,
        "collector_outage": outage,
        "raw_call_latencies_ns": samples,
        "latency": latency_summary(samples, minimum=profile.samples_per_trial),
        "exporter_counters": counters,
        "transport_counts": transport_counts,
        "error": failure,
    }


def summarize_case(surface, outage, trials, profile):
    complete = len(trials) == 4 and all(
        len(row["raw_call_latencies_ns"]) == profile.samples_per_trial for row in trials
    )
    deltas = (
        [
            observed - baseline
            for a, b in ((0, 1), (3, 2))
            for baseline, observed in zip(
                trials[a]["raw_call_latencies_ns"],
                trials[b]["raw_call_latencies_ns"],
                strict=True,
            )
        ]
        if complete
        else []
    )
    summary = signed_quantiles(deltas) if deltas else None
    return {
        "surface": surface,
        "collector_outage": outage,
        "trials_abba": trials,
        "raw_signed_paired_deltas_ns": deltas,
        "paired_delta": summary,
        "passed": bool(
            summary
            and not any(row["error"] for row in trials)
            and summary["p50_ms"] <= profile.median_added_ms_ceiling
        ),
    }


def run_profile(profile):
    import cutback.instrumentation as instrumentation

    if instrumentation._active is not None and not instrumentation._active.closed:
        raise ValueError("PROFILE_REQUIRES_AN_ISOLATED_PROCESS")
    if version("openai") not in {"2.29.0", "3.35.1"} or version("anthropic") != "2.3.0":
        raise ValueError("PROFILE_REQUIRES_QUALIFIED_PINNED_SDKS")
    for key in list(os.environ):
        if key.startswith(("CUTBACK_", "OPENAI_", "ANTHROPIC_")):
            os.environ.pop(key)
    profile = Profile.model_validate(profile)
    cases = []
    for surface in ("responses", "chat", "messages"):
        for outage in (False, True):
            trials = [
                trial(surface, observed=active, outage=outage, profile=profile)
                for active in (False, True, True, False)
            ]
            cases.append(summarize_case(surface, outage, trials, profile))
    result = {
        "contract": "cutback.native-hook-profile-receipt.v1",
        "profile": profile.model_dump(mode="json"),
        "profile_hash": fingerprint(profile.model_dump(mode="json")),
        "status": "PASSED_DECLARED_SYNTHETIC_HOOK_ENVELOPE"
        if all(row["passed"] for row in cases)
        else "FAILED_OR_INCOMPLETE_HOOK_PROFILE",
        "cases": cases,
        "openai_sdk": version("openai"),
        "anthropic_sdk": version("anthropic"),
        "pydantic": version("pydantic"),
        "python": platform.python_version(),
        "system": platform.system(),
        "logical_cpu_count": os.cpu_count(),
        "measurement": (
            "ordinary synchronous native calls; SDK/provider responses and actual exporter HTTP "
            "use closed synthetic transports; no forced flush inside timing"
        ),
        "pairing": (
            "ABBA trials, per-index observed-minus-baseline; signed negative deltas retained "
            "as timing noise, not negative overhead claims"
        ),
        "startup_serverless_stream_async_memory_and_live_network": "not measured by this profile",
        "hardware_and_competing_load": "operator-controlled, not independently attested",
        "paid_provider_calls": 0,
        "production_sla": False,
    }
    return {**result, "receipt_hash": fingerprint(result)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    try:
        path = prepare_private_output(args.receipt)
        if path.exists():
            raise ValueError("PROFILE_RECEIPT_ALREADY_EXISTS")
        receipt = run_profile(Profile.model_validate_json(args.profile.read_text()))
        write_private(path, receipt, exclusive=True)
    except Exception:
        print("NATIVE_HOOK_PROFILE_FAILED_CONFIGURATION_OR_EXECUTION", file=sys.stderr)
        return 1
    passed = receipt["status"] == "PASSED_DECLARED_SYNTHETIC_HOOK_ENVELOPE"
    print("NATIVE_HOOK_PROFILE_PASSED" if passed else "NATIVE_HOOK_PROFILE_FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
