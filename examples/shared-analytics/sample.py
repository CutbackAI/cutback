"""Create a resumable local shared view over existing permitted metadata; no inference."""

import argparse
import ipaddress
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cutback.core.config import validate_endpoint
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import (
    prepare_private_output,
    read_private,
    workspace_lock,
    write_private,
)
from cutback.demo_analytics import panels


def demonstrate(endpoint, project, token, receipt, *, execute_local=False, transport=None):
    endpoint = validate_endpoint(endpoint).rstrip("/")
    parts = urlsplit(endpoint)
    try:
        local = ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        local = False
    if not local or parts.path or parts.query or parts.fragment:
        raise ValueError("ANALYTICS_EXAMPLE_REQUIRES_OWNED_NUMERIC_LOOPBACK")
    if type(execute_local) is not bool:
        raise ValueError("ANALYTICS_EXAMPLE_EXPLICIT_BOOLEAN_REQUIRED")
    if not execute_local:
        return {
            "status": "PREVIEW_ONLY",
            "action": "Create one shared chart definition over existing metadata, then read it.",
            "panel_metrics": [panel["metric"] for panel in panels()],
            "provider_calls_added": 0,
            "network_requests": 0,
            "content_capture_authorized": False,
        }
    project = str(UUID(project))
    if not UUID(project).int:
        raise ValueError("ANALYTICS_EXAMPLE_PROJECT_REQUIRED")
    import httpx

    requested = Path(receipt).absolute()
    if requested.parent.name != ".cutback" or not re.fullmatch(
        r"[A-Za-z0-9_.-]+\.json", requested.name
    ):
        raise ValueError("ANALYTICS_EXAMPLE_IGNORED_WORKSPACE_REQUIRED")
    destination = prepare_private_output(requested)
    intent = {"contract": "cutback.analytics-example.v1", "endpoint": endpoint, "project": project}
    with workspace_lock(destination.parent):
        ignored = destination.parent / ".gitignore"
        if ignored.is_symlink() or (ignored.exists() and ignored.stat().st_size > 65536):
            raise ValueError("ANALYTICS_EXAMPLE_UNSAFE_IGNORE_FILE")
        if not ignored.exists():
            ignored.write_text("*\n", encoding="utf-8")
        else:
            content = ignored.read_text(encoding="utf-8")
            rule = "/" + destination.name
            if rule not in content.splitlines():
                with ignored.open("a", encoding="utf-8") as handle:
                    handle.write("\n" + rule + "\n")
        state = read_private(destination) if destination.exists() else None
        if state is None:
            identifier = str(uuid4())
            state = {
                "intent": intent,
                "phase": "prepared",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "body": {
                    "dashboard_id": identifier,
                    "key": "sample-health-" + UUID(identifier).hex[:12],
                    "expected_revision": 0,
                    "environment": "staging",
                    "window_days": 7,
                    "panels": panels(),
                },
            }
            state["body_sha256"] = fingerprint(state["body"])
            write_private(destination, state, exclusive=True)
        if (
            not isinstance(state, dict)
            or state.get("intent") != intent
            or state.get("phase") not in {"prepared", "saved", "complete"}
            or fingerprint(state.get("body")) != state.get("body_sha256")
        ):
            raise ValueError("ANALYTICS_EXAMPLE_INTENT_CHANGED")
        if state["phase"] == "complete":
            return {
                **state["receipt"],
                "source_freshness": "saved_receipt_not_rechecked",
                "network_requests": 0,
            }
        expires = datetime.fromisoformat(state["expires_at"])
        if expires.tzinfo is None or expires <= datetime.now(UTC):
            raise ValueError("ANALYTICS_EXAMPLE_INTENT_EXPIRED_REVIEW_REQUIRED")
        count = 0
        with httpx.Client(
            base_url=endpoint,
            headers={"Authorization": "Bearer " + token},
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=10,
        ) as client:

            def request(method, path, body=None, expected=200):
                nonlocal count
                count += 1
                response = client.request(method, path, **({"json": body} if body else {}))
                if response.status_code != expected:
                    raise ValueError("ANALYTICS_EXAMPLE_CURRENT_RIGHTS_OR_STATE_REFUSED")
                return response.json()

            ready = request("GET", "/v1/health/ready")
            if ready.get("analytics_dashboard_contract") != "cutback.analytics-dashboard.v1":
                raise ValueError("ANALYTICS_EXAMPLE_MATCHING_SERVER_REQUIRED")
            base = f"/v1/projects/{project}/analytics-dashboards"
            if state["phase"] == "prepared":
                saved = request("POST", base, state["body"], expected=201)
                if saved["id"] != state["body"]["dashboard_id"] or saved["revision"] != 1:
                    raise ValueError("ANALYTICS_EXAMPLE_CANONICAL_IDENTITY_MISMATCH")
                state["phase"], state["saved"] = "saved", saved
                write_private(destination, state)
            saved = state["saved"]
            data = request(
                "GET",
                base + "/" + saved["id"] + "/data?expected_revision=" + str(saved["revision"]),
            )
            if (
                data["definition_sha256"] != saved["definition_sha256"]
                or data["content_included"] is not False
            ):
                raise ValueError("ANALYTICS_EXAMPLE_RETAINED_DEFINITION_CHANGED")
            result = {
                "status": "COMPLETE",
                "dashboard_id": saved["id"],
                "revision": saved["revision"],
                "definition_sha256": saved["definition_sha256"],
                "generated_at": data["generated_at"],
                "source_freshness": "live_metadata_at_read_time",
                "provider_calls_added": 0,
                "content_included": False,
                "population_coverage": "unknown",
                "network_requests": count,
                "dashboard_url": endpoint + "/dashboard/?project=" + project,
            }
            state["phase"], state["receipt"] = "complete", result
            write_private(destination, state)
            return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8766")
    parser.add_argument("--project")
    parser.add_argument("--key-env", default="CUTBACK_CONTROL_TOKEN")
    parser.add_argument("--receipt", type=Path, default=Path(".cutback/shared-analytics.json"))
    parser.add_argument("--execute-local", action="store_true")
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", args.key_env):
            raise ValueError("ANALYTICS_EXAMPLE_KEY_ENV_NAME_INVALID")
        token = os.environ.get(args.key_env, "") if args.execute_local else ""
        if args.execute_local and (not args.project or not token):
            raise ValueError("ANALYTICS_EXAMPLE_PROJECT_AND_CURRENT_EDITOR_KEY_REQUIRED")
        result = demonstrate(
            args.endpoint, args.project, token, args.receipt, execute_local=args.execute_local
        )
    except Exception as error:
        code = (
            str(error)
            if type(error) is ValueError and re.fullmatch(r"[A-Z_]{5,120}", str(error))
            else "ANALYTICS_EXAMPLE_STOPPED"
        )
        print(json.dumps({"code": code, "automatic_retry": False}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
