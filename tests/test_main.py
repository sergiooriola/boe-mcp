import logging
from typing import Any

import pytest

from boe_mcp import __main__ as cli

ENV = {
    "BOE_MCP_RESOURCE_URL": "https://abc.execute-api.eu-south-2.amazonaws.com/mcp",
    "BOE_MCP_ISSUER_URL": "https://cognito-idp.eu-south-2.amazonaws.com/eu-south-2_TEST",
    "BOE_MCP_CLIENT_IDS": "client",
    "BOE_MCP_REQUIRED_SCOPE": "scope",
    "PORT": "9000",
}


def test_stdio_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    transports: list[str] = []
    monkeypatch.setattr(
        "mcp.server.MCPServer.run", lambda _, transport: transports.append(transport)
    )

    cli.main([])

    assert transports == ["stdio"]
    assert logging.getLogger("httpx").level == logging.WARNING


def test_http_serves_the_app_with_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **options: calls.append(options))

    cli.main(["http"])

    assert calls == [{"host": "127.0.0.1", "port": 9000, "log_config": None, "proxy_headers": True}]
