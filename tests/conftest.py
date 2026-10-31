"""All provider responses are synthetic. Tests never call a public inference endpoint."""

import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cutback.core.models import LLMEvent


@pytest.fixture
def event():
    return LLMEvent(
        environment="staging",
        service_name="synthetic-test",
        provider="openai",
        sdk_surface="responses.create",
        requested_model="gpt-4.1-mini",
        response_model="gpt-4.1-mini-2025-04-14",
        trace_id="a" * 32,
        span_id="b" * 16,
        occurred_at=datetime.now(UTC),
        duration_ms=125,
        status="success",
        input_tokens=1000,
        output_tokens=100,
        cache_read_tokens=200,
        reasoning_tokens=20,
        usage_source="provider_reported",
        metering_status="text_only",
    )


@pytest.fixture(scope="session")
def postgres_url():
    base = os.getenv("CUTBACK_TEST_DATABASE_URL")
    if not base:
        pytest.skip("set CUTBACK_TEST_DATABASE_URL; PostgreSQL tests are not silently emulated")
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    url = make_url(base)
    if url.host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("tests require a local disposable PostgreSQL server")
    # Unique dedicated DB per test session. Never mutate the supplied base database.
    db_name = "cutback_test_" + uuid4().hex
    kwargs = {
        "host": url.host,
        "port": url.port or 5432,
        "user": url.username,
        "password": url.password,
        "dbname": url.database,
    }
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    test_url = url.set(database=db_name).render_as_string(hide_password=False)
    old = os.environ.get("CUTBACK_DATABASE_URL")
    os.environ["CUTBACK_DATABASE_URL"] = test_url
    from cutback.server.migrate import migrate

    migrate()
    yield test_url
    if old is None:
        os.environ.pop("CUTBACK_DATABASE_URL", None)
    else:
        os.environ["CUTBACK_DATABASE_URL"] = old
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name)))


@pytest.fixture
def isolated_postgres_url(postgres_url, monkeypatch):
    """A fresh owned database for destructive migration tests, independent of test order."""
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    from cutback.server.migrate import migrate

    url = make_url(postgres_url)
    assert url.host in {"127.0.0.1", "localhost", "::1"}
    assert url.database.startswith("cutback_test_")
    name = "cutback_test_" + uuid4().hex
    kwargs = {
        "host": url.host,
        "port": url.port or 5432,
        "user": url.username,
        "password": url.password,
        "dbname": url.database,
    }
    with psycopg.connect(**kwargs, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    target = url.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("CUTBACK_DATABASE_URL", target)
    try:
        migrate()
        yield target
    finally:
        with psycopg.connect(**kwargs, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def api_database_url(postgres_url):
    return postgres_url


@pytest.fixture
def api(api_database_url):
    import re
    import secrets

    from fastapi.testclient import TestClient
    from sqlalchemy.exc import SQLAlchemyError

    from cutback.server.api import create_app

    admin = secrets.token_urlsafe(48)
    app = create_app(database_url=api_database_url, admin_token=admin, allow_insecure_http=True)
    app.state.fixture_storage_diagnostics = []
    original_handler = app.exception_handlers[SQLAlchemyError]

    async def storage_diagnostic(request, exc):
        original = getattr(exc, "orig", None)
        state = getattr(original, "sqlstate", None)
        if not isinstance(state, str) or not re.fullmatch(r"[0-9A-Z]{5}", state):
            state = None
        message = str(original).casefold() if original is not None else ""
        category = next(
            (
                code
                for phrase, code in (
                    ("timeout expired", "connection_timeout"),
                    ("connection timeout", "connection_timeout"),
                    ("server closed the connection", "connection_closed"),
                    ("connection refused", "connection_refused"),
                    ("too many clients", "connection_limit"),
                    ("does not exist", "missing_database_or_relation"),
                    ("statement timeout", "statement_timeout"),
                    ("deadlock", "deadlock"),
                    ("authentication failed", "authentication"),
                )
                if phrase in message
            ),
            "not_classified",
        )
        diagnostics = app.state.fixture_storage_diagnostics
        diagnostics.append(
            {
                "exception_type": type(exc).__name__,
                "driver_type": type(original).__name__ if original is not None else None,
                "sqlstate": state,
                "category": category,
            }
        )
        del diagnostics[:-8]
        return await original_handler(request, exc)

    app.add_exception_handler(SQLAlchemyError, storage_diagnostic)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, {"Authorization": f"Bearer {admin}"}, app


@pytest.fixture
def tenant(api):
    client, admin, _ = api
    project_response = client.post(
        "/v1/projects", json={"slug": "synthetic-" + uuid4().hex}, headers=admin
    )
    assert project_response.status_code == 201, "PROJECT_FIXTURE_CREATE_FAILED " + json.dumps(
        api[2].state.fixture_storage_diagnostics
    )
    project_id = project_response.json()["id"]
    write_response = client.post(
        f"/v1/projects/{project_id}/keys", json={"role": "ingest"}, headers=admin
    )
    assert write_response.status_code == 201, "INGEST_FIXTURE_KEY_CREATE_FAILED"
    write = write_response.json()
    read_response = client.post(
        f"/v1/projects/{project_id}/keys", json={"role": "viewer"}, headers=admin
    )
    assert read_response.status_code == 201, "VIEWER_FIXTURE_KEY_CREATE_FAILED"
    read = read_response.json()
    return (
        project_id,
        {"Authorization": f"Bearer {write['token']}"},
        {"Authorization": f"Bearer {read['token']}"},
    )
