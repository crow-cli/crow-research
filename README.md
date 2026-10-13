# crow-research

A multi-agent orchestrator for **ACP v2**, built as an agent in its own right.
One session upstream, a cast of specialized agents downstream, and a single
update stream that shows all of their work — each event stamped with the role
that produced it.

It imports nothing from crow-cli. The only thing here that knows crow exists is
the default endpoint a role points at (`crow-cli acp2`), and any other v2 agent
works in its place: spawned from an argv, or dialed on a socket where it is
already being served.

```
┌────────────────────────────────────────────┐
│ upstream client — crow-agent, the VSCode   │
│ panel, `crow-research run`, anything v2    │
└────────────────────────────────────────────┘
              ▲ session/update (one stream, tagged with _meta)
              │ session/prompt → ack, then the turn runs
┌────────────────────────────────────────────┐
│ OrchestratorAgent        (ACP v2 AGENT)    │
│   workflow: async (Turn) -> stop_reason    │
│   Turn: emit · say · notice · run · cancel │
└────────────────────────────────────────────┘
              │ as an ACP v2 CLIENT, one Peer per role
   ┌──────────┼──────────┬─────────────┐
   ▼          ▼          ▼             ▼
 worker    verifier   explorer    researcher
```

## Why v2 makes this possible

Three things in v2 are load-bearing, and each one is a place where a v1 port
would quietly break:

**`session/prompt` is an ack.** The response carries a `message_id` and nothing
else; the turn's progress and its ending arrive as `session/update`s, and the end
is an `idle` `state_update` with a `stop_reason`. So `OrchestratorAgent.prompt`
echoes the user message, returns, and runs the workflow as a task — and
`Peer.send` is a send *plus a watch*, with the watch registered before the send
because a fast turn can go idle before the ack comes back.

**Updates flow at any time, and the agent may initiate.** That is what
`--autostart` is: a session that opens and immediately begins working, with no
prompt from anybody. "Always on" becomes a property of the agent instead of a
cron job wrapped around it.

**Everything is extensible via `_meta`.** A relayed update carries
`crow/agent: {role, title}` and `crow/agent/session`, so an upstream client can
render "who is speaking" without a protocol change — and a client that ignores
`_meta` still sees an ordinary, valid stream.

## Install and run

```sh
uv sync
uv run pytest                      # 3 end-to-end tests, real wire, no model
```

Drive it from a terminal — this spawns `serve` as a child and prints the relayed
stream, which is the whole thing end to end without an IDE:

```sh
uv run crow-research run --workflow chain \
    --role worker="crow-cli acp2" --role verifier="crow-cli acp2" \
    "find the flakiest test in this repo and prove it"
```

Serve it for a real client (the VSCode panel's `crowAcp.endpoint`, crow-agent's
`--agent`, anything that spawns an agent):

```sh
uv run crow-research serve --workflow chain --role worker --role verifier
```

Point a role at an agent that is **already running** instead of spawning one —
verified against a live `crow-cli acp2 --http`:

```sh
uv run crow-research serve --role verifier=ws://127.0.0.1:2771/acp
```

Always on: open a session and the work starts by itself.

```sh
uv run crow-research serve --workflow chain --rounds -1 --autostart "keep poking holes in the last round"
```

## The pieces

| module | what it is |
|---|---|
| `agent.py` | `OrchestratorAgent` — a v2 agent upstream whose react loop is the workflow you pass in. One foreground turn per session; cancel and close are honored mid-turn. |
| `turn.py` | `Turn` — the workflow's whole world: `emit`/`say`/`notice` upstream, `peer`/`run` downstream, `cancelled` for the seams. Relays every child update with its role tag and drops the child's own `state_update`, which would otherwise end the orchestration mid-flight. |
| `peer.py` | `Peer` — one downstream agent: connect, handshake, session, turn, cancel, teardown. `TurnResult` is what a step produced; `.report` is what a handoff carries. |
| `workflows.py` | `single`, `chain`, `rounds`, `forever`, `select` — factories that return `async (Turn) -> stop_reason`. |
| `roles.py` | `Role` — a name, an endpoint, a `brief` (its specialization, since we do not own the child's system prompt) and a `handoff` template. `worker`/`verifier`/`explorer`/`researcher` are built in. |
| `transport.py` | `Stdio` / `Ws` / `Http` behind one `open_endpoint`, so nothing above it branches on transport. |
| `cli.py` | `serve` and `run`. No config file: a cast is `--role name=endpoint`, repeated. |
| `examples/stub_agent.py` | A v2 agent with no model in it. It emits the exact update sequence a real agent owes and quotes what it heard — which is how the tests prove a handoff carried the previous agent's report forward. |

## Writing your own workflow

The contract is one async callable. Everything else is available through `Turn`:

```python
from crow_research import OrchestratorAgent, Role, WORKER, VERIFIER, serve_stdio

async def adversarial(turn):
    """Work, then have somebody who did not do the work try to break it."""
    done = await turn.run(WORKER, turn.prompt)
    await turn.notice("handing off to the verifier")
    verdict = await turn.run(VERIFIER, VERIFIER.assignment(
        task=turn.prompt, report=done.report, previous="worker"))
    if "FAIL" in verdict.report.upper():
        await turn.say("the verifier found holes; going around again\n")
        return await adversarial(turn)          # rounds are just recursion
    return verdict.stop_reason

agent = OrchestratorAgent(adversarial)
```

`turn.run` relays everything the child emits while it works, so a workflow never
touches the wire to be visible. Cancellation is checked between steps and raced
against every child turn, so `session/cancel` lands promptly even when an agent
downstream is stuck — the child is cancelled too, and given a moment to report
its own cancelled idle before we let go of it.

## What is tested

`tests/test_orchestrator.py` runs the real thing over the real wire — `serve`
spawned as a child, stub agents spawned by it in turn, no model anywhere:

* **chain** — both agents speak over one upstream session, in order; every
  update carries its role tag; the children's `state_update`s stay downstream
  (exactly one running/idle pair arrives); tool calls relay; the verifier's
  prompt contains the worker's report.
* **cancel** — a slow child, a cancel mid-turn, and an `idle(cancelled)` that
  actually arrives instead of a hang.
* **autostart** — work begins on `session/new` with no prompt, and the echoed
  user message is stamped `crow/autostart`.

## Next

* **Wake and deliveries.** crow already has the infrastructure; an orchestrator
  that idles until a delivery arrives is the difference between a loop and a
  resident.
* **Compaction as the handoff channel.** `--compact` asks an agent to summarize
  itself for its successor. The TUI's compaction surface is the same event seen
  from the other side, and unifying them is what makes a handoff legible to a
  human watching one stream.
* **`select()` with a real picker.** Choosing the next agent by prompt, by
  config option, or by an LLM judging the task — the seam exists, the judgement
  does not yet.
* **A researcher's round.** The cast is defined; the interesting scientific
  questions it was built to go after are the next document.
