import hashlib
import hmac
import json
import ssl

import pytest
from httpcore._backends.sync import SyncBackend

from cutback.server.notifications import DeadlineStream, deliver_http, destinations


def entry(**extra):
    return {
        "url": "https://hooks.example.test/incidents",
        "addresses": ["8.8.8.8"],
        "secret_env": "CUTBACK_WEBHOOK_SECRET_SYNTHETIC",
        "project_ids": ["00000000-0000-0000-0000-000000000001"],
        **extra,
    }


@pytest.mark.parametrize(
    "value",
    [
        entry(url="http://hooks.example.test/"),
        entry(url="https://user:secret@hooks.example.test/"),
        entry(url="https://hooks.example.test/?secret=forbidden"),
        entry(url="https://hooks.example.test/#fragment"),
        entry(url="https://hooks.example.test:8080/"),
        entry(addresses=["127.0.0.1"]),
        entry(addresses=["10.0.0.1"]),
        entry(addresses=["169.254.169.254"]),
        entry(addresses=["::1"]),
        entry(addresses=["64:ff9b::a00:1"]),
        entry(addresses=["2002:0a00:0001::"]),
        entry(addresses=["224.0.0.1"]),
        entry(secret_env="CUTBACK_ADMIN_TOKEN"),
        entry(url="https://hooks.example.test/\r\nInjected:bad"),
    ],
)
def test_operator_destination_denies_ssrf_credentials_and_unqualified_networks(monkeypatch, value):
    monkeypatch.setenv("CUTBACK_WEBHOOK_DESTINATIONS", json.dumps({"synthetic": value}))
    with pytest.raises(ValueError, match="^WEBHOOK_OPERATOR_CONFIGURATION_INVALID$"):
        destinations()


def test_pinned_transport_uses_public_literal_original_tls_host_and_exact_signature(monkeypatch):
    # Actual qualified httpcore 1.0.9 HTTP/1.1 and TLS path; only the final TCP
    # stream is synthetic. There is no DNS lookup or outbound connection.
    observed = {"writes": bytearray()}

    class Stream:
        response = b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n"

        def write(self, buffer, timeout=None):
            observed["writes"].extend(buffer)

        def read(self, max_bytes, timeout=None):
            response, self.response = self.response, b""
            return response

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            observed["tls_host"] = server_hostname
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED and ssl_context.check_hostname
            return self

        def get_extra_info(self, info):
            return None

        def close(self):
            pass

    def connect(self, host, port, timeout=None, local_address=None, socket_options=None):
        observed["address"] = (host, port)
        return Stream()

    monkeypatch.setattr(SyncBackend, "connect_tcp", connect)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    payload, secret = b'{"schema":"cutback.incident.v1"}', "synthetic-signing-key-" + "x" * 32
    assert deliver_http(entry(), payload, "synthetic-delivery", 123, secret) == 204
    assert observed["address"] == ("8.8.8.8", 443)
    assert observed["tls_host"] == "hooks.example.test"
    request = bytes(observed["writes"])
    expected = (
        hmac.new(secret.encode(), b"123.synthetic-delivery." + payload, hashlib.sha256)
        .hexdigest()
        .encode()
    )
    assert b"X-Cutback-Signature: sha256=" + expected in request
    assert b"Idempotency-Key: synthetic-delivery" in request
    assert b"Host: hooks.example.test" in request
    assert secret.encode() not in request and b"Authorization:" not in request


def test_total_deadline_also_bounds_slow_headers_tls_and_writes(monkeypatch):
    import httpcore

    from cutback.server import notifications

    monkeypatch.setattr(notifications.time, "monotonic", lambda: 11)

    class NeverCalled:
        def read(self, *args, **kwargs):
            raise AssertionError("deadline must be enforced before read")

        def write(self, *args, **kwargs):
            raise AssertionError("deadline must be enforced before write")

        def start_tls(self, *args, **kwargs):
            raise AssertionError("deadline must be enforced before TLS")

    stream = DeadlineStream(NeverCalled(), 10)
    with pytest.raises(httpcore.ReadTimeout):
        stream.read(1)
    with pytest.raises(httpcore.WriteTimeout):
        stream.write(b"x")
    with pytest.raises(httpcore.ConnectTimeout):
        stream.start_tls(ssl.create_default_context(), "hooks.example.test")
