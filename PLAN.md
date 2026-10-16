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

## Phase 1 — the campaign document  ✅ DONE (`ddb8e3f`, 2026-10-15, 96 passed)

Evidence: `src/crow_research/campaign.py` (types + every rejection), `Todo` grows
`task`/`ideas`/`attempts`/`best`/`conclusions` plus `add_idea`, `idea`,
`latest_idea`, `scaffold_rubrics`, `next_program_index`, `record_attempt`,
`apply_rubric_status`, `set_conclusions`, `render_campaign`. 44 new unit tests in
`tests/test_campaign.py`. Two existing document-shape tests updated for the grown
contract; a legacy file with no campaign keys still loads. `render()` untouched.
Bug found by a test and fixed: the `best` gate was in `record_attempt`, so an
unverified better score became the incumbent — it now moves only in
`apply_rubric_status`, after verification.

**1.1** ✅ Read `src/crow_research/todo.py` end to end before touching it. Note
`Item.from_dict`, `Todo.set`, `Todo.load`, `Todo.render`, `state_path`,
`STATES`, `VERDICTS`, and where validation is duplicated between `from_dict`
and `set`.
*Verified by:* the duplication is named in the commit message or in a comment,
not silently re-implemented a third time.

**1.2** ✅ Add the campaign dataclasses: `Rubric {id, description, type}`,
`Idea {id, title, hypothesis, plan, rubrics, notes, added_by}`,
`Attempt {idea_id, program, metrics, correct, rubrics, stop_reason, note}`,
`Best {program, score, attempt}`, `TaskSpec {goal, rubrics, evaluator, program0}`.
`RUBRIC_TYPES = ("metric", "implementation")`. Frozen dataclasses, `from_dict`
that raises on a bad shape, `to_dict` that round-trips.
*Verified by:* unit tests — round-trip every type; a rubric `type` outside
`RUBRIC_TYPES` raises; a non-snake_case rubric id raises; an `Attempt` naming an
`idea_id` that is not in `ideas` raises.

**1.3** ✅ Hang them off `Todo`: `todo.task`, `todo.ideas`, `todo.attempts`,
`todo.best`, `todo.conclusions`. `load` accepts an old file with none of them
(the 52 existing tests and every live state file must still load); `save` writes
them; `render` shows them.
*Verified by:* the full suite still 52+ passed with no change to existing test
expectations; a new test loads the pre-existing `.crow-research/todo.json` shape.

**1.4** ✅ Host-owned scaffold: `Todo.scaffold_rubrics(idea)` returns the
`pass: false` / empty-evidence status document the verifier fills in, and
`Todo.apply_rubric_status(...)` accepts only `pass`, `evidence` and
`next_recommendation` from an agent, computing `overall_pass` itself.
*Verified by:* unit test — an agent write that tries to set `overall_pass: true`
with a failing rubric is corrected by the host, not believed.

**1.5** ✅ The `best` gate: `Todo.record_attempt(attempt)` updates `best` only when
`correct` is true and the score is strictly higher. Two independent gates, as in
their §3.3 — all-rubrics-pass closes the contract, a better correct score moves
`best`.
*Verified by:* unit tests for higher/equal/lower/incorrect, and for
rubric-pass-without-a-better-score.

**1.6** ✅ Conclusions validation: one paragraph, no blank lines, <= 300 words, and
a recorded gate reason from `contradicts | new_category | flips | refines | none`.
*Verified by:* unit tests for each rejection and for a valid update.

**1.7** ✅ Commit. `uv run pytest -q` green — **96 passed** (52 before, 44 new).

## Phase 2 — an executable example task  ✅ DONE (2026-10-15, 111 passed)

Evidence: `examples/tasks/circpack/{task.md, evaluate.py, program_0/src/program.py}`
plus 15 tests in `tests/test_task_circpack.py`. The baseline is a 5 x 6 grid
trimmed to 26 with one uniform radius by construction (`radius_spread` exactly
0.0), scoring **2.166666666666666**, `correct: true`. Two bugs the run caught:
`math.isclose(atol=)` is `abs_tol=`, and float noise made 26 "equal" radii differ
by 2.8e-17. A test caught a wrong assumption of mine, not a bug in the task:
`exposes_solution` is an *implementation* rubric and names no evaluator output
file, so the rubric test is type-aware — metric rubrics must name a file and key
the evaluator really writes, implementation rubrics must name the interface and be
evidenced by a run that breaks it.

**2.1** ✅ `examples/tasks/circpack/task.md` — the goal in prose, the rubrics in the
state-file shape (at least one `metric` and one `implementation` rubric), the
candidate interface, and how the evaluator is invoked.
*Verified by:* the rubric ids in `task.md` are exactly the ones `evaluate.py`
and the state scaffold use — `test_task_rubrics_are_a_task_spec_the_state_file_accepts`
feeds task.md's fence straight into `TaskSpec.from_dict` (so `require_both` is
exercised on the real bundle), `test_metric_rubrics_name_keys_the_evaluator_writes`
and `test_the_implementation_rubric_is_evidenced_by_a_run` tie each rubric to a
key or message a real run produces, and
`test_beats_program_0_is_a_strict_threshold_at_the_baseline` pins the threshold to
the executed baseline so the two cannot drift apart.

**2.2** ✅ `examples/tasks/circpack/evaluate.py` — deterministic, CPU-only, no
network, runs in seconds. Writes `metrics.json` (`combined_score`, plus the
per-circle detail) and `correct.json` (`correct`, and why not). Overlap or
out-of-bounds is `correct: false`. Same score as theirs: sum of radii.
*Verified by:* `tests/test_task_circpack.py` runs it as a subprocess on `program_0`
in a tmpdir and asserts `correct: true` and `combined_score > 0`; runs it on six
deliberately-bad candidates — overlapping, outside the square, a dishonest
`sum_of_radii`, 25 circles instead of 26, no `solution()`, and one that raises —
and asserts each is *recorded* (`correct: false`, exit code 0, the fault named in
both files) rather than crashing the run; runs it twice and asserts the two files
are byte-identical. `test_the_evaluator_is_stdlib_only_and_off_the_clock` parses
its imports and fails if it grows numpy, a socket, or any of the modules a clock
is built from (`signal`, `threading`, `multiprocessing`, `subprocess`).

**2.3** ✅ `examples/tasks/circpack/program_0/` — a grid-placement baseline,
read-only reference, scores above zero.
*Verified by:* 2.2's test — 2.166666666666666, `correct: true`, `num_circles` 26.

**2.4** ✅ Commit.

## Phase 3 — the two new roles  ✅ DONE (2026-10-15, 127 passed)

Evidence: `DRAFTER`/`WRITER` in `roles.py` and `BUILTIN` (and exported from
`crow_research`), `agents/drafter_agent.py` (patches ROLE,
PROBLEM_SOLVING_WORKFLOW, FILE_SYSTEM_GUIDELINES, CODE_QUALITY; adds TASK_SPEC),
`agents/writer_agent.py` (patches ROLE, PROBLEM_SOLVING_WORKFLOW,
FILE_SYSTEM_GUIDELINES, SEARCH; adds GROUNDED), default ports 2783/2784. 16 tests
in `tests/test_roles.py`. The render assertions are *derived* from each agent file
rather than hardcoded against it — which sections it patches, what it calls them,
how many — so they also catch the failure nothing else would: a section written
into the file and never wired into `role_prompt()`. Confirmed by mutation:
deleting `"SEARCH": SEARCH` from the writer's patches fails
`test_the_agent_file_renders_crows_prompt_edited[writer]` with "SEARCH landed in
[]". Char counts are relational (`len(rendered) > len(base)`, section count
`base + 1`, every untouched section byte-identical) because a hardcoded count is
a false red the first time crow's prompt changes upstream — and inheriting that
change is the whole reason `role_prompt` patches a template instead of freezing a
copy.

**3.1** ✅ ✅ `roles.py`: `DRAFTER` and `WRITER` in `BUILTIN`, each with a `brief` in
the same voice as the existing four. `RESEARCHER` stays file-less and parked.
*Verified by:* `resolve("drafter")` and `resolve("writer")` work, and
`resolve("writer=ws://…")` still parses an endpoint.

**3.2** ✅ `agents/drafter_agent.py` — turns a research brief into a task spec:
goal, rubrics, evaluator, `program_0`. Writes `task` into the state file. It may
write the evaluator and the baseline, because without them there is nothing to
score; it does not run experiments.
*Verified by:* the file's `system_prompt` factory renders against a real
`Config.load()` — `crow_role == "drafter"`, section count `base + 1`, the render
strictly longer than crow's own, every patched section name present in crow's
prompt, every section it did not patch byte-identical, the added section
immediately after `</IMPORTANT>`, and the remaining `template_args` (workspace,
session lookup, skills) inherited untouched.

**3.3** ✅ `agents/writer_agent.py` — turns the evidence into a grounded report:
every quantitative claim resolves to an `attempt` or a `metrics.json`, and the
claim-to-source map is part of the output. Read-only on the code.
*Verified by:* same render assertions as 3.2.

**3.4** ✅ Both files: `--http/--host/--port/--model/--config-dir/--config-file/--debug`,
stdio by default, distinct default ports that do not collide with 2771/2772/2782
(drafter 2783, writer 2784; worker 2780 and verifier 2781 were taken too).
*Verified by:* `--help` on both, asserting every flag is offered; a
spawn-and-handshake `test_the_agent_file_serves_http` per role — bind, dial over
WebSocket, `session/new`, SIGTERM, and assert the process actually exited; and
`test_no_two_agent_files_share_a_default_port`, which AST-reads the `--port`
default out of every file in `agents/` (resolving `DEFAULT_PORT` as well as a
literal) and fails on any collision with another agent or with `crow-cli acp2`.

**3.5** ✅ Commit.

## Phase 4 — the three existing contracts grow  ✅ DONE (2026-10-15, 148 passed)

Evidence: the explorer's ROLE/FINDINGS and module docstring grow the idea
contract (falsifiable hypothesis, rubrics the verifier can check unaided, at
least one of each type, stable ids, gated `conclusions`, and *no execution while
ideating*); the worker's ROLE/REPORT grow the experiment contract (append-only
`program_<idx>`, the task's evaluator and nothing else, `metrics`/`correct`
copied verbatim, an honest `stop_reason`, rubric `pass`/`evidence` left at the
scaffold, no `best` and no `conclusions`, a failing candidate recorded as a
result) plus an ATTEMPT section in its handoff shape; the verifier's ROLE/VERDICT
grow the scaffold contract (fill only `pass`/`evidence`/`next_recommendation`,
never invent a criterion, reproduce the score itself, an uncheckable rubric is
`pass` false with `UNVERIFIED`) plus a RUBRICS section in its report shape.
`workflows.py`: STATE_SCHEMA now shows the whole document, STATE_RULES names who
owns every key, and the three templates grow `{campaign}` plus one role-specific
block each — `_campaign_block`, `_experiment_block`, `_scaffold_block`,
`_idea_contract_block`, all four rendering to `""` when the list has no `task`, so
the plain pipeline's prompts are unchanged in substance. 12 tests in
`tests/test_workflows.py`; the render tests in `test_roles.py` now run over all
five agent files rather than the two new ones.

One existing test needed a fix, and the fix is worth recording: the explorer's
dispatch embeds the verifier's report verbatim, and the stub echoes its prompt,
so an echo nested in an echo nested in a third outgrew the stub's 4000-char echo
limit and pushed "Decide what happens next" past the truncation. The verifier now
has a scripted reply in that test. Nothing was asserted about its wording, the
worker still echoes so the list it was handed is visible, and the assertion the
test exists for is intact.

**4.1** ✅ ✅ `explorer_agent.py`: the `FINDINGS` section becomes an idea contract —
title, falsifiable hypothesis, ordered plan, `target_rubrics` with at least one
`metric` and one `implementation` rubric, stable snake_case ids. Read-only in the
repo apart from the state file. No execution during ideation: no Bash, no Python,
no pip, no probes. Keep the `be16d27` framing (goal first, not an auditor).
*Verified by:* render assertions; the old prose-only shape is gone from the file.

**4.2** ✅ `worker_agent.py`: append-only `program_<idx>`, never overwrite an older
one; run the task's evaluator and nothing else; write `metrics`, `correct` and
rubric evidence as an `attempt`. No exploratory execution outside the official
evaluation.
*Verified by:* render assertions; the dispatch prompt in `workflows.py` names the
evaluator and the next free `program_<idx>`.

**4.3** ✅ `verifier_agent.py`: it checks the rubrics the explorer wrote, from the
host's scaffold, and fills only `pass`, `evidence`, `next_recommendation`. It
does not invent criteria, does not add items, does not edit.
*Verified by:* render assertions; a wire test shows the scaffold in its dispatch.

**4.4** ✅ `workflows.py` prompt templates: `EXPLORER_PROMPT` grows the idea
contract and the schema; `WORKER_PROMPT` grows the evaluator, the program index
and the attempt record; `VERIFIER_PROMPT` grows the scaffold. `STATE_SCHEMA` and
`STATE_RULES` updated to match the extended document.
*Verified by:* the existing three pipeline tests still pass unchanged in intent
(one needed a scripted verifier reply — see the phase note); `.format()` on each
template with a realistic campaign state raises no `KeyError` and carries the
campaign half, and with a task-less state carries none of it. Plus the test that
pays for itself: `test_the_schema_is_the_document_key_for_key` parses
`STATE_SCHEMA` as JSON and compares its keys, in order, against `Todo().to_dict()`,
`Item.to_dict()`, `Idea.to_dict()` and `Conclusions.to_dict()`, and
`test_the_schema_omits_exactly_the_keys_an_agent_may_not_write` asserts the
attempt example is `Attempt.to_dict()` minus `overall_pass`. A schema that drifts
from the loader teaches agents a shape the file rejects, and it fails silently —
the write does not parse and the round is thrown away.

**4.5** ✅ Commit.

## Phase 5 — the `scientist` workflow  ✅ DONE (2026-10-15, 158 passed)

Evidence: `pipeline`'s body moved wholesale into a private
`_loop(worker, verifier, explorer, todo, *, max_review, announce, state_path,
drafter=None, writer=None)`; `pipeline(...)` and `scientist(drafter, worker,
verifier, explorer, writer, todo, *, announce, state_path)` are thin doors onto
it, so there is one loop and no duplicated step. `scientist` is exported from
`crow_research`. Three host-side campaign steps run only when the file has a
`task`, so a plain list's prompts and behaviour are untouched: the drafter gets
one turn before the `while True` and only when `state.task is None` (if it still
wrote no task, a `warning` notice says the run continues as a list — no retry);
`state.repair_scaffolds()` runs before every verifier dispatch;
`state.recompute_best()` runs after every verifier reload and announces
`best X -> Y` when the incumbent moved; the writer gets a turn when a round
signed off on a complete list and the explorer added nothing, and the loop
continues anyway. New `Todo` methods: `recompute_best()` (resets and replays
every attempt through the same gate `apply_rubric_status` uses — idempotent, and
it corrects an incumbent an agent wrote) and `repair_scaffolds()` (puts each
attempt's rubric *ids* back to its idea's, returning the missing ones as
`pass: false` with empty evidence and dropping invented ones; the *values* stay
the verifier's). 10 new tests in `tests/test_campaign.py`, 54 in that file.
5.2/5.3/5.4 are unit-tested here and wire-tested in `tests/test_scientist.py`
(Phase 7), which is where their stated *Verified by* lives.

**5.1** ✅ `workflows.scientist(drafter, worker, verifier, explorer, writer, todo, *,
announce=True, state_path=None) -> Workflow`. Drafter dispatched once, only when
the file has no `task`. Then the existing perpetual loop. Then, when a round
signs off on a complete list **and** the explorer adds nothing, the writer is
dispatched to publish what the campaign has — and the round continues anyway.
*Verified by:* the function is exported from `crow_research`; its docstring says
plainly that nothing in it is on a clock.

**5.2** ✅ (host side in `_loop`; wire test
`test_a_rejected_idea_is_a_notice_and_the_round_after_it_still_happens`)
A rejected idea is a notice, not a retry counter: the orchestrator
validates what the explorer wrote, and on failure emits a notice naming the
problem and lets the next round ask again.
*Verified by:* a stub explorer scripted to write a rubric with a bad `type`
produces the notice and the loop keeps going.

**5.3** ✅ (host side in `_loop` — `repair_scaffolds` before the dispatch,
`recompute_best` after the reload; wire tests
`test_the_scaffold_is_repaired_and_the_aggregates_are_not_believed` and
`test_the_best_gate_banks_the_number_the_evaluator_wrote`)
The orchestrator pre-creates the rubric scaffold before dispatching the
verifier, and computes `overall_pass` and `best` itself afterwards.
*Verified by:* wire test — the scaffold is in the verifier's dispatch, and a stub
verifier that lies about `overall_pass` is corrected in the file.

**5.4** ✅ (`Conclusions.validate` runs in the loader, so an over-long paragraph
is a document that does not parse, which `_reload` turns into an error notice
plus the last good copy restored — restore now happens *before* the notice, so
the notice is true when read; wire test
`test_an_over_long_conclusions_is_refused_and_the_paragraph_survives`)
Conclusions are gated on the wire too: a round that writes an over-long,
multi-paragraph, or trigger-less `conclusions` is rejected with a notice naming
the problem, and the loop keeps going. Same shape as 5.2 — a rejection is a
notice, never a retry counter and never a stop.
*Verified by:* a stub scripted to write a 400-word conclusions string produces the
notice, the file still holds the previous conclusions, and the next round is
dispatched.

**5.5** ✅ Commit.

## Phase 6 — wiring  ✅ DONE (2026-10-15, 160 passed)

Evidence: `cli.py` grows `CASTS` — the one table saying which roles each stateful
workflow needs — and `_cast(roles, workflow)`, which raises a `SystemExit` naming
the roles a `--workflow` was not given; `build_workflow` gains a `scientist`
branch, `state_path(args.cwd)` is hoisted so both stateful workflows pin the
file the same way, and `--workflow` offers `scientist` with a help string. No new
flag: a cast is still `--role name[=endpoint]` repeated. Verified by
`serve --help` (all four workflows listed, `scientist` described), a dry
`build_workflow` over five stub-pointed specs, and the missing-role exit
(`--workflow scientist needs a role for each of: drafter, explorer, writer`).
`agents/orchestrator_agent.py` now builds `workflows.scientist(...)` from five
`agent_role(...)` calls — same flags, same defaults, same `--name`/`--title`, so
the stdio entry in `~/.agents/crow/settings.json` is unchanged — and its module
docstring's sequence diagram grows the drafter and the writer. Two new tests in
`test_roles.py`: a stdio spawn-and-handshake of the agent file (the `--http` one
already existed in `test_http.py`), and `build()` called for real with both
workflow constructors watched, asserting the cast is exactly `CASTS["scientist"]`
and that `pipeline` is never reached. Mutation-checked: pointing `build()` back at
`workflows.pipeline` fails it.

**6.1** ✅ `cli.py`: `--workflow scientist` builds the five-role cast from `--role`
specs, defaulting each to its own agent file.
*Verified by:* `uv run crow-research serve --workflow scientist --help` and a dry
construction with all five roles pointed at the stub.

**6.2** ✅ `agents/orchestrator_agent.py`: the scientist cast, same flags as today.
*Verified by:* spawn-and-handshake over stdio and over `--http`.

**6.3** ✅ Commit.

## Phase 7 — tests over the real wire  ✅ DONE (2026-10-15, 167 passed)

Evidence: `tests/test_scientist.py`, 7 tests, all of them driving the five stubs
through `crow-research serve --workflow scientist` as a spawned child — the same
real wire `test_pipeline.py` uses (stdio pipes; `test_http.py` already holds the
socket variant of this loop to the same contract, and a second socket test would
be testing hypercorn twice). `serve_scientist(cwd)` builds the argv, `campaign(cwd,
scripts)` builds the peer with the stubs' shared env, and `seeded_campaign(cwd, *,
idea, done)` writes a file that already has a task on it. `DISPATCH` is this
cast's own regex — the drafter's notice carries no round number, because it is not
part of a round — and `test_pipeline.dispatches` grew a `pattern` parameter so the
counting is shared rather than copied.

Six mutations, each confirmed to fail the test that exists for it: no writer turn,
no `recompute_best`, no `repair_scaffolds`, an unconditional drafter, no
`_experiment_block`, and an unconditional writer.

Two things the tests forced out of the product, both worth keeping:

* `_reload` restored the last good copy *after* announcing that it had. The
  notice says "the last good copy is back in place", so an observer acting on it
  was acting on a file that had not been written yet. Save, then announce.
* A rejection test cannot read the file after the run. The explorer writes the
  same bad document every round, so the only moment the file holds the host's
  restored copy is the moment a write is refused — the two rejection tests read it
  inside the predicate, with `STUB_DELAY` widening the window past the poll.

**7.1** ✅ `tests/test_scientist.py`: the stub cast driven through
`workflows.scientist` over a real socket. Assert the dispatch order
`Drafter → Worker → Verifier → Explorer → Writer`, the role stamps in `_meta`,
the notices, `stop_reason == "cancelled"`, and the state file afterwards.
*Verified by:* the test passes; it fails if any role is dispatched out of order.
`test_the_campaign_casts_five_roles_in_order_and_never_stops` asserts
`roles_in_order() == list(CAST)` off the `_meta` stamps, counts one Drafter /
Worker / Verifier and >= 2 Explorers off the notices, and waits for round 2's
explorer precisely because that is what proves round 1's writer finished.

**7.2** ✅ A resume test: a file with a `task`, a signed-off verdict and a complete
list goes straight to the explorer, with no drafter turn.
*Verified by:* the test passes and asserts `Drafter` was never dispatched —
`test_a_resumed_campaign_never_dispatches_the_drafter`, which also asserts the
Worker and the Verifier were not, and that nothing was seeded.

**7.3** ✅ The example task's evaluator is exercised by a real worker-shaped stub
that writes an attempt, and the host's `best` gate is asserted from the file.
*Verified by:* the test passes. `test_the_best_gate_banks_the_number_the_evaluator_wrote`
writes a real candidate, runs the real `evaluate.py` on it as a subprocess twice
(the candidate and the baseline), and hands the worker stub the bytes
`metrics.json` held; the file afterwards holds `Best(program_1, <the evaluator's
own float>, 1)` and the stream carries `best (none) -> program_1 (…)`. The same
test asserts the worker's dispatch named the idea, the next free `program_<idx>`
and the evaluator.

**7.4** ✅ Commit. Full suite green — **167 passed** — count recorded in README.

Also closed here, because their *Verified by* was a wire test in this file:
**5.2** (`test_a_rejected_idea_is_a_notice_and_the_round_after_it_still_happens`),
**5.3** (`test_the_scaffold_is_repaired_and_the_aggregates_are_not_believed` — a
worker that leaves a rubric out and invents one, a verifier that asserts
`overall_pass: true` over a rubric it just failed and writes an incumbent of its
own, and the host taking both back with a notice), **5.4**
(`test_an_over_long_conclusions_is_refused_and_the_paragraph_survives`), plus the
explorer-contract handover
(`test_an_idea_the_explorer_writes_lands_in_the_file_and_reaches_the_worker`).

## Phase 8 — docs  ✅ DONE (2026-10-16, 168 passed)

**8.1** ✅ README: the mapping table, "what we took", "what we refused and why",
how to run a campaign against `examples/tasks/circpack`, and the new test count.
*Verified by:* every command in the new prose actually runs, copy-pasted.

Evidence: four new sections and six corrected ones. New — `## The campaign` (the
five-role sequence, its own ASCII diagram with the two loops on separate columns,
`repair_scaffolds()` and `recompute_best()` as the host's two steps, and why
`overall_pass` is a property); `## Redoing The Agentic Scientist` (the §3.1–§3.5
mapping table, `### What we took` — eight items, `### What we refused, and why` —
five, plus a closing paragraph naming the three that are out of scope rather than
refused); `### Running a campaign against circpack`; and two new bullets in
`## What is tested`. Corrected — the contract section now shows the whole document
instead of three keys and its `| who | writes |` table names the drafter, the
writer and the host; the prompt table gains `drafter_agent.py` and
`writer_agent.py` and **all five char counts were re-measured**, because Phase 4
had grown them and the README still carried the pre-campaign numbers (worker
21179 → 23362, verifier 18607 → 20767, explorer 20105 → 22160); the module table
gains `campaign.py` and `examples/tasks/circpack/` and its `todo.py`,
`workflows.py`, `roles.py` and `cli.py` rows now describe what is there; the run
table gains runs 6, 7 and 8 out of their logs; `## Next` gains a second task,
parallel workers and figures; "the three roles share one transcript" is five.

Every command was run. The `/tmp/circpack-run` setup block ran copy-pasted — and
the first copy committed two `.pyc` files, so the block grew an
`echo "__pycache__/" > .gitignore` line and a sentence saying why (the evaluator
imports each candidate in-process). `crow-research serve --workflow scientist
--role worker` prints exactly the `_cast` line the prose quotes. The
`scientist(...)` snippet was executed. The `repl_client` campaign command was run
verbatim against `/tmp/circpack-run` and reached `Session: orch-1 (… protocol 2)`,
`⚠ todo seeded with 1 items` and `⚠ Drafter` before being cancelled.

**The verification criterion found a product bug.** The prose claims Ctrl-C sends
`session/cancel`; checking the negative half of the absolute-path claim meant
running `repl_client` and signalling it, and it exited `-2` with a traceback
instead of cancelling. `asyncio.Runner` installs its own SIGINT handler whose
first press *cancels the main task*, so the `except KeyboardInterrupt` around
`client.send(prompt)` was dead code and the cancel was never sent — the loop's
only exit did not work. Runs 5–8 never caught it because they were all ended by
the since-deleted `--cancel-after`, which called `client.cancel()` directly.
Fixed in `repl_client.py` by catching `asyncio.CancelledError` alongside
`KeyboardInterrupt` in the `-p` path and in both of the REPL's, with a comment
saying why. Pinned by `test_ctrl_c_in_the_client_is_session_cancel` in
`tests/test_orchestrator.py`: the console script as a subprocess in its own
process group, one `SIGINT`, and `stop_reason: cancelled` plus exit 0 asserted off
what it printed. Mutation-checked — reverting the exception tuple gives `rc -2`
and a red test. 168 passed.

**8.2** ✅ Commit.

## Phase 9 — a real run  ✅ DONE (2026-10-16, 176 passed)

**9.1** ✅ Start a campaign on local `glm-5.3-flash` against
`examples/tasks/circpack/` through `repl_client.py`, in a scratch cwd outside the
repo. Let it run. End it by hand with `session/cancel` — nothing else ends it.

Run 9, in `/tmp/campaign1` (circpack copied in, git init'd, `__pycache__`
ignored). Two sessions, 5h48m of model time in total: the first ended at 234.8
minutes, the second at 112.8. One SIGINT to the client, `rc 0`, `cancelling...`
then `stop_reason: cancelled`, 4.0 seconds from signal to exit — the Phase 8 fix,
in the wild, on a real campaign. (The first session was spawned before that fix
landed and so ended `rc -2` with a traceback and no cancel sent; the pre-fix code
was still in its memory. Same bug, observed twice, once fixed.)

The state file afterwards, every clause of the criterion:

* `task` drafted — the drafter's turn, 24 minutes, commit `9cb8553`. It ran the
  evaluator on the baseline twice and byte-diffed the results, threw three bad
  candidates at it, and wrote `task.md`'s three rubrics verbatim
  (`exposes_solution` implementation, `correct_packing` metric,
  `beats_program_0` metric), `evaluator: circpack/evaluate.py`,
  `program0: circpack/program_0/src/program.py`. It left `items`, `ideas`,
  `attempts`, `best` and `conclusions` alone.
* at least one attempt with metrics — attempt 1, `idea_id` null,
  `program_1/src/program.py`, `combined_score` **2.6359830795664423** against the
  baseline's 2.166666666666666, `correct` true, `stop_reason` `rubric_pass`, and
  all three rubrics passed with commands and a sha256 in the evidence.
* at least one idea with rubrics — idea 1, `record-beating refinement`,
  `added_by: explorer`, three rubrics: `score_beats_record` (metric),
  `lp_in_loop` and `deterministic_output` (implementation), so
  `_rubrics(require_both=True)` is satisfied by an idea a model wrote unprompted.
  Plus items 2 and 3, `open`, `added_by: explorer`, and a first `conclusions`
  paragraph at trigger `new_category`, 149 words, one paragraph.
* `best` set only by the gate — the host announced
  `best (none) -> program_1/src/program.py (2.6359830795664423) — the gate is the
  host's: every rubric passed, the candidate is correct, and it strictly beats
  the incumbent` on the reload *after* the verifier's turn. Neither the worker
  nor the verifier wrote it, and `recompute_best()` on the worker's unverified
  record returned `None`, because `overall_pass` was still false.

**The run found a bug in the contract, and the contract's own recovery is what
made it survivable.** The worker recorded its attempt with `"idea_id": null` —
correct, since round 1 has no idea, the explorer writing `ideas` only after the
worker has run — and the host refused the document:
`todo.json does not parse — an attempt's idea_id is an integer, got None — the
last good copy is back in place; that agent's write is lost`. `Attempt.idea_id`
was typed `int` and `_validate` said "an attempt without an idea is evidence for
nothing", so the loop's own ordering guaranteed the first attempt would always be
discarded and `best` could never move. Fixed in `b781094`: `idea_id` is
`Optional[int]`; `_validate` skips the membership check for null and rewords the
message for a *named* idea that was never proposed; the new
`Todo._expected_rubric_ids` grades an idealess attempt on the task's own rubrics,
so `repair_scaffolds` still polices it against the drafter's contract instead of
waving it through; `record_attempt(None, …)` scaffolds from `task.rubrics`;
`render_campaign` prints `idea none`; and `EXPERIMENT`, `SCAFFOLD` and
`STATE_RULES` all say so, because a prompt that only shows the idea case is how a
worker's first record gets refused on load. 8 new tests, 176 passing, and all 8
mutations of the fix go red.

Because the worker had committed its record as `ef106ee`, the resume was
`git show ef106ee:.crow-research/todo.json` — the sidecar git doing exactly what
it was taken on for. The fixed loader accepted the worker's own write unchanged,
`repair_scaffolds()` found nothing to repair, and the run went straight to
`round 1 · Verifier`: no drafter, because the file has a `task`, and no worker,
because nothing is pending. That is the documented resume path, exercised on a
real campaign for the first time.

**9.2** ✅ Write the evidence into README's run table: rounds, ideas, attempts,
scores, whether the rubric gate held, what the model got wrong.

Run 9 is a row in the table, and the intro now says nine runs with 6 to 9 all
resumptions of the same file. Every number in it was read back out of
`/tmp/campaign1/.crow-research/todo.json`, the two run logs, or the scratch git
history after the cancel: the score, the baseline, the 21,298-byte 619-line
candidate whose only import is `math` (checked directly, not taken from the
verifier's word), the 99-word `verdict_note`, the 149-word `conclusions`, the
three rubric ids and their types, the commit hashes, and the per-role timings
taken from the poll log's elapsed stamps. What the model got wrong is in the row
too: the worker had a candidate beating the baseline from about the 2h35m mark —
its own generated seeds peak near 2.42 — and kept improving a scratch optimizer
at `.crow-research/dev/pack_dev.py` instead of filing it, and the score it
finally filed comes from a warm start transcribed off the web rather than from
its search, which it disclosed in the module docstring, the attempt note and its
report, and which the verifier then confirmed digit for digit against
minmaxarena's published data. The circpack subsection's guess at "a `results/`
directory per evaluation" is replaced with what happened: the worker scored into
`--results-dir program_1`, so each candidate carries its own evidence beside its
source, and the verifier reproduced into `/tmp/verify1` and `/tmp/verify2`
outside the campaign entirely. Test counts moved 168 → 176, `test_campaign` 54 →
61, `test_workflows` 12 → 13, all re-measured with `--collect-only`.

One more thing worth keeping: the verifier corrupted the state file's JSON three
times with a stray newline inside a string value, caught it from its own `edit`
diff each time, and repaired it before the host ever reloaded — so the host never
saw a document that did not parse, and the notice path that run 9's first session
exercised stayed exercised only once.

**9.3** ✅ Commit, then `goal_done`.
