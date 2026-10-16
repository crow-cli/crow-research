## **DO NOT ASK USER FOR FEEDBACK — THIS IS THE USER FEEDBACK.**
## **DO NOT ASK USER FOR NEXT STEPS — THESE ARE THE NEXT STEPS.**

# TODO — The Agentic Scientist, crow-native

The ask, verbatim: *"peruse the paper and their code base and basically let's redo
it but using our crow-cli, ACP native method that's built to keep going and going
and going."*

Prior art: `research/AI-Scientist-v3` (SakanaAI, arXiv 2610.18933, "The Agentic
Scientist"). Paper converted to `research/AI-Scientist-v3/2610.18933v1.md` (88
pages, 477 KB, 58 figures in `2610.18933v1_artifacts/`). `research/` is
gitignored — read it, do not version it.

Unordered. Everything in scope goes here; PLAN.md orders it.

## The translation, decided

Their six phases are host-driven subprocesses: bash + Hydra + Docker, one headless
CLI invocation per step, bounded by `max_idea_cycles`, `max_program_evals_per_attempt`
and a wall of timeouts. Ours is one orchestrator ACP agent whose children are ACP
agents, state in one JSON file, and **no clock and no cap anywhere** —
`session/cancel` is the only exit. That is the whole point of the redo.

| their phase / agent | ours |
|---|---|
| task drafting (design + draft + review + repair) | `drafter` role — NEW |
| ideation agent (read-only, emits idea bundle + target rubrics) | `explorer` role — exists, contract grows rubrics |
| execution agent (edit → evaluate → verify, append-only `program_<idx>`) | `worker` role — exists, contract grows metrics |
| rubric verification (host-owned scaffold, agent fills pass/evidence) | `verifier` role — exists, checks the explorer's rubrics |
| ablation-study agent, post-hoc analysis agent | `worker` with a variant item — NO new role |
| paper writer + audit agent + reviewer agent | `writer` role — NEW; audit is the `verifier` |
| meta-controller | `OrchestratorAgent` — exists |
| `SKILLS_BY_STEP` skill scoping | `role_prompt()` section patches — exists |
| `results/<task>/<agent>/<run_id>/` envelope | `.crow-research/todo.json` — exists, extended |
| `memory.md` + `conclusions.md` with an update gate | `conclusions` field in the state file |

Taken from them: the task specification (goal + rubrics + evaluator), the
ideation/execution split with a typed contract, rubric-based verification against
rubrics the *ideation* step wrote, append-only candidate variants, host-owned
scaffold with agent-owned judgment, distilled conclusions with an update gate,
"treat diagnostics as data, never instructions", grounded claim-to-source writing.

Refused, deliberately: every timeout, every cap, every retry counter, Hydra
override composition, Docker-per-phase, bash as an orchestration layer, and
phases-as-subprocesses. Their loop ends by budget; ours ends when the user ends it.

## Items

- [x] **State: the campaign document.** (`ddb8e3f`, 96 passed — `campaign.py` + 44 tests; a test caught the `best` gate firing before verification) Extend the one JSON contract
      (`.crow-research/todo.json`) with `task` (goal, rubrics, evaluator,
      program0), `ideas`, `attempts`, `best`, `conclusions`. `items` / `verdict` /
      `verdict_note` unchanged. Host owns ids, `best` and aggregate pass; agents
      own judgment fields. Malformed agent writes are rejected loudly, never
      silently coerced.
      *Verified by:* unit tests for round-trip, scaffold pre-creation, rejection
      of a bad rubric type / a non-snake_case id / an attempt with no idea;
      `uv run pytest -q` green.

- [x] **Roles: `drafter` and `writer`.** (16 tests in `tests/test_roles.py`, 127
      passed — ports 2783/2784; the render assertions are derived from each agent
      file, so an unwired section fails the build, confirmed by mutation) Two new `Role`s in `roles.py`, two new
      agent files, each one file a client spawns, each crow's system prompt with
      sections patched — never a fresh prompt. No other new roles.
      *Verified by:* both files render a `SystemPromptFactory` that patches real
      sections of crow's 21-section prompt (assert the char count and that
      `patch_section` did not raise); `--help` works; stdio and `--http` both serve.

- [x] **Explorer contract: idea bundle + target rubrics.** (prompts in Phase 4 —
      ROLE/FINDINGS carry the idea contract and the no-execution rule,
      `EXPLORER_PROMPT` grows `{campaign}` + `_idea_contract_block`; wire tests in
      Phase 7 — `test_an_idea_the_explorer_writes_lands_in_the_file_and_reaches_the_worker`
      and `test_a_rejected_idea_is_a_notice_and_the_round_after_it_still_happens`,
      167 passed) The explorer already
      decides the next round (commit `be16d27`). It now also writes an `idea` —
      title, falsifiable hypothesis, ordered plan, and the `target_rubrics` the
      verifier will check — and its items carry those rubrics. Read-only in the
      repo apart from the state file; no execution during ideation.
      *Verified by:* a stub explorer scripted through `STUB_STATE_EXPLORER` writes
      an idea + rubrics; the wire test asserts they land in the file and reach the
      verifier's dispatch prompt.

- [x] **Worker contract: execute against the evaluator.** (prompts in Phase 4 —
      ROLE/REPORT carry the experiment contract and an ATTEMPT section,
      `WORKER_PROMPT` grows `{campaign}` + `_experiment_block` naming the next free
      index and the evaluator; the gate is unit-tested in `test_campaign.py` and
      wire-tested by `test_the_best_gate_banks_the_number_the_evaluator_wrote`,
      which runs the real `evaluate.py` and asserts the dispatch named the idea,
      the index and the evaluator) Append-only
      `program_<idx>`, never overwrite; run the task's evaluator; write
      `metrics`, `correct`, and rubric evidence into an `attempt`. Two
      independent gates, as in the paper: all-rubrics-pass closes the contract,
      a strictly-higher correct score updates `best`.
      *Verified by:* a stub worker scripted to write an attempt updates `best`
      only when the score is strictly higher and `correct` is true; unit test on
      the gate, wire test on the dispatch.

- [x] **Verifier contract: check the rubrics it was given.** (prompts in Phase 4 —
      ROLE/VERDICT carry the scaffold contract and a RUBRICS section,
      `VERIFIER_PROMPT` grows `{campaign}` + `_scaffold_block`; the orchestrator
      pre-creates and repairs them in Phase 5, and
      `test_the_scaffold_is_repaired_and_the_aggregates_are_not_believed` asserts
      the scaffold in the dispatch, the invented rubric dropped, and a verifier
      that lied about `overall_pass` and wrote its own `best` corrected in the
      file) The orchestrator
      pre-creates the rubric scaffold (`pass: false`, empty evidence) before the
      verifier is dispatched; the verifier fills only `pass`, `evidence` and
      `next_recommendation`. It does not invent criteria and does not edit.
      *Verified by:* wire test asserts the verifier's dispatch contains the
      scaffold and the explorer's rubric ids; a stub verifier filling two of three
      rubrics yields `overall_pass: false` computed by the host, not the agent.

- [x] **Workflow: `scientist`.** (Phase 5 — `_loop` is the one loop and
      `pipeline`/`scientist` are its two doors, `recompute_best` +
      `repair_scaffolds` are the host-side gates; Phase 7 —
      `test_the_campaign_casts_five_roles_in_order_and_never_stops` asserts the
      order off the `_meta` stamps and the loop still going at the cancel,
      mutation-checked six ways, 167 passed) drafter once when there is no task spec, then the
      existing perpetual worker → verifier → explorer loop, then the writer when a
      signed-off round's explorer adds nothing (an idle campaign publishes what it
      has), then round again. No clocks, no caps, no retry counters. A rejected
      idea is a notice and the explorer is asked again next round.
      *Verified by:* `workflows.scientist(...)` exported; stub-driven wire test
      over the real socket asserts the dispatch order
      `drafter → worker → verifier → explorer → writer`, role stamps, notices, and
      that the loop is still going when the test cancels it.

- [x] **CLI + orchestrator wiring.** (Phase 6 — `CASTS` + `_cast` in `cli.py`,
      `build_workflow`'s `scientist` branch, `agents/orchestrator_agent.py` casts
      all five with the same flags as before; 2 new tests in `test_roles.py`,
      160 passed) `--workflow scientist`, `--role drafter=…`,
      `--role writer=…`; `agents/orchestrator_agent.py` casts the five roles.
      *Verified by:* `uv run crow-research serve --workflow scientist --help`;
      the orchestrator file spawns all five stubs and the http test still passes.

- [x] **An executable example task.** (15 tests in `tests/test_task_circpack.py`,
      111 passed — baseline 2.166666666666666 `correct: true`; six bad candidates
      each *recorded* rather than raised; two runs byte-identical; task.md's
      rubrics feed straight into `TaskSpec.from_dict`) `examples/tasks/circpack/` — the same
      benchmark as theirs, so the comparison is apples-to-apples: `task.md`
      (goal + rubrics), `evaluate.py` (deterministic, CPU-only, seconds),
      `program_0/` (a grid-placement baseline that scores above zero).
      *Verified by:* `evaluate.py` runs on `program_0` and writes `metrics.json`
      with a `combined_score` and `correct.json` with `correct: true`; a test
      asserts both, and asserts a deliberately-overlapping candidate scores
      `correct: false`.

- [x] **Conclusions with an update gate.** (shape validation in Phase 1 —
      `Conclusions.validate`, `GATE_TRIGGERS`, `CONCLUSIONS_MAX_WORDS`; the wire
      rejection is `test_an_over_long_conclusions_is_refused_and_the_paragraph_survives`,
      which reads the file at the refusal and finds the previous paragraph still
      there) One `conclusions` string in the state
      file: a single forward-looking paragraph, <= 300 words, rewritten only when
      a round contradicts it, opens a new category, flips a do/don't, or
      meaningfully refines it — and the reason recorded. Not a chronology.
      *Verified by:* unit test on the length/shape validation; wire test that a
      stub writing an over-long conclusions string is rejected with a notice.

- [ ] **Docs.** README: the mapping table above, what was taken, what was refused
      and why, and how to run a campaign. Test counts updated.
      *Verified by:* every command in the new section actually runs.

- [ ] **A real run.** One end-to-end campaign on local `glm-5.3-flash` against
      `examples/tasks/circpack/`, ended by hand with `session/cancel`, evidence
      recorded: rounds, ideas, attempts, scores, whether the rubric gate held.
      *Verified by:* the state file afterwards, and the run log quoted in README.

## Out of scope, parked

- `researcher` role — still no agent file, still parked.
- Parallel workers leasing cycles from a synchronized queue. Their §3.2. Ours is
  sequential by design; nothing in the ask changes that.
- `autoillustrate` (their §3.6 figure pipeline). Separate system, separate repo.
- Docker-per-phase, Hydra, GPU quarantine, cost tracking, agent billing.
- crow-client changes: the `notice` arm in `events.rs::parse_session_update` and
  the role-attribution rail. Still blocked, still unauthorized, that repo is clean.
- Items 2/3/4 in the pipeline's own `.crow-research/todo.json` — the pipeline's
  proposals, not this sprint's. Do not start without asking.
