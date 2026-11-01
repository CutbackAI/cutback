"""Actual disposable-database ownership during supervisor failures."""

import json
from uuid import UUID

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url
from support_mixed_supervisor import supervisor


@pytest.mark.postgres
@pytest.mark.parametrize("failure", ["migration_timeout", "create_ack_lost"])
def test_mixed_supervisor_retains_owned_intent_and_never_invents_cleanup(
    postgres_url, tmp_path, monkeypatch, failure
):
    module = supervisor()
    monkeypatch.setenv("CUTBACK_TEST_DATABASE_URL", postgres_url)
    profile = tmp_path / "profile.json"
    output = tmp_path / "new-private" / "qualification" / "receipt.json"
    profile.write_text(json.dumps({"duration_seconds": 10}))
    monkeypatch.setattr(module, "bounded_child", lambda *a, **kw: (None, True))
    real_connect = psycopg.connect
    if failure == "create_ack_lost":

        class LostCreateReply:
            def __init__(self, **kwargs):
                self.connection = real_connect(**kwargs)

            def __enter__(self):
                return self.connection.__enter__()

            def __exit__(self, *args):
                self.connection.__exit__(*args)
                raise RuntimeError("OWNED_PRIVATE_CREATE_REPLY_DETAIL")

        monkeypatch.setattr(psycopg, "connect", LostCreateReply)
    assert module.qualify(profile, output) is False
    monkeypatch.setattr(psycopg, "connect", real_connect)
    receipt = json.loads(output.read_text())
    intent = json.loads(output.with_name(output.name + ".intent.json").read_text())
    name = intent["owned_database_name"]
    assert name == receipt["owned_database_name"] and name.startswith("cutback_mixed_")
    UUID(hex=name.removeprefix("cutback_mixed_"))
    assert "OWNED_PRIVATE" not in output.read_text()
    assert make_url(postgres_url).password not in output.read_text()
    url = make_url(postgres_url)
    kwargs = dict(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        dbname=url.database,
        autocommit=True,
    )
    try:
        with real_connect(**kwargs) as connection:
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
            ).fetchone()
        assert bool(exists) is (failure == "create_ack_lost")
        assert receipt["owned_database_removed"] is (failure == "migration_timeout")
        assert receipt["status"] == "FAILED_OR_INCOMPLETE_MIXED_QUALIFICATION"
        with pytest.raises(ValueError, match="MIXED_RECEIPT_ALREADY_EXISTS"):
            module.qualify(profile, output)
    finally:
        # This test injected the lost acknowledgement after its own successful
        # CREATE. The production supervisor correctly did not infer that fact.
        if failure == "create_ack_lost":
            with real_connect(**kwargs) as connection:
                connection.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
                )
