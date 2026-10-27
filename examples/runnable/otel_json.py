"""Independent synthetic OTLP/HTTP JSON producer. No SDK hooks or provider requests."""

import argparse
import json
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4


def build_export():
    def attribute(key, value):
        return {
            "key": key,
            "value": {"intValue": str(value)} if isinstance(value, int) else {"stringValue": value},
        }

    now = time.time_ns()
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        attribute("service.name", "synthetic-otel-example"),
                        attribute("deployment.environment.name", "staging"),
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": uuid4().hex,
                                "spanId": uuid4().hex[:16],
                                "startTimeUnixNano": str(now - 125000000),
                                "endTimeUnixNano": str(now),
                                "attributes": [
                                    attribute("gen_ai.provider.name", "openai"),
                                    attribute("gen_ai.request.model", "gpt-4.1-mini"),
                                    attribute("gen_ai.response.model", "gpt-4.1-mini"),
                                    attribute("cutback.sdk_surface", "responses.create"),
                                    attribute("gen_ai.usage.input_tokens", 120),
                                    attribute("gen_ai.usage.output_tokens", 16),
                                    attribute("cutback.usage_source", "estimated"),
                                    attribute("cutback.metering_status", "text_only"),
                                    attribute("cutback.traffic_kind", "application"),
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }


def main(endpoint):
    import httpx

    url = urlsplit(endpoint)
    if (
        url.username
        or url.password
        or url.fragment
        or url.query
        or not (
            url.scheme == "https"
            or url.scheme == "http"
            and url.hostname in {"127.0.0.1", "localhost", "::1"}
        )
    ):
        raise ValueError("Use an approved HTTPS destination or loopback development collector")
    token = os.environ["CUTBACK_PROJECT_TOKEN"]
    body = build_export()
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
        for _ in range(2):
            response = client.post(
                endpoint, headers={"Authorization": "Bearer " + token}, json=body
            )
            if response.status_code != 200:
                raise ValueError("Synthetic export rejected; inspect the collector configuration")
    print(
        json.dumps({"synthetic": True, "exported_spans": 1, "replays": 1, "paid_provider_calls": 0})
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1/ingest/otel/v1/traces")
    main(parser.parse_args().endpoint)
