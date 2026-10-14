#!/usr/bin/env python
"""The worker agent — one file, and a client spawns it like any other ACP agent.

    python agents/worker_agent.py                    # stdio (what a client spawns)
    python agents/worker_agent.py --model glm-5.3    # same, on a chosen model

The lightest edit of the three: crow's prompt with ``<ROLE>`` reframed and one
``<REPORT>`` section added. That is deliberate. The worker's job *is* crow's job
— explore, implement, test, commit — and every byte of the harness detail it
already carries is what makes it good at that. What it needs from us is not a
new character but an awareness that it is one step in a sequence: a verifier
that never saw the work is about to read its final message and try to break
everything it claims.

So the two edits are the ones that change what the agent does *next*: who it is
working for, and what shape its last message has to be.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from crow_cli.agent2.main import agent_run

from crow_research.prompts import role_prompt

ROLE = """
* Your primary role is to DO the work: explore, implement, test, and commit it. Be thorough, methodical, and prioritize quality over speed.
* You are one step in a sequence of agents. A verifier that never saw your work reads your final message and tries to break every claim in it, so a claim you did not check is a defect you are handing downstream.
* If you are asked a question ("why is X happening"), don't try to fix the problem. Just give an answer to the question.
* For proper nouns you don't recognize: search first, flap gums later.
"""

REPORT = """
Your final message is a HANDOFF, not a summary for a human who was watching.
It is read by an agent that has no memory of your work, no access to your
transcript, and no way to ask you a follow-up: whatever is not in that message
did not happen, as far as the rest of the sequence is concerned.

```
DONE
- <what you changed, file by file, in the order it matters>

VERIFIED
- <the exact command you ran> — <what it returned>

NOT DONE
- <what you left, and why — including anything you were unsure enough to skip>

COMMIT
- <hash and subject>, or "uncommitted: <why>"
```

Rules that make the handoff trustworthy:

* Every line under VERIFIED carries the command that produced it. "Tests pass"
  is a claim; "`uv run pytest -q` -> 3 passed" is evidence the next agent can
  reproduce.
* NOT DONE is not a failure, it is the most useful section you write. An
  omitted caveat becomes the verifier's finding and then the next worker's bug.
* Commit before you report. The verifier reads git, and uncommitted work reads
  as work not done.
* Call no tools after you start writing the handoff. It is the last thing you say.
"""

#: crow's prompt, edited: one section replaced, one added after <IMPORTANT>.
system_prompt = role_prompt(
    patches={"ROLE": ROLE},
    extra=[("REPORT", REPORT, "IMPORTANT")],
    role="worker",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP + WebSocket instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2780)
    parser.add_argument("--model", default=None)
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--config-file", type=Path, default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        agent_run(
            system_prompt=system_prompt,
            http=args.http,
            host=args.host,
            port=args.port,
            model=args.model,
            config_dir=args.config_dir,
            config_file=args.config_file,
            debug=args.debug,
        )
    )


if __name__ == "__main__":
    main()
