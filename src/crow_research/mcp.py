"""The tool supply a child agent gets.

A v2 agent owns no tools. The client hands them over at ``session/new`` as
``mcp_servers``, and an empty list is an agent that can only talk — which makes
the supply this orchestrator's problem, because it is a client to every agent it
runs.

The default is crow's own, read from ``~/.agents/crow/config.yaml``: the same
``mcpServers`` map the TUI reads. Reading it rather than hardcoding
``crow-cli mcp2`` matters — that config is where this machine's paths, its
transport choice and its API keys already live, and a second copy of the answer
is a second thing to drift.

Config keys a server by name and spells its transport ``transport:``; the wire
carries a list with the name *inside* the entry and ``type:`` instead. That
translation is the whole of this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from acp.experimental import v2

from crow_cli.config import Config


def stdio_server(
    name: str, command: str, *args: str, env: Optional[dict[str, str]] = None
) -> v2.schema.StdioMcpServer:
    """One stdio MCP server, as the wire model ``session/new`` carries."""
    return v2.schema.StdioMcpServer(
        name=name,
        command=command,
        args=list(args),
        env=[v2.schema.EnvVariable(name=k, value=str(val)) for k, val in (env or {}).items()],
    )


def http_server(
    name: str, url: str, headers: Optional[dict[str, str]] = None
) -> v2.schema.HttpMcpServer:
    """One streamable-HTTP MCP server. v2 has no SSE variant: an SSE endpoint is
    an HTTP endpoint, so ``transport: sse`` in an old config lands here too."""
    return v2.schema.HttpMcpServer(
        name=name,
        url=url,
        headers=[v2.schema.HttpHeader(name=k, value=str(v)) for k, v in (headers or {}).items()],
    )


def from_map(servers: Optional[dict[str, Any]]) -> list[Any]:
    """A config ``mcpServers`` map -> the wire list.

    A malformed entry raises instead of being skipped. Skipping is the failure
    v1 taught: the child comes up toolless, works anyway for a while, and
    nothing anywhere says why it could not read a file.
    """
    out: list[Any] = []
    for name, entry in (servers or {}).items():
        if not isinstance(entry, dict):
            raise ValueError(f"mcpServers.{name!r} must be a mapping, not {type(entry).__name__}")
        kind = entry.get("transport") or entry.get("type") or ("http" if entry.get("url") else "stdio")
        if kind == "stdio":
            if not entry.get("command"):
                raise ValueError(f"mcpServers.{name!r}: a stdio server needs a command")
            out.append(
                stdio_server(
                    name,
                    str(entry["command"]),
                    *[str(a) for a in (entry.get("args") or ())],
                    env=entry.get("env"),
                )
            )
        elif kind in ("http", "sse"):
            if not entry.get("url"):
                raise ValueError(f"mcpServers.{name!r}: an http server needs a url")
            out.append(http_server(name, str(entry["url"]), headers=entry.get("headers")))
        else:
            raise ValueError(f"mcpServers.{name!r}: unknown transport {kind!r}")
    return out


def from_config(config_dir: Optional[Path] = None) -> list[Any]:
    """crow's configured tool supply, ready to hand to ``session/new``."""
    return from_map(Config.load(config_dir).mcp_servers)
