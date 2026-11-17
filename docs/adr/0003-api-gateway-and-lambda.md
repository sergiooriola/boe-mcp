---
status: accepted
date: 2026-11-17
---

# Host on AWS Lambda behind an API Gateway HTTP API

## Context and Problem Statement

The server answers a few hundred requests a month and should cost close to nothing while idle.
It must have a public HTTPS URL that is known before the function is created, because that URL
is also the OAuth resource the tokens are bound to (ADR 0002) and the function needs it in its
configuration.

## Considered Options

* Lambda with a function URL
* Lambda behind an API Gateway HTTP API
* Amazon ECS on Fargate
* Amazon Bedrock AgentCore Runtime

## Decision Outcome

Chosen option: "Lambda behind an API Gateway HTTP API".

* Pay per request, nothing while idle: at this volume the cost is cents a month (README).
* The API's URL exists before the function, so Terraform can give the function its own public
  URL with no dependency cycle. A function URL only exists after the function, and the Cognito
  app clients the function must know about depend on that URL.
* The stage throttles requests (5 per second, bursts of 10) before they reach the function,
  which caps the bill and the load on the BOE.
* The Lambda Web Adapter layer runs the unchanged ASGI app (uvicorn), so the same code runs
  locally, in tests and in Lambda, with no Lambda-specific handler.

Fargate bills by the hour while idle. AgentCore Runtime is AWS's managed host for agents and
MCP servers and supports OAuth discovery, but it bills per session and I found no report of a
Claude.ai connector working end to end with it; it is the option to try for a larger
deployment.

### Consequences

* Good, because the function has no AWS permission besides writing its own logs.
* Bad, because API Gateway's 30-second limit bounds every request; the function stops at 25
  seconds and the BOE client at 15, so a slow BOE becomes an error the model can read.
* Bad, because a cold start adds about two seconds (1.6 to 2.7 measured) to the first request
  after a quiet period.
