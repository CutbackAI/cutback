import runpy
from pathlib import Path


def test_test_runner_scrubs_dsn_bearer_and_display_once_fields_without_import_side_effects():
    root = Path(__file__).resolve().parents[2]
    sanitizer = runpy.run_path(str(root / "scripts/check.py"))["sanitized_line"]
    for value in (
        "postgresql+psycopg://owned-user:OWNED_SECRET@127.0.0.1/database",
        "{'Authorization': 'Bearer OWNED_SECRET_VALUE'}",
        '{"access_token": "OWNED_SECRET_VALUE"}',
        "{'token': 'OWNED_SECRET_VALUE'}",
    ):
        assert "OWNED_SECRET" not in sanitizer(value)
    assert sanitizer("12 passed; no credentials") == "12 passed; no credentials"
