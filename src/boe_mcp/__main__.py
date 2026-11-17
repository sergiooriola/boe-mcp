"""Command line: `boe-mcp stdio` for a local client, `boe-mcp http` for the hosted server."""

import argparse
import logging
import os

import uvicorn

from boe_mcp.http_app import HttpSettings, create_app
from boe_mcp.server import create_server


def main(argv: list[str] | None = None) -> None:
    """Run the server over the transport named on the command line."""
    parser = argparse.ArgumentParser(prog="boe-mcp", description=__doc__)
    parser.add_argument(
        "transport",
        choices=["stdio", "http"],
        nargs="?",
        default="stdio",
        help="stdio: local, no login. http: Streamable HTTP with OAuth, configured by "
        "BOE_MCP_* environment variables.",
    )
    args = parser.parse_args(argv)
    _configure_logging()
    if args.transport == "stdio":
        create_server().run("stdio")
        return
    app = create_app(HttpSettings.from_env())
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        log_config=None,
        proxy_headers=True,
    )


def _configure_logging() -> None:
    # Logs go to stderr, which stdio leaves free and Lambda sends to CloudWatch. The BOE client
    # keeps httpx quiet, because its request lines carry search words.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


if __name__ == "__main__":
    main()
