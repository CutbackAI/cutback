# Identity and teams

Self-hosting continues to support scoped project keys and an installation bootstrap token. Optional OIDC uses the identity provider configured by the customer; no Cutback identity account is required. Install `[identity]` for JWT verification.

Configure `CUTBACK_OIDC_ISSUER`, `CUTBACK_OIDC_AUDIENCE` and `CUTBACK_OIDC_JWKS_URL`. Tokens require matching issuer/audience, signature, expiry, issued-at and subject. Only RS256/ES256 and a matching named public key are accepted. JWKS reads are bounded, use TLS outside loopback, have no redirects/proxy inheritance, and fail closed after cache expiry. Unverified roles or organization claims never grant privileges.

An installation administrator creates organizations and registers the identity issuer/subject through the organization API. The database retains a subject hash, not the original subject or email. Organization owners/admins administer their projects; ordinary members need explicit project viewer/analyst/editor/admin grants. Disabling membership takes effect on the next authenticated request. The final enabled owner cannot be removed through the API. The bootstrap token remains a separately protected recovery capability.

For browser sign-in, additionally configure `CUTBACK_OIDC_CLIENT_ID`, `CUTBACK_OIDC_AUTHORIZATION_URL`, `CUTBACK_OIDC_TOKEN_URL` and `CUTBACK_PUBLIC_URL`; an optional `CUTBACK_OIDC_CLIENT_SECRET` stays in server secrets. Register exactly `<public URL>/dashboard/` at the identity provider and permit the API resource audience. The provider must issue a signed resource JWT with the configured API audience and an ID token for the browser client. Opaque access tokens/introspection and provider-specific token claim rewrites are not silently accepted.

The authorization-code flow uses S256 PKCE, one-use five-minute state and nonce checks. The parent browser retains its verifier in memory; no bearer/refresh token is stored in cookies, localStorage, sessionStorage or the database. The server exchanges the code and validates both tokens/nonce/subject. Only the access token is returned to the authorized browser session. Closing/reloading that session clears it. The callback carries an authorization code, never a bearer token, and removes callback parameters promptly.

Organization/team APIs are under `/v1/organizations`; `/v1/me` describes the authenticated principal and project roles. Project settings expose bounded retention under `/v1/projects/{id}/settings`. Audit records capture administrative decisions without tokens or original identity subjects.

Local acceptance uses real RSA-signed tokens, synthetic JWKS/token transports and real PostgreSQL. Cross-project access, forged role claims, issuer/audience/time errors, disabled memberships, PKCE/nonce checks and replay denial are tested. A customer's actual identity-provider/client registration and browser policy still need environment-specific acceptance before launch.
