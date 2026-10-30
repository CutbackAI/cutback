"""Authenticated list-price interpretation with real scoped HTTP/PostgreSQL."""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from support_browser import browser_command
from test_pipeline import server


@pytest.mark.postgres
def test_modern_pricing_dashboard(api, tenant):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for the authenticated dashboard gate")
    root = Path(__file__).resolve().parents[2]
    from cutback.server.db import RateOverride

    with api[2].state.sessions.begin() as session:
        session.add_all(
            [
                RateOverride(
                    project_id=UUID(tenant[0]),
                    provider="openai",
                    model=f"synthetic-contract-{index}",
                    service_tier="default",
                    valid_from=datetime.now(UTC) - timedelta(days=1),
                    rates={
                        "input": "1",
                        "output": "2",
                        "cache_read": "0.1",
                        "cache_write": None,
                        "cache_write_1h": None,
                    },
                    approved_by="synthetic-browser-fixture",
                )
                for index in range(51)
            ]
        )
    token = api[1]["Authorization"][7:]
    with server(api[2]) as endpoint:
        completed = subprocess.run(
            browser_command(root / "web", "modern-pricing.spec.ts"),
            cwd=root / "web",
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_KEY": token,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_MODERN_PRICING": "true",
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
    output = (completed.stdout + completed.stderr).replace(token, "<credential-omitted>")
    assert completed.returncode == 0, output
