# crow-research

A sequential agent pipeline over **ACP v2**. One agent to the upstream client —
and a client inside `session/prompt`.

`agents/orchestrator_agent.py` is a single spawnable Python file. Upstream it is
an ACP v2 *agent*: it accepts a session, acks a prompt, streams updates. Inside
its prompt handler it is an ACP v2 *client*: it spawns other agents, drives them
turn by turn, and forwards every `session/update` they emit upstream tagged with
`_meta`, so the UI knows who is speaking.

The prompt goes to the whole workflow. There is no flag that picks an agent —
that flag would mean "no pipeline".

## The pipeline

`worker` then `verifier`, repeatedly, **until the verifier signs off
(`VERDICT: PASS`) and the todo list is complete**. Then the explorer adds more
items to the todo. Then round again — in perpetuity. `session/cancel` is what
stops it.

```
  prompt ──► seed the todo (its ADD lines, else the whole prompt)
                 │
       ┌─────────▼──────────────────────────────────────┐
       │ round N                                        │
       │                                                │
       │   worker ──report──► verifier ──VERDICT──┐     │
       │     ▲                                    │     │
       │     └─── FAIL, or items still pending ───┘     │
       │                                                │
       │   PASS and todo.complete ──► explorer ──ADD──┐ │
       └──────────────────────────────────────────────┴─┘
                     there is no exit; cancel ends it
```

Two guards the first real run taught:

* the review loop breaks on `todo.complete`, so the worker is never dispatched
  with nothing to claim (round 2 of run 1 did exactly that — worker *and*
  verifier spent a turn each on an empty list);
* when the explorer adds nothing there is no work anywhere in the system, so the
  loop waits `idle_pause` seconds instead of immediately asking the explorer
  again. The wait is raced against `turn.cancelled`, because a perpetual loop
  that ignores `session/cancel` for thirty seconds is a loop that does not stop.

## The todo is the contract

The list (`todo.py`) outlives the turn: the explorer's items are what the next
round works on. Agents move it by printing lines in their final report, and the
orchestrator parses those lines and nothing else:

| line | who prints it | effect |
|---|---|---|
| `TODO <id> <state> <note>` | worker, verifier | sets item `<id>` to `open`, `working`, `done` or `blocked`. An id that is not on the list is announced upstream rather than dropped — an agent inventing an id is a real event. |
| `ADD <text>` | explorer, and the seeding prompt | appends an item, `added_by` whoever said it |
| `VERDICT: PASS / FAIL / UNVERIFIED` | verifier, last line | last one in the report wins; `PASS` is the sign-off |

`turn.notice` carries the round's shape upstream — `round 3 · Worker`,
`round 3 · verdict PASS`, `round 3 · Explorer added 6 items` — so a client that
renders nothing but notices still shows the loop turning.

## Specialization means editing crow's system prompt

No agent here writes a system prompt. Each one *modifies* crow's:
`prompts.role_prompt()` starts from `crow_cli.agent.prompt.default_system_prompt`
(19219 chars, 21 `<SECTION>` blocks), patches the sections that carry the role's
character, inserts the ones that are new, and preserves `template_args`
(`workspace` and `rlm_depth` are load-bearing). The result is a
`SystemPromptFactory` handed to `crow_cli.agent2.main.agent_run`.

| agent file | patches | inserts | untouched | chars |
|---|---|---|---|---|
| `worker_agent.py` | `ROLE` | `REPORT`, after `IMPORTANT` | 20 of 21 | 19219 → 20515 |
| `verifier_agent.py` | `ROLE`, `PROBLEM_SOLVING_WORKFLOW`, `FILE_SYSTEM_GUIDELINES`, `CODE_QUALITY`, `VERSION_CONTROL` | `VERDICT` | 16 of 21 | → 17841 |
| `explorer_agent.py` | `ROLE`, `PROBLEM_SOLVING_WORKFLOW`, `FILE_SYSTEM_GUIDELINES`, `EXTERNAL_SERVICES` | `FINDINGS` | 17 of 21 | → 19387 |

`patch_section` raises `KeyError` when the section it was asked to patch is not
there, so a rename upstream fails loudly instead of quietly producing a generic
agent. The worker commits before it reports; the verifier is read-only and never
fixes anything; the explorer searches the web first and clones prior art into
`/tmp/explore-*`.

Each agent is **one file a client spawns** — how you run that file *is* the ACP
server configuration. Every one of them takes `--model`, `--http --host --port`,
`--config-dir`, `--config-file` and `--debug`, and serves stdio by default. The
orchestrator spawns them as
`Stdio((sys.executable, "agents/<name>_agent.py", "--model", M))`.

Tools come from `~/.agents/crow/config.yaml`'s `mcpServers` (`mcp.from_config()`),
resolved lazily on the first session — unless the upstream client supplied its
own list, which wins. An explicit empty list means "no tools".

## Running it

```sh
uv sync
uv run pytest -q        # 39 tests; 5 of them end-to-end over the real wire, no model
```

The client is `repl_client.py`, a v2 port of crow's
`sandbox/repl-agent/client.py` `ReplClient` — same shape, same rich rendering.
Positionals are the agent command; the child's own flags go after `--`:

```sh
uv run python -m crow_research.repl_client -p "ADD add a Todo.remove(item_id) method with tests" --cancel-after 900 -- python agents/orchestrator_agent.py --model glm-5.3-flash --debug
```

`--cancel-after` is how a run ends: it sends `session/cancel` N seconds in,
because the loop has no other exit. Without `-p` you get an interactive REPL;
inbound `_meta` renders a rich rule per speaking role, so the stream reads as
Worker, then Verifier, then Explorer.

The generic CLI is still there for pointing the orchestrator at agents that are
not the specialized cast:

```sh
uv run crow-research run --workflow pipeline --role worker --role verifier --role explorer "the task"
uv run crow-research serve --workflow pipeline --role verifier=ws://127.0.0.1:2771/acp
uv run crow-research serve --workflow chain --rounds -1 --autostart "keep poking holes in the last round"
```

A bare `--role name` uses that role's default endpoint, `crow-cli acp2` — an
unspecialized crow agent. `--workflow chain` and `--workflow single` predate the
pipeline and are kept because they are two lines each.

## The pieces

The core is pure ACP v2: nothing imports crow-cli at module load. `prompts.py`
and `mcp.py` are the two crow-cli-aware edges, and `agent.py` imports the latter
lazily, inside `_tool_supply`.

| module | what it is |
|---|---|
| `agent.py` | `OrchestratorAgent(workflow, name=, title=, version=, default_cwd=, autostart=, mcp_servers=)` — a v2 agent upstream whose react loop is the workflow you pass in. `prompt` acks with a `message_id`, echoes the user message, and runs the workflow as a task; the task is shielded so even a cancelled turn writes its own `idle`. One foreground turn per session. |
| `turn.py` | `Turn` — the workflow's whole world: `emit`/`say`/`notice` upstream, `peer`/`run` downstream, `cancelled` for the seams. Peers are registered before `start` so a cancel can reach them; `run` races the child against the cancel and gives it `GRACE` to report its own cancelled idle. Child `state_update`s stay downstream by default. |
| `peer.py` | `Peer` — one downstream agent: connect, handshake, session, turn, cancel, teardown. `PeerClient` records updates and folds running→idle into a per-session stop queue registered *before* the send, auto-allows permissions, declines elicitation. `TurnResult` is what a step produced; `.report` is what a handoff carries. |
| `todo.py` | `Item`, `Todo` (`add`/`extend`/`set`/`remove`/`pending`/`complete`/`render`/`in`), and the three report parsers. |
| `workflows.py` | `pipeline(worker, verifier, explorer, todo, *, timeout=, max_review=5, announce=, idle_pause=30.0)` — the loop — plus its three prompt templates, `apply_updates`, and the older `single`/`chain`/`rounds`/`forever`/`select`. |
| `roles.py` | `Role(name, brief, endpoint, title, model, handoff, env)` and `resolve("name=endpoint")`. A brief is one sentence of handoff framing; the character lives in the agent file. `researcher` is defined and has no agent file yet. |
| `prompts.py` | `sections`, `patch_section`, `insert_section`, `role_prompt` — how a role specializes crow's system prompt. |
| `mcp.py` | config `mcpServers` → v2 wire models (`stdio_server`, `http_server`, `from_map`, `from_config`). Malformed entries raise; nothing is silently skipped. |
| `transport.py` | `Stdio` / `Ws` / `Http` behind one `open_endpoint`, so nothing above it branches on transport. |
| `repl_client.py` | the v2 `ReplClient`, and `python -m crow_research.repl_client` as a console script. |
| `cli.py` | `serve` and `run`. No config file: a cast is `--role name=endpoint`, repeated. |
| `examples/stub_agent.py` | A v2 agent with no model in it. `STUB_REPLY_<NAME>` scripts a role's report, so the pipeline tests can emit `TODO 1 done`, `VERDICT: PASS` and `ADD …` over a real wire. |

## Writing your own workflow

The contract is one async callable, `async (Turn) -> stop_reason`. The pipeline is
that, with a todo list in the closure:

```python
from crow_research import OrchestratorAgent, Role, Todo, WORKER, VERIFIER, pipeline

agent = OrchestratorAgent(pipeline(WORKER, VERIFIER, EXPLORER, Todo(), idle_pause=30.0))
```

Anything else works too, and rounds are just recursion:

```python
async def adversarial(turn):
    """Work, then have somebody who did not do the work try to break it."""
    done = await turn.run(WORKER, turn.prompt)
    await turn.notice("handing off to the verifier")
    verdict = await turn.run(VERIFIER, VERIFIER.assignment(
        task=turn.prompt, report=done.report, previous="worker"))
    if "FAIL" in verdict.report.upper():
        await turn.say("the verifier found holes; going around again")
        return await adversarial(turn)
    return verdict.stop_reason
```

`turn.run` relays everything the child emits while it works, so a workflow never
touches the wire to be visible. Cancellation is checked between steps and raced
against every child turn, so `session/cancel` lands promptly even when an agent
downstream is stuck — the child is cancelled too, and given a moment to report
its own cancelled idle before we let go of it.

## Why v2 makes this possible

Three things in v2 are load-bearing, and each one is a place where a v1 port
would quietly break:

**`session/prompt` is an ack.** The response carries a `message_id` and nothing
else; the turn's progress and its ending arrive as `session/update`s, and the end
is an `idle` `state_update` with a `stop_reason`. So `OrchestratorAgent.prompt`
echoes the user message, returns, and runs the workflow as a task — and
`Peer.send` is a send *plus a watch*, with the watch registered before the send
because a fast turn can go idle before the ack comes back. `ReplClient.send`
awaits that idle for the same reason.

**Updates flow at any time, and the agent may initiate.** That is what
`--autostart` is: a session that opens and immediately begins working, with no
prompt from anybody. "Always on" becomes a property of the agent instead of a
cron job wrapped around it.

**Everything is extensible via `_meta`.** A relayed update carries
`crow/agent: {role, title}` and `crow/agent/session`, so an upstream client can
render "who is speaking" without a protocol change — and a client that ignores
`_meta` still sees an ordinary, valid stream.

## What is tested

`tests/test_orchestrator.py` and `tests/test_pipeline.py` run the real thing over
the real wire — the orchestrator spawned as a child, stub agents spawned by it in
turn, no model anywhere:

* **chain** — both agents speak over one upstream session, in order; every update
  carries its role tag; the children's `state_update`s stay downstream (exactly
  one running/idle pair arrives); tool calls relay; the verifier's prompt
  contains the worker's report.
* **cancel** — a slow child, a cancel mid-turn, and an `idle(cancelled)` that
  actually arrives instead of a hang.
* **autostart** — work begins on `session/new` with no prompt, and the echoed
  user message is stamped `crow/autostart`.
* **sign-off** — a scripted `VERDICT: PASS` on a complete list sends the loop to
  the explorer, and the worker is *not* re-dispatched with nothing to claim.
* **explorer additions** — the explorer's `ADD` lines land on the list and reach
  the next worker prompt.

`tests/test_todo.py` is 34 unit tests for the list and the report parsers. It was
written by the pipeline's own worker on run 1 and extended by it on runs 2 and 3.

## What it has actually done

Three runs against real models, each ended by `session/cancel`:

| run | model | observed |
|---|---|---|
| 1 | `deepseek-v4-pro` | Worker wrote `tests/test_todo.py`, 30 passed, committed `5d8b379`. Verifier reproduced every claim, found nothing, `VERDICT: PASS`. Explorer added 0 items, so round 2 ran worker and verifier against an empty list — the defect the `todo.complete` guard now prevents — and then added 6 (prior art: pydantic-ai, todo.txt, langchain deepagents, opencode, Claude Code todo-tracking and agent-teams). Round 3's worker was mid-implementation when the cancel landed; its in-flight edits broke 2 tests and are kept in `stash@{0}` as evidence. |
| 2 | `glm-5.3-flash` | Worker added `Todo.remove` plus 4 tests, `36 passed`, committed `28a0202` under its own session trailer. Verifier dispatched; the cancel landed before it reported. |
| 3 | `glm-5.3-flash` | Worker added `Todo.__contains__` plus 3 tests, `39 passed`. |

## Next

* **A researcher's round.** The role is defined in `roles.py` and has no agent
  file; the interesting scientific questions this was built to go after are the
  next document.
* **Wake and deliveries.** crow already has the infrastructure; an orchestrator
  that idles until a delivery arrives is the difference between a loop and a
  resident.
* **Compaction as the handoff channel.** `--compact` asks an agent to summarize
  itself for its successor. The TUI's compaction surface is the same event seen
  from the other side, and unifying them is what makes a handoff legible to a
  human watching one stream.
* **`select()` with a real picker.** Choosing the next agent by prompt, by config
  option, or by an LLM judging the task — the seam exists, the judgement does not
  yet.
