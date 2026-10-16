"""Who the agents in a workflow are.

A :class:`Role` is a name, an endpoint, and the text that turns the previous
agent's report into this one's assignment. It is deliberately *not* where an
agent's character lives.

Character lives in the agent file: ``agents/verifier_agent.py`` is crow's v2
agent with five sections of crow's own system prompt surgically edited (see
:mod:`crow_research.prompts`), and a client spawns it like any other ACP agent.
What is left for a role to say is the one thing a system prompt cannot: where
this agent sits in the sequence, and what the report it just received is *for*.
That is the ``brief`` — a sentence of handoff framing, not a job description.

Splitting it this way is what makes the cast configurable from a command line.
``--role verifier="python agents/verifier_agent.py"`` re-points a role without
touching a prompt, and an agent file can be driven directly by a human who has
never heard of this orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .transport import Endpoint, Stdio, parse_endpoint

#: What the next agent is told, given what the last one did. ``{task}`` is the
#: original request, ``{report}`` the previous agent's own words.
DEFAULT_HANDOFF = (
    "You are the {role} in a sequence of agents working one task.\n"
    "Original task:\n{task}\n\n"
    "The {previous} agent finished and reported:\n{report}\n\n"
    "{brief}"
)

#: The default cast. All four start out as the same plain crow agent — a role
#: with no agent file of its own is still a working agent, it just has nobody's
#: edited prompt behind it — and any of them can be pointed at a real one with
#: ``--role name=endpoint``, which is what ``agents/orchestrator_agent.py`` does.
CROW_ACP2 = Stdio(("crow-cli", "acp2"))

@dataclass(frozen=True)
class Role:
    """One specialized agent a workflow can hand work to."""

    name: str
    brief: str = ""
    endpoint: Endpoint = CROW_ACP2
    title: Optional[str] = None
    model: Optional[str] = None
    handoff: str = DEFAULT_HANDOFF
    env: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.title or self.name

    def assignment(self, *, task: str, report: Optional[str] = None, previous: str = "") -> str:
        """The prompt text for this role: the task, or the handoff of a report."""
        if report is None:
            return f"{self.brief}\n\n{task}".strip() if self.brief else task
        return self.handoff.format(
            role=self.label, task=task, report=report, previous=previous or "previous", brief=self.brief
        ).strip()


WORKER = Role(
    name="worker",
    title="Worker",
    brief=(
        "You are the first agent in the sequence. A verifier that never saw this "
        "work is next, and it will read your report and try to break every claim in it."
    ),
)
VERIFIER = Role(
    name="verifier",
    title="Verifier",
    brief=(
        "The report above is a set of claims, not a description of the task. Those "
        "claims are your checklist, and your findings are the next agent's starting point."
    ),
)
EXPLORER = Role(
    name="explorer",
    title="Explorer",
    brief=(
        "The reports above are what this round produced. Your job is the next one: what "
        "the user asked for, and what this package needs next to deliver it."
    ),
)
#: No agent file yet — cast it against an endpoint of your own
#: (``--role researcher="crow-cli acp2"``) or write ``agents/researcher_agent.py``.
RESEARCHER = Role(
    name="researcher",
    title="Researcher",
    brief=(
        "The reports above are one implementation of one idea. Your job is the idea: "
        "which problems behind it are worth attacking next."
    ),
)

BUILTIN: dict[str, Role] = {r.name: r for r in (WORKER, VERIFIER, EXPLORER, RESEARCHER)}


def resolve(spec: str, *, cwd: Optional[str] = None) -> Role:
    """``name`` -> a built-in role; ``name=endpoint`` -> that role, pointed there.

    The endpoint half is what makes a role configurable from a command line
    without inventing a config file: ``verifier=ws://127.0.0.1:2771/acp`` sends
    the verifier to an agent that is already running.
    """
    name, sep, endpoint = spec.partition("=")
    name = name.strip()
    role = BUILTIN.get(name)
    if role is None:
        role = Role(name=name, title=name.title())
    if not sep:
        return role
    target = parse_endpoint(endpoint, cwd=cwd)
    if isinstance(target, Stdio) and role.env:
        target = Stdio(target.argv, env={**role.env, **target.env}, cwd=target.cwd)
    return Role(
        name=role.name,
        brief=role.brief,
        endpoint=target,
        title=role.title,
        model=role.model,
        handoff=role.handoff,
        env=role.env,
    )
