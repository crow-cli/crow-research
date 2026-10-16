## **DO NOT ASK USER FOR FEEDBACK — THIS IS THE USER FEEDBACK.**
## **DO NOT ASK USER FOR NEXT STEPS — THESE ARE THE NEXT STEPS.**

# PLAN — The Agentic Scientist, crow-native

Build/test command, used by every item below:

```sh
uv --project /home/thomas/src/crow-research run pytest -q
```

Baseline at `2e784f7`: **52 passed** (44 `test_todo`, 3 `test_pipeline`,
3 `test_orchestrator`, 2 `test_http`). Nothing in this plan is allowed to make
that number go down.

Trajectory: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9. Phase 2 (the example task) is
independent of the role work and can be pulled forward if a later item needs a
real evaluator to test against.

Hard rules for every item, inherited from the session that started this:

- **No clocks, no caps, no retry counters.** Not in the product, not in a flag,
  not "just for tests". Test-side deadlines that turn a hang into a failure are
  the one exception and they already exist.
- **Specialization is patching crow's system prompt** via `role_prompt()`. Never
  write a fresh system prompt.
- **One file per agent**, spawnable, stdio by default, `--http --host --port`
  optional. No `--agent` flag anywhere; the prompt goes to the whole workflow.
- **One state file.** `.crow-research/todo.json` is the contract. Do not add a
  second one.
- Precision edits, not rewrites. `git commit -a -F /tmp/msgN.txt` at every
  checkpoint, trailer `Session-Id: unbreakable-elegant-bettong-of-essence`.

---

## Phase 1 — the campaign document

**1.1** Read `src/crow_research/todo.py` end to end before touching it. Note
`Item.from_dict`, `Todo.set`, `Todo.load`, `Todo.render`, `state_path`,
`STATES`, `VERDICTS`, and where validation is duplicated between `from_dict`
and `set`.
*Verified by:* the duplication is named in the commit message or in a comment,
not silently re-implemented a third time.

**1.2** Add the campaign dataclasses: `Rubric {id, description, type}`,
`Idea {id, title, hypothesis, plan, rubrics, notes, added_by}`,
`Attempt {idea_id, program, metrics, correct, rubrics, stop_reason, note}`,
`Best {program, score, attempt}`, `TaskSpec {goal, rubrics, evaluator, program0}`.
`RUBRIC_TYPES = ("metric", "implementation")`. Frozen dataclasses, `from_dict`
that raises on a bad shape, `to_dict` that round-trips.
*Verified by:* unit tests — round-trip every type; a rubric `type` outside
`RUBRIC_TYPES` raises; a non-snake_case rubric id raises; an `Attempt` naming an
`idea_id` that is not in `ideas` raises.

**1.3** Hang them off `Todo`: `todo.task`, `todo.ideas`, `todo.attempts`,
`todo.best`, `todo.conclusions`. `load` accepts an old file with none of them
(the 52 existing tests and every live state file must still load); `save` writes
them; `render` shows them.
*Verified by:* the full suite still 52+ passed with no change to existing test
expectations; a new test loads the pre-existing `.crow-research/todo.json` shape.

**1.4** Host-owned scaffold: `Todo.scaffold_rubrics(idea)` returns the
`pass: false` / empty-evidence status document the verifier fills in, and
`Todo.apply_rubric_status(...)` accepts only `pass`, `evidence` and
`next_recommendation` from an agent, computing `overall_pass` itself.
*Verified by:* unit test — an agent write that tries to set `overall_pass: true`
with a failing rubric is corrected by the host, not believed.

**1.5** The `best` gate: `Todo.record_attempt(attempt)` updates `best` only when
`correct` is true and the score is strictly higher. Two independent gates, as in
their §3.3 — all-rubrics-pass closes the contract, a better correct score moves
`best`.
*Verified by:* unit tests for higher/equal/lower/incorrect, and for
rubric-pass-without-a-better-score.

**1.6** Conclusions validation: one paragraph, no blank lines, <= 300 words, and
a recorded gate reason from `contradicts | new_category | flips | refines | none`.
*Verified by:* unit tests for each rejection and for a valid update.

**1.7** Commit. `uv run pytest -q` green, count >= 52.

## Phase 2 — an executable example task

**2.1** `examples/tasks/circpack/task.md` — the goal in prose, the rubrics in the
state-file shape (at least one `metric` and one `implementation` rubric), the
candidate interface, and how the evaluator is invoked.
*Verified by:* the rubric ids in `task.md` are exactly the ones `evaluate.py`
and the state scaffold use.

**2.2** `examples/tasks/circpack/evaluate.py` — deterministic, CPU-only, no
network, runs in seconds. Writes `metrics.json` (`combined_score`, plus the
per-circle detail) and `correct.json` (`correct`, and why not). Overlap or
out-of-bounds is `correct: false`. Same score as theirs: sum of radii.
*Verified by:* a test runs it on `program_0` in a tmpdir and asserts
`correct: true` and `combined_score > 0`; runs it on a deliberately-overlapping
candidate and asserts `correct: false`; runs it twice and asserts identical
output.

**2.3** `examples/tasks/circpack/program_0/` — a grid-placement baseline,
read-only reference, scores above zero.
*Verified by:* 2.2's test.

**2.4** Commit.

## Phase 3 — the two new roles

**3.1** `roles.py`: `DRAFTER` and `WRITER` in `BUILTIN`, each with a `brief` in
the same voice as the existing four. `RESEARCHER` stays file-less and parked.
*Verified by:* `resolve("drafter")` and `resolve("writer")` work, and
`resolve("writer=ws://…")` still parses an endpoint.

**3.2** `agents/drafter_agent.py` — turns a research brief into a task spec:
goal, rubrics, evaluator, `program_0`. Writes `task` into the state file. It may
write the evaluator and the baseline, because without them there is nothing to
score; it does not run experiments.
*Verified by:* the file's `system_prompt` factory renders against a real
`Config.load()` — assert the section count, the char count, `crow_role ==
"drafter"`, and that every patched section name exists in crow's prompt.

**3.3** `agents/writer_agent.py` — turns the evidence into a grounded report:
every quantitative claim resolves to an `attempt` or a `metrics.json`, and the
claim-to-source map is part of the output. Read-only on the code.
*Verified by:* same render assertions as 3.2.

**3.4** Both files: `--http/--host/--port/--model/--config-dir/--config-file/--debug`,
stdio by default, distinct default ports that do not collide with 2771/2772/2782.
*Verified by:* `--help` on both; a spawn-and-handshake check like
`test_the_agent_file_serves_http`.

**3.5** Commit.

## Phase 4 — the three existing contracts grow

**4.1** `explorer_agent.py`: the `FINDINGS` section becomes an idea contract —
title, falsifiable hypothesis, ordered plan, `target_rubrics` with at least one
`metric` and one `implementation` rubric, stable snake_case ids. Read-only in the
repo apart from the state file. No execution during ideation: no Bash, no Python,
no pip, no probes. Keep the `be16d27` framing (goal first, not an auditor).
*Verified by:* render assertions; the old prose-only shape is gone from the file.

**4.2** `worker_agent.py`: append-only `program_<idx>`, never overwrite an older
one; run the task's evaluator and nothing else; write `metrics`, `correct` and
rubric evidence as an `attempt`. No exploratory execution outside the official
evaluation.
*Verified by:* render assertions; the dispatch prompt in `workflows.py` names the
evaluator and the next free `program_<idx>`.

**4.3** `verifier_agent.py`: it checks the rubrics the explorer wrote, from the
host's scaffold, and fills only `pass`, `evidence`, `next_recommendation`. It
does not invent criteria, does not add items, does not edit.
*Verified by:* render assertions; a wire test shows the scaffold in its dispatch.

**4.4** `workflows.py` prompt templates: `EXPLORER_PROMPT` grows the idea
contract and the schema; `WORKER_PROMPT` grows the evaluator, the program index
and the attempt record; `VERIFIER_PROMPT` grows the scaffold. `STATE_SCHEMA` and
`STATE_RULES` updated to match the extended document.
*Verified by:* the existing three pipeline tests still pass unchanged in intent;
`.format()` on each template with a realistic state does not raise `KeyError`.

**4.5** Commit.

## Phase 5 — the `scientist` workflow

**5.1** `workflows.scientist(drafter, worker, verifier, explorer, writer, todo, *,
announce=True, state_path=None) -> Workflow`. Drafter dispatched once, only when
the file has no `task`. Then the existing perpetual loop. Then, when a round
signs off on a complete list **and** the explorer adds nothing, the writer is
dispatched to publish what the campaign has — and the round continues anyway.
*Verified by:* the function is exported from `crow_research`; its docstring says
plainly that nothing in it is on a clock.

**5.2** A rejected idea is a notice, not a retry counter: the orchestrator
validates what the explorer wrote, and on failure emits a notice naming the
problem and lets the next round ask again.
*Verified by:* a stub explorer scripted to write a rubric with a bad `type`
produces the notice and the loop keeps going.

**5.3** The orchestrator pre-creates the rubric scaffold before dispatching the
verifier, and computes `overall_pass` and `best` itself afterwards.
*Verified by:* wire test — the scaffold is in the verifier's dispatch, and a stub
verifier that lies about `overall_pass` is corrected in the file.

**5.4** Commit.

## Phase 6 — wiring

**6.1** `cli.py`: `--workflow scientist` builds the five-role cast from `--role`
specs, defaulting each to its own agent file.
*Verified by:* `uv run crow-research serve --workflow scientist --help` and a dry
construction with all five roles pointed at the stub.

**6.2** `agents/orchestrator_agent.py`: the scientist cast, same flags as today.
*Verified by:* spawn-and-handshake over stdio and over `--http`.

**6.3** Commit.

## Phase 7 — tests over the real wire

**7.1** `tests/test_scientist.py`: the stub cast driven through
`workflows.scientist` over a real socket. Assert the dispatch order
`Drafter → Worker → Verifier → Explorer → Writer`, the role stamps in `_meta`,
the notices, `stop_reason == "cancelled"`, and the state file afterwards.
*Verified by:* the test passes; it fails if any role is dispatched out of order.

**7.2** A resume test: a file with a `task`, a signed-off verdict and a complete
list goes straight to the explorer, with no drafter turn.
*Verified by:* the test passes and asserts `Drafter` was never dispatched.

**7.3** The example task's evaluator is exercised by a real worker-shaped stub
that writes an attempt, and the host's `best` gate is asserted from the file.
*Verified by:* the test passes.

**7.4** Commit. Full suite green, count recorded in README.

## Phase 8 — docs

**8.1** README: the mapping table, "what we took", "what we refused and why", how
to run a campaign against `examples/tasks/circpack`, and the new test count.
*Verified by:* every command in the new prose actually runs, copy-pasted.

**8.2** Commit.

## Phase 9 — a real run

**9.1** Start a campaign on local `glm-5.3-flash` against
`examples/tasks/circpack/` through `repl_client.py`, in a scratch cwd outside the
repo. Let it run. End it by hand with `session/cancel` — nothing else ends it.
*Verified by:* the state file afterwards: `task` drafted, at least one idea with
rubrics, at least one attempt with metrics, `best` set only by the gate.

**9.2** Write the evidence into README's run table: rounds, ideas, attempts,
scores, whether the rubric gate held, what the model got wrong.
*Verified by:* the numbers in the table match the state file and the run log.

**9.3** Commit, then `goal_done`.
