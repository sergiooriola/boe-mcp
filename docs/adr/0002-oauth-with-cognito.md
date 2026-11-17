---
status: accepted
date: 2026-11-17
---

# Authorize with OAuth 2.1, Amazon Cognito as authorization server

## Context and Problem Statement

The BOE data is public, but the server is not: every request costs money, loads the BOE, and
the server is meant to show how agents reach corporate APIs safely. Who may call it, and how
does a client prove it?

The [MCP authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/index)
makes an HTTP server an OAuth 2.1 resource server. It must publish Protected Resource Metadata
(RFC 9728), accept only tokens issued for itself (audience, RFC 8707), and never pass a token
through to another service. Clients register in this order of preference: pre-registered
credentials, Client ID Metadata Documents, then Dynamic Client Registration (deprecated).

## Considered Options

* A static API key in a header
* An authorization server inside the MCP server (the SDK can host one)
* Amazon Cognito
* A third-party identity provider (Auth0, WorkOS…)

## Decision Outcome

Chosen option: "Amazon Cognito", on the Essentials plan with managed login.

* It runs in the same AWS account and region as the server, and its first 10,000 monthly
  active users are free.
* Its resource binding (RFC 8707) puts the server's URL in the token's `aud`, so the server can
  refuse tokens issued for anything else, as the specification requires.
* It handles passwords, MFA and token signing, which the MCP server then never touches.

A static key fails the specification and cannot be revoked per user. Hosting our own
authorization server means writing security-critical code that Cognito already provides. A
third-party provider adds a vendor and an account outside AWS.

Each client is **pre-registered** as a Cognito app client: Claude Code as a public client
(PKCE, fixed `localhost` callback port, because Cognito matches callback URLs exactly) and
Claude.ai as a confidential client with a secret. Cognito's Dynamic Client Registration is
days old (November 2026) and deprecated by the specification, and Cognito does not support
Client ID Metadata Documents.

The server verifies each token itself: RS256 signature against the pool's JWKS, issuer, expiry,
`aud` equal to its own URL, `token_use` = `access`, `client_id` in the allowlist, and the
`read` scope.

### Consequences

* Good, because users sign in on Cognito's pages with password and authenticator app, and
  self sign-up is off: only an administrator creates users.
* Good, because the token check is a few dozen lines with PyJWT, fully tested with signed test
  tokens.
* Bad, because each new client needs a Cognito app client and a redeploy of the allowlist.
* Bad, because Cognito's discovery document lives under the issuer path
  (`<issuer>/.well-known/openid-configuration`) and it has no RFC 8414 endpoint. The 2026-07-28
  specification requires clients to try that path, but whether every client does has to be
  confirmed against each one.

### Confirmation

`tests/test_http_app.py` refuses tokens with another audience, another issuer, an expired one,
an ID token, an unknown client, a wrong signature and an HS256 token, and answers 403 without
the scope.
