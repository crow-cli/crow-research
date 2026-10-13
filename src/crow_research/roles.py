"""Who the agents in a workflow are.

A :class:`Role` is a name, an endpoint, and the two pieces of text that make an
agent *that* agent rather than a generic one: a ``brief`` prepended to its first
prompt (its specialization, since we do not own the child's system prompt) and a
``handoff`` template that turns the previous agent's transcript into the next
agent's assignment.

Specialization lives in prompts, not in code, on purpose: the same orchestrator
drives a worker, a verifier, an explorer and a researcher, and the difference
between them is a paragraph and a model choice — nothing that deserves its own
class.
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

#: The default cast. All four are the same binary by default — the character
#: comes from the brief — and any of them can be pointed elsewhere with
#: ``--role name=endpoint``.
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
        )


WORKER = Role(
    name="worker",
    title="Worker",
    brief=(
        "You are the WORKER. Do the task completely and leave the repository in a "
        "state you would defend. Report what you changed and what you verified."
    ),
)
VERIFIER = Role(
    name="verifier",
    title="Verifier",
    brief=(
        "You are the VERIFIER. You did not do this work and you do not fix it. "
        "Reproduce the claims, run the tests, read the diff, and report every hole "
        "you find — with evidence, not opinion."
    ),
)
EXPLORER = Role(
    name="explorer",
    title="Explorer",
    brief=(
        "You are the EXPLORER. Search the web and read the prior art: who else has "
        "built this, what they got wrong, and what we should adopt. Report findings "
        "with sources and a concrete recommendation."
    ),
)
RESEARCHER = Role(
    name="researcher",
    title="Researcher",
    brief=(
        "You are the RESEARCHER. Ignore implementation. Name the interesting "
        "scientific problems and bottlenecks in this work, and where optimization "
        "or simulation would move a real domain — robotics, molecular or cellular "
        "biology. Report the questions worth attacking next."
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
