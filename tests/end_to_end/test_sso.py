"""Actual browser -> local OIDC fixture -> PKCE/token endpoint -> RSA/JWKS -> project RBAC."""

import base64
import hashlib
import html
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from support_browser import browser_command
from test_pipeline import server

from cutback.server.api import create_app


@pytest.mark.postgres
@pytest.mark.e2e
def test_browser_sso_pkce_rsa_and_memory_only_credentials(postgres_url, monkeypatch):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true with the built React dashboard and Chromium")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="owned-synthetic-key", use="sig", alg="RS256")
    transactions = {}
    identity = FastAPI()

    @identity.get("/jwks")
    def jwks():
        return {"keys": [jwk]}

    @identity.get("/authorize")
    def authorize(request: Request):
        params = dict(request.query_params)
        assert params["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(32)
        transactions[code] = params
        response = HTMLResponse(
            '<!doctype html><html lang="en"><title>Synthetic identity</title>'
            '<form method="get" action="'
            + html.escape(params["redirect_uri"], quote=True)
            + '"><input type="hidden" name="code" value="'
            + code
            + '"><input type="hidden" name="state" value="'
            + html.escape(params["state"], quote=True)
            + '"><button>Continue synthetic identity login</button></form></html>'
        )
        # Force an opener-isolating cross-origin identity response; BroadcastChannel
        # must deliver the callback without persisting a PKCE verifier or token.
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        return response

    @identity.post("/token")
    async def token(request: Request):
        params = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        transaction = transactions.pop(params["code"])
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(params["code_verifier"].encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert challenge == transaction["code_challenge"]
        assert params["redirect_uri"] == transaction["redirect_uri"]
        claims = {
            "iss": issuer,
            "sub": "owned-browser-member",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        }
        return {
            "access_token": jwt.encode(
                {**claims, "aud": "cutback-api"},
                key,
                algorithm="RS256",
                headers={"kid": jwk["kid"]},
            ),
            "id_token": jwt.encode(
                {**claims, "aud": "cutback-browser", "nonce": transaction["nonce"]},
                key,
                algorithm="RS256",
                headers={"kid": jwk["kid"]},
            ),
        }

    with server(identity) as issuer:
        monkeypatch.setenv("CUTBACK_OIDC_ISSUER", issuer)
        monkeypatch.setenv("CUTBACK_OIDC_AUDIENCE", "cutback-api")
        monkeypatch.setenv("CUTBACK_OIDC_JWKS_URL", issuer + "/jwks")
        monkeypatch.setenv("CUTBACK_OIDC_CLIENT_ID", "cutback-browser")
        monkeypatch.setenv("CUTBACK_OIDC_AUTHORIZATION_URL", issuer + "/authorize")
        monkeypatch.setenv("CUTBACK_OIDC_TOKEN_URL", issuer + "/token")
        admin = secrets.token_urlsafe(48)
        app = create_app(database_url=postgres_url, admin_token=admin, allow_insecure_http=True)
        with server(app) as endpoint, httpx.Client(base_url=endpoint, trust_env=False) as control:
            monkeypatch.setenv("CUTBACK_PUBLIC_URL", endpoint)
            headers = {"Authorization": "Bearer " + admin}
            org = control.post(
                "/v1/organizations", headers=headers, json={"name": "Synthetic browser identity"}
            ).json()["id"]
            project = control.post(
                "/v1/projects",
                headers=headers,
                json={"slug": "sso-browser", "organization_id": org},
            ).json()["id"]
            member = control.post(
                f"/v1/organizations/{org}/members",
                headers=headers,
                json={"issuer": issuer, "subject": "owned-browser-member"},
            ).json()["id"]
            control.post(
                f"/v1/organizations/{org}/members/{member}/grants",
                headers=headers,
                json={"project_id": project, "role": "viewer"},
            ).raise_for_status()
            result = subprocess.run(
                browser_command(Path(__file__).resolve().parents[2] / "web", "tests/sso.spec.ts"),
                cwd=Path(__file__).resolve().parents[2] / "web",
                env={**os.environ, "CUTBACK_UI_URL": endpoint, "CUTBACK_UI_SSO_TEST": "true"},
                capture_output=True,
                text=True,
                timeout=90,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert not transactions
