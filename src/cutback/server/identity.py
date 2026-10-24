"""Customer-configured OIDC resource-token validation and explicit database membership mapping."""

import base64
import hashlib
import os
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from cutback.core.config import validate_endpoint
from cutback.server.db import Project
from cutback.server.enterprise_db import OIDCChallenge, OrganizationMember, ProjectGrant


def subject_digest(issuer, subject):
    return hashlib.sha256((issuer + "\x00" + subject).encode()).hexdigest()


class OIDCVerifier:
    def __init__(self, issuer, audience, jwks_url, *, client_id=None):
        import jwt

        self.issuer = issuer
        validate_endpoint(issuer)
        self.audience = audience
        self.jwks_url = validate_endpoint(jwks_url)
        self.client_id = client_id
        self.jwt = jwt
        self.keys = {}
        self.expires = 0.0
        self.last_refresh = float("-inf")
        self.lock = threading.Lock()

    def refresh(self):
        import httpx

        with self.lock:
            if time.monotonic() - self.last_refresh < 5:
                return
            self.last_refresh = time.monotonic()
            with httpx.Client(timeout=3, follow_redirects=False, trust_env=False) as client:
                with client.stream("GET", self.jwks_url) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        if len(data) + len(chunk) > 256000:
                            raise ValueError("JWKS_TOO_LARGE")
                        data.extend(chunk)
            import json

            keys = json.loads(data)["keys"]
            if not isinstance(keys, list) or len(keys) > 100:
                raise ValueError("INVALID_JWKS")
            resolved = {}
            for item in keys:
                if item.get("kty") in {"RSA", "EC"} and item.get("use", "sig") == "sig":
                    key = self.jwt.PyJWK.from_dict(item)
                    if key.algorithm_name not in {"RS256", "ES256"}:
                        continue
                    if not key.key_id or key.key_id in resolved:
                        raise ValueError("AMBIGUOUS_JWKS")
                    resolved[key.key_id] = key
            self.keys, self.expires = resolved, time.monotonic() + 300

    def verify(self, token, *, audience=None):
        if not isinstance(token, str) or len(token) > 8192:
            raise ValueError("INVALID_OIDC_TOKEN")
        header = self.jwt.get_unverified_header(token)
        if header.get("alg") not in {"RS256", "ES256"} or not isinstance(header.get("kid"), str):
            raise ValueError("INVALID_OIDC_ALGORITHM")
        if time.monotonic() >= self.expires or header["kid"] not in self.keys:
            self.refresh()
        if time.monotonic() >= self.expires:
            raise ValueError("OIDC_KEYS_EXPIRED")
        key = self.keys.get(header["kid"])
        if not key or key.algorithm_name != header["alg"]:
            raise ValueError("UNKNOWN_OIDC_KEY")
        claims = self.jwt.decode(
            token,
            key.key,
            algorithms=[key.algorithm_name],
            issuer=self.issuer,
            audience=audience or self.audience,
            leeway=15,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 200:
            raise ValueError("INVALID_OIDC_SUBJECT")
        if claims["exp"] - claims["iat"] > 86400:
            raise ValueError("OIDC_TOKEN_LIFETIME_EXCEEDS_LIMIT")
        return claims

    def principal(self, token, sessions):
        claims = self.verify(token)
        hashed = subject_digest(self.issuer, claims["sub"])
        with sessions() as session:
            members = session.scalars(
                select(OrganizationMember).where(
                    OrganizationMember.issuer == self.issuer,
                    OrganizationMember.subject_hash == hashed,
                    OrganizationMember.enabled.is_(True),
                )
            ).all()
            if not members:
                raise ValueError("OIDC_MEMBERSHIP_REQUIRED")
            roles, organizations = {}, {}
            for member in members:
                organizations[member.organization_id] = member.role
                if member.role in {"owner", "admin"}:
                    for project in session.scalars(
                        select(Project).where(Project.organization_id == member.organization_id)
                    ):
                        roles[project.id] = "project_admin"
                else:
                    for grant in session.scalars(
                        select(ProjectGrant).where(ProjectGrant.member_id == member.id)
                    ):
                        project = session.get(Project, grant.project_id)
                        if project and project.organization_id == member.organization_id:
                            roles[project.id] = grant.role
        return {
            "role": "oidc",
            "actor": "oidc:" + hashed[:32],
            "subject_hash": hashed,
            "project_roles": roles,
            "organizations": organizations,
        }


def configured_verifier():
    values = [
        os.getenv(name)
        for name in ("CUTBACK_OIDC_ISSUER", "CUTBACK_OIDC_AUDIENCE", "CUTBACK_OIDC_JWKS_URL")
    ]
    if not any(values):
        return None
    if not all(values):
        raise ValueError("OIDC_CONFIGURATION_INCOMPLETE")
    return OIDCVerifier(*values, client_id=os.getenv("CUTBACK_OIDC_CLIENT_ID"))


def login_configuration(verifier):
    if verifier is None:
        return {"enabled": False, "browser_login": False}
    return {
        "enabled": True,
        "issuer": verifier.issuer,
        "browser_login": all(
            os.getenv(key)
            for key in (
                "CUTBACK_OIDC_AUTHORIZATION_URL",
                "CUTBACK_OIDC_TOKEN_URL",
                "CUTBACK_PUBLIC_URL",
                "CUTBACK_OIDC_CLIENT_ID",
            )
        ),
    }


def start_login(verifier, sessions, challenge):
    if not login_configuration(verifier)["browser_login"]:
        raise HTTPException(503, "OIDC_BROWSER_LOGIN_NOT_CONFIGURED")
    public = validate_endpoint(os.environ["CUTBACK_PUBLIC_URL"])
    endpoint = validate_endpoint(os.environ["CUTBACK_OIDC_AUTHORIZATION_URL"])
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.execute(delete(OIDCChallenge).where(OIDCChallenge.expires_at < now))
        if session.scalar(select(func.count()).select_from(OIDCChallenge)) >= 10000:
            raise HTTPException(429, "LOGIN_CAPACITY_REACHED")
        session.add(
            OIDCChallenge(
                state_hash=hashlib.sha256(state.encode()).hexdigest(),
                nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
                pkce_challenge=challenge,
                expires_at=now + timedelta(minutes=5),
            )
        )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": verifier.client_id,
            "redirect_uri": public + "/dashboard/",
            "scope": "openid",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return {"authorization_url": endpoint + "?" + query, "state": state}


def exchange_login(verifier, sessions, body):
    import httpx

    if not login_configuration(verifier)["browser_login"]:
        raise HTTPException(503, "OIDC_BROWSER_LOGIN_NOT_CONFIGURED")
    hashed = hashlib.sha256(body.state.encode()).hexdigest()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(body.verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    with sessions.begin() as session:
        record = session.get(OIDCChallenge, hashed, with_for_update=True)
        if (
            not record
            or record.used
            or record.expires_at < datetime.now(UTC)
            or record.pkce_challenge != challenge
        ):
            raise HTTPException(401, "OIDC_STATE_OR_PKCE_INVALID")
        nonce_hash = record.nonce_hash
        record.used = True  # Failed/uncertain exchanges cannot replay a one-use code.
    data = {
        "grant_type": "authorization_code",
        "code": body.code,
        "client_id": verifier.client_id,
        "redirect_uri": validate_endpoint(os.environ["CUTBACK_PUBLIC_URL"]) + "/dashboard/",
        "code_verifier": body.verifier,
    }
    if os.getenv("CUTBACK_OIDC_CLIENT_SECRET"):
        data["client_secret"] = os.environ["CUTBACK_OIDC_CLIENT_SECRET"]
    try:
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            with client.stream(
                "POST", validate_endpoint(os.environ["CUTBACK_OIDC_TOKEN_URL"]), data=data
            ) as response:
                response.raise_for_status()
                import json

                raw = bytearray()
                for chunk in response.iter_bytes():
                    if len(raw) + len(chunk) > 64000:
                        raise ValueError("OIDC_RESPONSE_TOO_LARGE")
                    raw.extend(chunk)
                tokens = json.loads(raw)
        identity = verifier.verify(tokens["id_token"], audience=verifier.client_id)
        if hashlib.sha256(identity.get("nonce", "").encode()).hexdigest() != nonce_hash:
            raise ValueError("OIDC_NONCE_INVALID")
        access = verifier.verify(tokens["access_token"])
        if access["sub"] != identity["sub"]:
            raise ValueError("OIDC_SUBJECT_MISMATCH")
        verifier.principal(tokens["access_token"], sessions)
        return {
            "access_token": tokens["access_token"],
            "expires_at": access["exp"],
            "storage": "memory_only",
        }
    except Exception:
        raise HTTPException(401, "OIDC_EXCHANGE_FAILED") from None
