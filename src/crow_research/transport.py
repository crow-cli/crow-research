"""How to reach a downstream agent: stdio argv, WebSocket URL, or HTTP URL.

One seam, three shapes. Everything above this module holds an :class:`Endpoint`
and never learns which transport it got, which is what lets the same workflow
drive a child it spawned (``crow-cli acp2`` over stdio) and a long-lived agent
somebody else is already serving (``ws://127.0.0.1:2771/acp``) without a branch
in the orchestration code.

``open_endpoint`` yields a :class:`Link`: the positional arguments
``v2.connect_to_agent`` wants, plus the process handle when there is one, plus
the teardown. The SDK's own convention is the surprising part and is preserved
here so callers never see it — for stdio the *input* stream is the writer and
the *output* stream is the reader.
"""

from __future__ import annotations

import inspect
import os
import shlex
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from acp.http.client import create_http_stream
from acp.stdio import spawn_stdio_transport
from acp.ws.client import create_websocket_stream


@dataclass(frozen=True)
class Stdio:
    """A command to spawn. ``env`` is merged OVER the inherited environment."""

    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None

    def __str__(self) -> str:
        return " ".join(shlex.quote(a) for a in self.argv)


@dataclass(frozen=True)
class Ws:
    """A WebSocket endpoint somebody is already serving, e.g. ``acp2 --http``."""

    url: str

    def __str__(self) -> str:
        return self.url


@dataclass(frozen=True)
class Http:
    """A Streamable HTTP endpoint (SSE downstream)."""

    url: str

    def __str__(self) -> str:
        return self.url


Endpoint = Stdio | Ws | Http


def parse_endpoint(text: str, *, cwd: Optional[str] = None) -> Endpoint:
    """``ws://…`` / ``wss://…`` -> Ws, ``http://…`` / ``https://…`` -> Http,
    anything else -> a shell-quoted argv to spawn.

    The order matters: a URL is recognized by its scheme, so an agent command
    may be any argv at all, including one whose first token is a path.
    """
    stripped = text.strip()
    if stripped.startswith(("ws://", "wss://")):
        return Ws(stripped)
    if stripped.startswith(("http://", "https://")):
        return Http(stripped)
    argv = tuple(shlex.split(stripped))
    if not argv:
        raise ValueError(f"empty agent endpoint: {text!r}")
    return Stdio(argv, cwd=cwd)


@dataclass
class Link:
    """An opened transport, ready to hand to ``v2.connect_to_agent``.

    ``args`` is a tuple to splat: ``(writer, reader)`` for stdio, ``(transport,)``
    for a socket transport. ``proc`` is the child process when we spawned one.
    """

    args: tuple[Any, ...]
    proc: Optional[Any] = None
    _closers: list[Any] = field(default_factory=list)

    async def close(self) -> None:
        while self._closers:
            closer = self._closers.pop()
            result = closer()
            if inspect.isawaitable(result):
                await result


@asynccontextmanager
async def open_endpoint(
    endpoint: Endpoint, *, env: Optional[dict[str, str]] = None
) -> AsyncIterator[Link]:
    """Open the transport and tear it down again.

    A spawned child inherits this process's whole environment with ``env``
    merged over it. Inheriting is deliberate: the trim ``spawn_stdio_transport``
    applies by default exists for other people's MCP servers, and silently
    drops shell-exported API keys from an agent we are about to ask for work.
    """
    link: Link
    if isinstance(endpoint, Stdio):
        stack = spawn_stdio_transport(
            *endpoint.argv,
            env={**os.environ, **endpoint.env, **(env or {})},
            cwd=endpoint.cwd,
        )
        reader, writer, proc = await stack.__aenter__()
        link = Link(args=(writer, reader), proc=proc)
        link._closers.append(lambda: stack.__aexit__(None, None, None))
    else:
        factory = create_websocket_stream if isinstance(endpoint, Ws) else create_http_stream
        transport = factory(endpoint.url)
        if inspect.isawaitable(transport):
            transport = await transport
        link = Link(args=(transport,))
        link._closers.append(transport.close)
    try:
        yield link
    finally:
        await link.close()
