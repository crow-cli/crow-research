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
(`"verdict": "PASS"`) and the todo list is complete**. Then the explorer adds
more items to the todo. Then round again — in perpetuity. `session/cancel` is
what stops it.

```
  prompt ──► write the todo file (the whole prompt is item 1)
                 │
       ┌─────────▼──────────────────────────────────────┐
       │ round N                                        │
       │                                                │
       │   worker ──report──► verifier ──verdict──┐     │
       │     ▲                                    │     │
       │     └── not PASS, or items still open ───┘     │
       │                                                │
       │   PASS and complete ──► explorer ──new items─┐ │
       └──────────────────────────────────────────────┴─┘
                     there is no exit; cancel ends it
```

Two guards the first real run taught:

* the worker is dispatched only when there is something to claim, and the review
  loop breaks once a complete list has a verdict on it. Round 2 of run 1 spent a
  worker turn *and* a verifier turn on an empty list; a round resumed from the
  file with the work already done would spend a worker turn saying "nothing to
  do" before the verifier ever saw it;
* nothing in the loop is on a clock. A turn ends when the child ends it; the
  loop ends when the client cancels it. An explorer that adds nothing is simply
  asked again, which is what "in perpetuity" means.

## The todo is the contract, and the contract is a file

`.crow-research/todo.json`, under the working directory the session was opened on
(`--cwd` pins it). The list outlives the turn — the explorer's items are what the
next round works on — and it outlives the process: a run cancelled mid-round is
picked up where it left off, and a human can add an item with a text editor.

```json
{
  "verdict": null,
  "verdict_note": "",
  "items": [
    {"id": 1, "text": "what to do", "state": "open", "note": "", "added_by": "prompt"}
  ]
}
```

| who | writes |
|---|---|
| orchestrator | the file once, to seed it — the prompt verbatim as item 1. It never edits it again: it reads it back after every turn and announces what moved. |
| worker | `state` and `note` on the items it touched |
| verifier | `state` on the items whose claims it disputes, plus `verdict` and `verdict_note` |
| explorer | new items, each `id` one past the highest in the file |

`state` is `open`, `working`, `done` or `blocked`; `verdict` is `PASS`, `FAIL`,
`UNVERIFIED` or `null`. Anything else — a duplicate id, an item with no text, a
verdict spelled `MAYBE`, a file that does not parse — is a `ValueError` on load,
which the orchestrator announces upstream and answers by putting the last good
copy back. The write is lost, the run goes on, and whoever is watching is told.

That is the whole reason it is a file. The protocol this replaced was lines in
the agents' prose — `TODO 1 done …`, `ADD …`, `VERDICT: PASS` — and a line the
parser did not recognize was *silently dropped*, which looks exactly like an
agent agreeing with you. Run 3 spun a round on an item the worker had already
finished, because the worker wrapped its line in backticks and the parser read
them literally. `json.load` has no such failure mode: it works, or it raises.

The agents write it with the same `edit` tool they use on source, which is why it
is `indent=2` and why every key is always present: a document an agent can patch
in place, rather than one it has to regenerate from memory.

What an agent *says* is prose — for whoever is watching, and for the next agent's
prompt. Nothing in it is parsed.

`turn.notice` carries the round's shape upstream — `round 3 · Worker`,
`todo 3 -> done`, `round 3 · verdict PASS`, `round 3 · Explorer added 6 items` —
so a client that renders nothing but notices still shows the loop turning. Those
notices are a diff of the file before and after each turn, so they cannot drift
from what the file says.

## Specialization means editing crow's system prompt

No agent here writes a system prompt. Each one *modifies* crow's:
`prompts.role_prompt()` starts from `crow_cli.agent.prompt.default_system_prompt`
(19219 chars, 21 `<SECTION>` blocks), patches the sections that carry the role's
character, inserts the ones that are new, and preserves `template_args`
(`workspace` and `rlm_depth` are load-bearing). The result is a
`SystemPromptFactory` handed to `crow_cli.agent2.main.agent_run`.

| agent file | patches | inserts | untouched | chars |
|---|---|---|---|---|
| `worker_agent.py` | `ROLE` | `REPORT`, after `IMPORTANT` | 20 of 21 | 19219 → 21179 |
| `verifier_agent.py` | `ROLE`, `PROBLEM_SOLVING_WORKFLOW`, `FILE_SYSTEM_GUIDELINES`, `CODE_QUALITY`, `VERSION_CONTROL` | `VERDICT` | 16 of 21 | → 18607 |
| `explorer_agent.py` | `ROLE`, `PROBLEM_SOLVING_WORKFLOW`, `FILE_SYSTEM_GUIDELINES`, `EXTERNAL_SERVICES` | `FINDINGS` | 17 of 21 | → 20105 |

`patch_section` raises `KeyError` when the section it was asked to patch is not
there, so a rename upstream fails loudly instead of quietly producing a generic
agent. The worker commits before it reports; the verifier is read-only and never
fixes anything; the explorer reads the goal and the state file and writes the
next round's items, with the state file its only write inside the repo.

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
uv run pytest -q        # 52 tests; 8 of them end-to-end over the real wire, no model
```

The client is `repl_client.py`, a v2 port of crow's
`sandbox/repl-agent/client.py` `ReplClient` — same shape, same rich rendering.
Positionals are the agent command; the child's own flags go after `--`:

```sh
uv run python -m crow_research.repl_client -p "add a Todo.remove(item_id) method with tests" -- python agents/orchestrator_agent.py --model glm-5.3-flash --debug
```

A run ends when you end it: Ctrl-C sends `session/cancel`, which is the only
exit the loop has. Without `-p` you get an interactive REPL, where Ctrl-C
cancels the turn in flight and Ctrl-D exits; inbound `_meta` renders a rich
rule per speaking role, so the stream reads as Worker, then Verifier, then
Explorer.

### Served instead of spawned

The same agent file also serves, for a client that dials rather than forks:

```sh
uv run python agents/orchestrator_agent.py --http --port 2772
# -> ws://127.0.0.1:2772/acp   (Streamable HTTP + WebSocket, one agent per connection)
```

`serve_http` hands `AgentProtocolRouter` a *factory*, so every connection gets
its own `OrchestratorAgent`; only a v2 factory is configured, so a v1 client is
refused at negotiation rather than half-served. The default port is 2772 because
2771 is `crow-cli acp2`'s own default and the two are exactly the agents most
likely to be up at once. Nothing about the pipeline changes on a socket — the
role stamps, the notices and `session/cancel` all arrive as they do over stdio,
which is what `tests/test_http.py` asserts.

`crow-agent` will not dial it: a harness there is an argv (`harness.rs` is
`{command, args, env}`), so the extension spawns the agent over stdio and
`--http` is for clients that speak Streamable HTTP or WebSocket.

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

## Driving it from crow-agent

`crow-agent` — the `crow-client` crate — is an arbitrary ACP client: a harness is
an argv, and the protocol is negotiated rather than declared. Point it at the
pipeline and it spawns the orchestrator, adopts v2, and supplies the tools:

```sh
crow-agent -w /home/thomas/src/crow-research --agent /home/thomas/src/crow-research/.venv/bin/python --agent-arg /home/thomas/src/crow-research/agents/orchestrator_agent.py --agent-arg --model --agent-arg glm-5.3-flash
```

or as a harness entry in `~/.agents/crow/settings.json`, which is what the
in-TUI `/harness` picker offers and what `defaultHarness` selects:

```json
{"id": "crow-research",
 "label": "crow-research pipeline (glm-5.3-flash)",
 "command": "/home/thomas/src/crow-research/.venv/bin/python",
 "args": ["/home/thomas/src/crow-research/agents/orchestrator_agent.py",
          "--model", "glm-5.3-flash"],
 "env": {}}
```

`crow-agent --check-runtime` spawns and initializes without the TUI, and reports
what the peer actually said: `initialize ok in 0.7s → crow-research acp2`. The
`acp2` suffix is the negotiated stack, so v2 was adopted, not assumed.

Three things line up with no configuration at all. **Tools**: `mcp_supply.rs`
reads `mcpServers` from crow's settings and then from `~/.agents/crow/config.yaml`
— the same file `mcp.from_config()` reads — and passes them on `session/new`,
where `_tool_supply` lets the upstream supply win, so the children get exactly
what the client handed the orchestrator. **Cancel**: `esc` is `Cmd::Interrupt`,
which sends `session/cancel`, so the loop's only exit is a first-class key.
**The turn**: crow-agent already treats `session/prompt` as an ack and settles
the turn off the `idle` `state_update`, which is the same reading
`ReplClient.send` makes.

One gap, and it is the whole narration. `events.rs::parse_session_update` has no
`notice` arm, so notices fall through to `_ => Vec::new()`: driven from
crow-agent today the pipeline is an unexplained stream of worker prose, with
`round 1 · verdict PASS` and `Explorer added 6 items` invisible. The fix is one
arm mapping onto the `UiEvent::SessionNotice` that already exists and already
renders `title — details` with severity as info/warn/error — field for field what
`turn.notice(title, description=, severity=)` emits. Role attribution is the
second gap: `_meta["crow/agent"]` reaches the parser and nothing reads it, so
the three roles share one transcript instead of using the subagent rail that
`crow/agent/session` would key into.

## The pieces

The core is pure ACP v2: nothing imports crow-cli at module load. `prompts.py`
and `mcp.py` are the two crow-cli-aware edges, and `agent.py` imports the latter
lazily, inside `_tool_supply`.

| module | what it is |
|---|---|
| `agent.py` | `OrchestratorAgent(workflow, name=, title=, version=, default_cwd=, autostart=, mcp_servers=)` — a v2 agent upstream whose react loop is the workflow you pass in — plus `serve_stdio(agent)` and `serve_http(factory, host, port)`. `prompt` acks with a `message_id`, echoes the user message, and runs the workflow as a task; the task is shielded so even a cancelled turn writes its own `idle`. One foreground turn per session. |
| `turn.py` | `Turn` — the workflow's whole world: `emit`/`say`/`notice` upstream, `peer`/`run` downstream, `cancelled` for the seams. Peers are registered before `start` so a cancel can reach them; `run` races the child against the cancel and lets go of it when the cancel wins. Child `state_update`s stay downstream by default. |
| `peer.py` | `Peer` — one downstream agent: connect, handshake, session, turn, cancel, teardown. `PeerClient` records updates and folds running→idle into a per-session stop queue registered *before* the send, auto-allows permissions, declines elicitation. `TurnResult` is what a step produced; `.report` is what a handoff carries. |
| `todo.py` | `Item`, `Todo` (`add`/`extend`/`set`/`remove`/`get`/`pending`/`complete`/`render`/`in`), the document (`to_dict`/`from_dict`, validating) and the file (`load`/`save`, atomic). `STATE_FILE` and `state_path(cwd)`. |
| `workflows.py` | `pipeline(worker, verifier, explorer, todo, *, max_review=5, announce=, state_path=)` — the loop — plus its three prompt templates, `STATE_SCHEMA` and `STATE_RULES` (the file contract every agent is shown), and the older `single`/`chain`/`rounds`/`forever`/`select`. |
| `roles.py` | `Role(name, brief, endpoint, title, model, handoff, env)` and `resolve("name=endpoint")`. A brief is one sentence of handoff framing; the character lives in the agent file. `researcher` is defined and has no agent file yet. |
| `prompts.py` | `sections`, `patch_section`, `insert_section`, `role_prompt` — how a role specializes crow's system prompt. |
| `mcp.py` | config `mcpServers` → v2 wire models (`stdio_server`, `http_server`, `from_map`, `from_config`). Malformed entries raise; nothing is silently skipped. |
| `transport.py` | `Stdio` / `Ws` / `Http` behind one `open_endpoint`, so nothing above it branches on transport. |
| `repl_client.py` | the v2 `ReplClient`, and `python -m crow_research.repl_client` as a console script. |
| `cli.py` | `serve` and `run`. No config file: a cast is `--role name=endpoint`, repeated. |
| `examples/stub_agent.py` | A v2 agent with no model in it. `STUB_REPLY_<NAME>` scripts what a role *says*; `STUB_STATE_<NAME>` is a JSON patch deep-merged into `STUB_STATE_PATH` before it goes idle, which is what a role *writes* — so the pipeline tests drive the real contract over a real wire. |

## Writing your own workflow

The contract is one async callable, `async (Turn) -> stop_reason`. The pipeline is
that, with a todo file it reads back after every turn:

```python
from crow_research import EXPLORER, OrchestratorAgent, Todo, VERIFIER, WORKER, pipeline, state_path

agent = OrchestratorAgent(pipeline(WORKER, VERIFIER, EXPLORER, Todo()))
```

Anything else works too, and rounds are just recursion:

```python
async def adversarial(turn):
    """Work, then have somebody who did not do the work try to break it."""
    path = state_path(turn.cwd)
    task = f"{turn.prompt}\n\nThe todo list is {path}; write your outcome into it."
    done = await turn.run(WORKER, task)
    await turn.notice("handing off to the verifier")
    await turn.run(VERIFIER, VERIFIER.assignment(
        task=task, report=done.report, previous="worker"))
    # The verdict is a field in the file, not a word in the report. Reading it
    # back is the difference between knowing what happened and guessing at it.
    if Todo.load(path).verdict != "PASS":
        await turn.say("the verifier found holes; going around again")
        return await adversarial(turn)
    return done.stop_reason
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

`tests/test_orchestrator.py`, `tests/test_pipeline.py` and `tests/test_http.py`
run the real thing over the real wire — the orchestrator spawned as a child or
served on a socket, stub agents spawned by it in turn, no model anywhere:

* **chain** — both agents speak over one upstream session, in order; every update
  carries its role tag; the children's `state_update`s stay downstream (exactly
  one running/idle pair arrives); tool calls relay; the verifier's prompt
  contains the worker's report.
* **cancel** — a slow child, a cancel mid-turn, and an `idle(cancelled)` that
  actually arrives instead of a hang.
* **autostart** — work begins on `session/new` with no prompt, and the echoed
  user message is stamped `crow/autostart`.
* **sign-off** — a `"verdict": "PASS"` written into the state file on a complete
  list sends the loop to the explorer, and the worker is *not* re-dispatched with
  nothing to claim. The file is asserted afterwards too: it is the only thing a
  restarted run would read.
* **explorer additions** — the items the explorer appends to the file land on the
  list and reach the next worker prompt, and the explorer's own dispatch carries
  the user's ask: an explorer told only the verdict and the file has no goal to
  aim the next round at.
* **resume** — a file left behind by a cancelled run, with the work done and no
  verdict on it, sends the loop straight to the verifier: no seeding notice, no
  worker turn, and the verifier is told there is no report rather than handed a
  blank one.
* **http** — the same loop served by `serve_http` and dialled by a `Peer` over
  `ws://…/acp`: role stamps, notices and `session/cancel` all survive the socket,
  and `agents/orchestrator_agent.py --http --port N` binds, negotiates v2 and
  opens a session.

The pipeline tests are driven by `STUB_STATE_<ROLE>`, a JSON patch each stub
merges into the state file before it goes idle, so what is under test is the real
contract — the agents writing, the orchestrator reading back — and not a stub's
idea of it.

`tests/test_todo.py` is 44 unit tests for the list, the document and the file:
what a document has to get wrong to be refused, and what a refused document costs
(the last good copy goes back, the run goes on). It was written by the pipeline's
own worker on run 1 and extended by it on runs 2, 3 and 4.

## What it has actually done

Five runs against real models, each ended by `session/cancel`. The first four ran
on the prose protocol; run 5 is the first on the state file:

| run | model | observed |
|---|---|---|
| 1 | `deepseek-v4-pro` | Worker wrote `tests/test_todo.py`, 30 passed, committed `5d8b379`. Verifier reproduced every claim, found nothing, `VERDICT: PASS`. Explorer added 0 items, so round 2 ran worker and verifier against an empty list — the defect the `todo.complete` guard now prevents — and then added 6 (prior art: pydantic-ai, todo.txt, langchain deepagents, opencode, Claude Code todo-tracking and agent-teams). Round 3's worker was mid-implementation when the cancel landed; its in-flight edits broke 2 tests and are kept in `stash@{0}` as evidence. |
| 2 | `glm-5.3-flash` | Worker added `Todo.remove` plus 4 tests, `36 passed`, committed `28a0202` under its own session trailer. Verifier dispatched; the cancel landed before it reported. |
| 3 | `glm-5.3-flash` | Worker added `Todo.__contains__` plus 3 tests, committed `ee44965`. Verifier reproduced it and returned `VERDICT: PASS` — and the round did not end, because the worker had wrapped its state line in backticks and the parser read it literally. `4f80fcc` is that fix. |
| 4 | `glm-5.3-flash` | Worker added `Todo.get` plus 3 tests, committed `ff5efc1`. Verifier reproduced every claim, examined and rejected a `todo.get(True)` bool/int nitpick, and returned `VERDICT: PASS`. The round closed for the first time — `todo 1 -> done`, list empty, explorer dispatched — and the cancel landed during the explorer's turn. It closed because the model happened *not* to use backticks this round, which is exactly the fragility the state file removes: the fix for run 3 taught the parser to tolerate more prose, and the answer was to stop reading prose. |
| 5 | `glm-5.3-flash` | The first run on the state file. The prompt became item 1 verbatim; the worker set it to `working` in the JSON, made `Todo.set` refuse an invented state, added 3 tests (`49 passed`), committed `ff29d87`, then wrote `state: done` with a note carrying the whole account — and closed its handoff with "the file parses after my write". `todo 1 -> done` was announced off a diff of the file, not off a line of prose. The verifier's first act was `fs/read` on the same file. The cancel landed mid-verification, which is the resume path the third pipeline test now pins. |

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
