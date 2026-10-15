"""The REPL client, on ACP v2.

A port of ``crow-cli/sandbox/repl-agent/client.py`` — same shape, same API, same
rich rendering — with the three things v2 changes:

* **the connection**: ``v2.connect_to_agent`` over ``spawn_stdio_transport``,
  because ``acp.stdio.spawn_agent_process`` builds a v1 ``ClientSideConnection``;
* **the turn**: v1's ``session/prompt`` returned when the agent was done, so
  ``send`` could await the response and then poke the dispatcher for stragglers.
  v2's ``session/prompt`` is an *ack* — it returns a ``message_id`` immediately
  and the turn ends later, with an ``idle`` ``state_update``. So ``send`` awaits
  that instead, which is both correct and private-attribute-free;
* **the handlers**: expanded snake_case, with inbound ``_meta`` spread into
  ``**kwargs``. That last part is what makes a pipeline legible: every relayed
  update carries ``crow/agent``, so the client can print *which* agent is
  speaking rather than one undifferentiated stream.

Usage::

    client = ReplClient(sys.executable, "agents/orchestrator_agent.py", cwd=".")
    await client.send("fix the parser")         # the prompt goes to the workflow
    print(client.conversation[client.session_id])
    await client.close()

Or just run it: ``python -m crow_research.repl_client``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from acp.experimental import v2
from acp.stdio import spawn_stdio_transport
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .mcp import from_config
from .transport import Stdio, open_endpoint

#: The pipeline agent this client drives when no command is given.
DEFAULT_AGENT = Path(__file__).resolve().parents[2] / "agents" / "orchestrator_agent.py"

#: ``_meta`` key the orchestrator stamps on a relayed update.
META_AGENT = "crow/agent"

#: How many tool calls' render state to keep. A pipeline runs in perpetuity, so
#: this dict would otherwise grow for as long as the run does; the oldest ids go
#: first, and losing one only means a late patch for it renders as nothing.
TOOL_MEMORY = 512


def stdio_mcp(name: str, command: str, *args: str, env: Optional[dict[str, str]] = None) -> Any:
    """A v2 ``StdioMcpServer``, ready for ``ReplClient(mcp_servers=[...])``."""
    return v2.schema.StdioMcpServer(
        name=name,
        command=command,
        args=list(args),
        env=[v2.schema.EnvVariable(name=k, value=str(v)) for k, v in (env or {}).items()],
    )


class ReplClient:
    """A v2 client you specify the agent command for.

    The agent on the other end may be one agent or a pipeline of them; this
    client does not care and does not choose. It sends a prompt to the workflow
    and renders whatever comes back, tagged with whoever produced it.
    """

    def __init__(
        self,
        agent_command: str,
        *agent_args: str,
        console: Optional[Console] = None,
        cwd: Optional[str | Path] = None,
        env: Optional[Mapping[str, str]] = None,
        mcp_servers: Optional[list] = None,
        quiet: bool = False,
    ) -> None:
        self.agent_command = agent_command
        self.agent_args = agent_args
        self.cwd = str(cwd) if cwd else None
        self.env = dict(env) if env else None
        #: ``None`` means crow's own configured supply, resolved at start: a v2
        #: agent has no tools until its client hands some over.
        self.mcp_servers = mcp_servers
        self.console = console or Console(quiet=quiet)
        #: ``{session_id: [{"role": ..., "content": ..., "agent": ...}]}``
        self.conversation: dict[str, list[dict[str, str]]] = {}

        self._conn: Optional[Any] = None
        self._stack: Optional[Any] = None
        self._session_id: Optional[str] = None
        self._started = False
        self._idle: Optional[asyncio.Event] = None
        self._stop_reason: Optional[str] = None
        self._agent: Optional[str] = None
        #: ``{tool_call_id: (title, status)}`` — see :meth:`_tool_call`.
        self._tools: dict[str, tuple[str, Optional[str]]] = {}
        self._response_buffer = ""
        self._thinking_buffer = ""
        self.stderr: list[str] = []
        self._drainer: Optional[asyncio.Task] = None

    # -- the client side of the wire ------------------------------------

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        """One update from the agent — or, through the orchestrator's relay,
        from whichever agent in the pipeline is speaking. ``kwargs`` is the
        inbound ``_meta``, which is how we know which one that is."""
        tag = kwargs.get(META_AGENT) or {}
        who = tag.get("title") or tag.get("role")
        # Only a *named* speaker changes the rule. The orchestrator's own
        # notices are untagged, and letting them reset `_agent` printed a bare
        # `Agent` rule between every pair of roles — one per notice, which is
        # most of the screen. They are visually distinct already, and leaving
        # `_agent` alone keeps the buffered response attributed to whoever
        # actually said it.
        if who is not None and who != self._agent:
            self._agent = who
            self._rule(who)
        kind = getattr(update, "session_update", None)
        if kind == "agent_message_chunk":
            text = _text_of(getattr(update, "content", None))
            self._response_buffer += text
            # Text, not a markup string: an agent writing `[x]`, `[link](url)`
            # or `[in_progress]` is writing prose, and rich would eat it.
            self.console.print(Text(text, style="purple"), end="")
        elif kind == "agent_thought_chunk":
            self._thinking_buffer += _text_of(getattr(update, "content", None))
        elif kind in ("tool_call", "tool_call_update"):
            self._tool_call(update)
        elif kind == "notice":
            severity = getattr(update, "severity", "info")
            style = {"error": "bold red", "warning": "yellow"}.get(severity, "bold blue")
            description = getattr(update, "description", None)
            body = f"\n⚠ {update.title}" + (f" — {description}" if description else "")
            self.console.print(Text(body, style=style))
        elif kind == "state_update":
            state = getattr(update, "state", None)
            if state == "idle":
                self._stop_reason = getattr(update, "stop_reason", None)
                if self._idle is not None:
                    self._idle.set()
        elif kind in ("user_message", "user_message_chunk"):
            pass  # our own prompt, echoed: the panel already showed it

    async def request_permission(self, session_id: str, title: str, options: list[Any], **kwargs: Any) -> Any:
        allowed = next(
            (o for o in options if getattr(o, "kind", None) in ("allow_once", "allow_always")),
            options[0] if options else None,
        )
        if allowed is None:
            return v2.schema.RequestPermissionResponse(outcome=v2.schema.CancelledPermissionOutcome())
        return v2.schema.RequestPermissionResponse(
            outcome=v2.schema.SelectedPermissionOutcomeVariant(option_id=allowed.option_id)
        )

    async def create_elicitation(self, message: str, mode: str, **kwargs: Any) -> Any:
        return v2.schema.CancelElicitationResponse()

    # -- lifecycle ------------------------------------------------------

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    async def start(self) -> str:
        """Spawn the agent, handshake, open a session."""
        endpoint = Stdio((self.agent_command, *self.agent_args), env=dict(self.env or {}), cwd=self.cwd)
        self.console.print(
            Panel(f"[bold]{self.agent_command}[/bold] {' '.join(self.agent_args)}",
                  title="[yellow]Agent[/yellow]", border_style="yellow")
        )
        self._stack = open_endpoint(endpoint)
        link = await self._stack.__aenter__()
        self._conn = v2.connect_to_agent(self, *link.args)
        if link.proc is not None:
            self._drainer = asyncio.create_task(self._drain(link.proc))
        info = await self._conn.initialize(
            protocol_version=v2.PROTOCOL_VERSION,
            info=v2.schema.Implementation(name="crow-research-repl", title="Crow Research REPL", version="0.1.0"),
        )
        supply = self.mcp_servers if self.mcp_servers is not None else from_config()
        response = await self._conn.new_session(cwd=self.cwd or str(Path.cwd()), mcp_servers=supply)
        self._session_id = response.session_id
        self.conversation[self._session_id] = []
        self._started = True
        agent = getattr(info, "info", None)
        name = f"{agent.name} {agent.version}" if agent is not None else "agent"
        self.console.print(f"[green]Session: {self._session_id}[/green] [dim]({name}, protocol {getattr(info, 'protocol_version', '?')})[/dim]")
        return self._session_id

    async def _drain(self, proc: Any) -> None:
        """Keep the child's stderr pipe empty, or it fills and the child blocks."""
        stream = getattr(proc, "stderr", None)
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            self.stderr.append(line.decode(errors="replace"))
            del self.stderr[:-200]

    async def send(self, prompt_text: str, *, timeout: Optional[float] = None) -> Optional[str]:
        """Send one prompt to the workflow and wait for the turn to end.

        Returns the stop reason. ``timeout`` bounds the wait only — a pipeline
        that runs in perpetuity never goes idle on its own, so a bounded wait
        that ends in a timeout is the normal way to stop watching one, and
        :meth:`cancel` is how you stop the work.
        """
        if not self._started:
            await self.start()
        self.console.print()
        self.console.print(Panel(f"[bold]{prompt_text}[/bold]", title="[cyan]You[/cyan]", border_style="cyan"))
        self.console.print()
        self._response_buffer = ""
        self._thinking_buffer = ""
        self._stop_reason = None
        # Registered BEFORE the send: a pipeline can go idle before the ack
        # comes back, and an event nobody is waiting on yet is a lost signal.
        self._idle = asyncio.Event()
        self.conversation[self._session_id].append({"role": "user", "content": prompt_text})
        await self._conn.prompt(
            session_id=self._session_id, prompt=[v2.schema.TextContentBlock(text=prompt_text)]
        )
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.console.print(f"\n[yellow]still running after {timeout}s[/yellow]")
        if self._response_buffer or self._thinking_buffer:
            self.conversation[self._session_id].append(
                {"role": "assistant", "content": self._response_buffer,
                 "reasoning_content": self._thinking_buffer, "agent": self._agent or ""}
            )
        self._response_buffer = ""
        self._thinking_buffer = ""
        self.console.print()
        return self._stop_reason

    async def cancel(self) -> None:
        """``session/cancel``: the seam that stops a perpetual pipeline."""
        if self._conn is not None and self._session_id is not None:
            await self._conn.cancel_session(session_id=self._session_id)

    async def repl(self) -> None:
        """Interactive loop. Ctrl-C cancels the turn in flight; Ctrl-D exits."""
        self.console.print(
            Panel("Type a message and press Enter. Ctrl-C cancels the turn, Ctrl-D exits.",
                  title="[magenta]Repl Client[/magenta]", border_style="magenta")
        )
        while True:
            try:
                self.console.print()
                line = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self.console.input(Text("repl> ", style="bold magenta"))
                )
            except (EOFError, KeyboardInterrupt):
                self.console.print("\n[yellow]Goodbye![/yellow]")
                break
            if not line.strip():
                continue
            try:
                await self.send(line)
            except KeyboardInterrupt:
                self.console.print("\n[yellow]cancelling turn...[/yellow]")
                await self.cancel()

    async def close(self) -> None:
        """Shut the agent down and disconnect."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._drainer is not None:
            self._drainer.cancel()
            try:
                await self._drainer
            except (asyncio.CancelledError, Exception):
                pass
            self._drainer = None
        if self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            except Exception:
                pass
            self._stack = None
        self._started = False

    # -- rendering ------------------------------------------------------

    def _tool_call(self, update: Any) -> None:
        """One line per tool call, and a second one only if it failed.

        crow's emitter is create-or-patch with ``exclude_unset``: the create
        carries the title, later patches carry only what changed, and a subtool
        is its own call under a ``<parent>/<child>`` id. Rendering every update
        as it arrives therefore prints three lines for one call — the tool name,
        a richer title, then a bare ``tool_call_id`` from the terminal patch that
        has no title at all. So the title is accumulated per id and printed
        once, and an id on its own is never printed.
        """
        call_id = getattr(update, "tool_call_id", None)
        title = getattr(update, "title", None)
        status = getattr(update, "status", None)
        seen = self._tools.get(call_id)
        if seen is None:
            if not title:
                return  # a patch for a call we never saw created: nothing to show
            self._tools[call_id] = (title, status)
            # crow-cli emits a drained subtool row exactly once — first sight of
            # the id and its completion in the same update — so a failure can
            # arrive with no in-progress phase before it.
            if status == "failed":
                self.console.print(Text(f"\n✗ {title} [failed]", style="bold red"))
            else:
                self.console.print(Text(f"\n⏳ {title}", style="cyan"))
            if len(self._tools) > TOOL_MEMORY:
                for stale in list(self._tools)[: len(self._tools) - TOOL_MEMORY]:
                    del self._tools[stale]
            return
        had_title, had_status = seen
        self._tools[call_id] = (title or had_title, status or had_status)
        if status == "failed" and had_status != "failed":
            self.console.print(Text(f"\n✗ {title or had_title} [failed]", style="bold red"))

    def _rule(self, who: Optional[str]) -> None:
        label = f"[bold purple]{who}[/bold purple]" if who else "[bold purple]Agent[/bold purple]"
        self.console.print()
        self.console.rule(label)
        self.console.print()


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "".join(_text_of(item) for item in content)
    text = getattr(content, "text", None)
    if text:
        return str(text)
    kind = type(content).__name__
    return f"<{kind}>"


async def _amain(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m crow_research.repl_client",
        description=__doc__.splitlines()[0],
        epilog="The positionals are the agent command, exactly as ReplClient(cmd, *args) takes it: "
               "python -m crow_research.repl_client -p 'fix the parser' "
               f"{sys.executable} agents/orchestrator_agent.py --debug",
    )
    parser.add_argument("command", nargs="*", help=f"the agent to spawn (default: {DEFAULT_AGENT})")
    parser.add_argument("-p", "--prompt", default=None,
                        help="send one prompt to the workflow and exit; omit for an interactive REPL")
    parser.add_argument("--cwd", default=None, help="working directory for the agent session")
    parser.add_argument("--timeout", type=float, default=None, help="seconds to wait for the turn to end")
    parser.add_argument("--cancel-after", type=float, default=None,
                        help="send session/cancel this many seconds in — how you stop a perpetual pipeline")
    parser.add_argument("--quiet", action="store_true", help="no rendering; conversation only")
    args = parser.parse_args(argv)
    agent = args.command or [str(DEFAULT_AGENT)]
    client = ReplClient(agent[0], *agent[1:], cwd=args.cwd or str(Path.cwd()), quiet=args.quiet)
    prompt = args.prompt or (None if sys.stdin.isatty() else sys.stdin.read().strip())
    try:
        if prompt:
            task = asyncio.ensure_future(client.send(prompt, timeout=args.timeout))
            if args.cancel_after:
                await asyncio.sleep(args.cancel_after)
                await client.cancel()
            stop = await task
            print(f"stop_reason: {stop}")
        else:
            await client.repl()
    finally:
        await client.close()
    tail = [line.strip() for line in client.stderr[-10:] if line.strip()]
    if tail:
        print("--- agent stderr ---")
        print("".join(tail))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(_amain(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    raise SystemExit(main())
