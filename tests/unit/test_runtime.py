import pytest

from cutback.server.runtime import configure_database, load_secret, trusted_proxies


def test_secret_file_database_and_proxy_trust_are_explicit(monkeypatch, tmp_path):
    import os

    for name in ("CUTBACK_DATABASE_URL", "CUTBACK_DB_PASSWORD"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    path = tmp_path / "credential"
    path.write_text("synthetic-password-with:@characters", encoding="utf-8")
    monkeypatch.delenv("CUTBACK_DATABASE_URL", raising=False)
    monkeypatch.delenv("CUTBACK_DATABASE_URL_FILE", raising=False)
    monkeypatch.delenv("CUTBACK_DB_PASSWORD", raising=False)
    monkeypatch.setenv("CUTBACK_DB_PASSWORD_FILE", str(path))
    monkeypatch.setenv("CUTBACK_DB_HOST", "private-postgres")
    configure_database()
    assert "synthetic-password-with%3A%40characters" in os.environ["CUTBACK_DATABASE_URL"]
    assert trusted_proxies("127.0.0.1,10.10.0.0/24") == "127.0.0.1,10.10.0.0/24"
    for value in ("*", "0.0.0.0/0", "::/0", "host.example", "10.10.1.1/24"):
        with pytest.raises(ValueError):
            trusted_proxies(value)
    monkeypatch.delenv("CUTBACK_DB_PASSWORD", raising=False)
    path.write_bytes(b"x" * 16385)
    with pytest.raises(ValueError, match="SECRET_FILE"):
        load_secret("CUTBACK_DB_PASSWORD")
