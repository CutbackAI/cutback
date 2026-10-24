import base64
import hashlib
import json
import secrets
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from cutback.server.identity import OIDCVerifier


@pytest.fixture
def identity():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    raw = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    raw.update(kid="synthetic-rsa", use="sig", alg="RS256")
    verifier = OIDCVerifier(
        "https://identity.example",
        "cutback-api",
        "https://identity.example/keys",
        client_id="cutback-browser",
    )
    verifier.keys = {"synthetic-rsa": jwt.PyJWK.from_dict(raw)}
    verifier.expires, verifier.last_refresh = time.monotonic() + 300, time.monotonic()

    def token(**changes):
        now = int(datetime.now(UTC).timestamp())
        return jwt.encode(
            {
                "iss": verifier.issuer,
                "aud": verifier.audience,
                "sub": "synthetic-user",
                "iat": now,
                "exp": now + 300,
                **changes,
            },
            private,
            algorithm="RS256",
            headers={"kid": "synthetic-rsa"},
        )

    return verifier, token


@pytest.mark.postgres
def test_oidc_uses_live_database_membership_not_token_role(api, identity):
    client, admin, app = api
    verifier, token = identity
    app.state.oidc_verifier = verifier
    org = client.post(
        "/v1/organizations", headers=admin, json={"name": "Synthetic identity team"}
    ).json()["id"]
    other = client.post(
        "/v1/organizations", headers=admin, json={"name": "Other synthetic team"}
    ).json()["id"]
    projects = [
        client.post(
            "/v1/projects",
            headers=admin,
            json={"slug": "identity-" + secrets.token_hex(8), "organization_id": organization},
        ).json()["id"]
        for organization in (org, org, other)
    ]
    member = client.post(
        f"/v1/organizations/{org}/members",
        headers=admin,
        json={"issuer": verifier.issuer, "subject": "synthetic-user"},
    ).json()["id"]
    assert (
        client.post(
            f"/v1/organizations/{org}/members/{member}/grants",
            headers=admin,
            json={"project_id": projects[0], "role": "viewer"},
        ).status_code
        == 200
    )
    user = {"Authorization": "Bearer " + token(role="admin", organization_id=other)}
    assert [row["id"] for row in client.get("/v1/projects", headers=user).json()] == projects[:1]
    assert not client.get("/v1/me", headers=user).json()["bootstrap"]
    assert client.get(f"/v1/projects/{projects[0]}/usage", headers=user).status_code == 200
    assert client.get(f"/v1/projects/{projects[1]}/usage", headers=user).status_code == 403
    assert client.get(f"/v1/projects/{projects[2]}/usage", headers=user).status_code == 403
    assert (
        client.post(
            f"/v1/projects/{projects[0]}/keys", headers=user, json={"role": "viewer"}
        ).status_code
        == 403
    )
    assert client.get(f"/v1/organizations/{org}/members", headers=user).status_code == 403
    assert (
        client.post(
            f"/v1/organizations/{org}/members/{member}/grants",
            headers=admin,
            json={"project_id": projects[2], "role": "project_admin"},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/v1/organizations/{org}/members/{member}", headers=admin).status_code == 200
    )
    assert client.get("/v1/projects", headers=user).status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"aud": "other"},
        {"iss": "https://other.example"},
        {"exp": 1},
        {"iat": 9999999999},
        {"sub": ""},
    ],
)
def test_invalid_oidc_claims_fail_closed(identity, change):
    verifier, token = identity
    with pytest.raises((ValueError, jwt.PyJWTError)):
        verifier.verify(token(**change))
    with pytest.raises((ValueError, jwt.PyJWTError)):
        verifier.verify(
            jwt.encode(
                {"sub": "synthetic"}, "synthetic-key-long-enough-for-hmac", algorithm="HS256"
            )
        )


@pytest.mark.postgres
def test_oidc_pkce_nonce_code_exchange_is_one_use_and_not_persisted(api, identity, monkeypatch):
    client, admin, app = api
    verifier, token = identity
    app.state.oidc_verifier = verifier
    for key, value in {
        "CUTBACK_OIDC_AUTHORIZATION_URL": "https://identity.example/authorize",
        "CUTBACK_OIDC_TOKEN_URL": "https://identity.example/token",
        "CUTBACK_PUBLIC_URL": "http://127.0.0.1:8080",
        "CUTBACK_OIDC_CLIENT_ID": "cutback-browser",
    }.items():
        monkeypatch.setenv(key, value)
    org = client.post("/v1/organizations", headers=admin, json={"name": "Synthetic SSO"}).json()[
        "id"
    ]
    client.post(
        f"/v1/organizations/{org}/members",
        headers=admin,
        json={"issuer": verifier.issuer, "subject": "synthetic-user", "role": "owner"},
    )
    proof = secrets.token_urlsafe(40)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(proof.encode()).digest()).decode().rstrip("=")
    )
    start = client.post("/v1/auth/oidc/start", json={"challenge": challenge})
    assert start.status_code == 200, start.text
    authorization = parse_qs(urlsplit(start.json()["authorization_url"]).query)
    assert authorization["code_challenge_method"] == ["S256"]
    nonce = authorization["nonce"][0]
    access = token()
    original = httpx.Client
    requests = []

    def provider(request):
        requests.append(request)
        assert parse_qs(request.content.decode())["code_verifier"] == [proof]
        return httpx.Response(
            200,
            json={"id_token": token(aud="cutback-browser", nonce=nonce), "access_token": access},
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(provider), **kw)
    )
    body = {"state": start.json()["state"], "verifier": proof, "code": "synthetic-code"}
    response = client.post("/v1/auth/oidc/exchange", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["access_token"] == access
    assert client.post("/v1/auth/oidc/exchange", json=body).status_code == 401
    assert len(requests) == 1
    from sqlalchemy import select

    from cutback.server.enterprise_db import OIDCChallenge

    with app.state.sessions() as session:
        persisted = [
            {c.name: str(getattr(row, c.name)) for c in OIDCChallenge.__table__.columns}
            for row in session.scalars(select(OIDCChallenge))
        ]
    assert access not in json.dumps(persisted) and proof not in json.dumps(persisted)
