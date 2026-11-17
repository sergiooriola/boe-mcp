---
status: accepted
date: 2026-11-17
---

# Serve MCP over stateless Streamable HTTP with JSON responses

## Context and Problem Statement

The server must be reachable by Claude.ai, which connects from Anthropic's cloud, and by Claude
Code on a laptop. It runs on AWS Lambda, where any instance may serve any request and none
lives long. Which MCP transport, and in which mode?

The current specification, [2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/changelog),
removed the session (`Mcp-Session-Id`) and the `initialize` handshake: every request carries its
protocol version and client capabilities. Clients that still speak 2025-11-25 or older do
the handshake. Claude Code's newer runtime speaks 2026-07-28 and falls back for older servers.

## Considered Options

* stdio only
* Streamable HTTP with sessions and Server-Sent Events
* Streamable HTTP, stateless, with one JSON answer per request

## Decision Outcome

Chosen option: "Streamable HTTP, stateless, with JSON answers". It is the only remote transport
in the specification. Without session state any Lambda instance can serve any request, and
the four tools answer in well under a second, so streaming would add nothing.

The official Python SDK (`mcp` 2.3.0) serves 2026-07-28 and the older handshake revisions from
the same endpoint. `stateless_http=True` makes legacy clients stateless too, and
`json_response=True` answers with `application/json` instead of an event stream. stdio stays
available for local use (`boe-mcp stdio`), without login, because the trust boundary is the
user's own computer.

### Consequences

* Good, because nothing has to be shared between Lambda instances: no database, no sticky
  sessions.
* Good, because older and newer clients both work, which the tests check with a legacy
  `initialize` and with the SDK's own client.
* Bad, because the server cannot push notifications or stream progress. None of the tools
  needs to.
* No stream may stay open, because behind the Lambda Web Adapter an open response keeps the
  function running until its timeout. The server answers `GET` with 405, and
  `MCPServer(subscriptions=False)` leaves `subscriptions/listen` unserved, so it advertises no
  `listChanged`. The first deploy showed this was needed: without it, every Claude Code
  connection waited 25 seconds.
* The SDK only accepts requests whose `Host` header is in an allowlist (DNS rebinding
  protection), so the public host name is part of the configuration.

### Confirmation

`tests/test_http_app.py` runs a full MCP session over HTTP with a signed token and a legacy
`initialize` request. It also checks that neither a `GET` stream nor a listen stream can be
opened.
