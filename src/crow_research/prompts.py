"""Specialize an agent by *editing* crow's system prompt, never by replacing it.

The seam is :data:`crow_cli.agent.prompt.SystemPromptFactory` — a callable
``(config, cwd, session_id) -> SystemPromptResponse`` that ``CrowAgentV2`` calls
once per session. :func:`role_prompt` builds one that starts from
:func:`~crow_cli.agent.prompt.default_system_prompt` and applies surgical edits
to the template it returns: replace the body of a ``<SECTION>``, or insert a new
section after an existing one.

Editing rather than writing is the whole point. crow's prompt is 19k characters
of hard-won harness detail — the persistent-kernel rules, the search tooling, the
git trailer contract, the memory schema — and a verifier that lost it would be a
worse agent for having a job. So a role keeps every byte of it and changes the
three sections that decide what the agent *does*: its role, its workflow, and
what it is allowed to touch. Everything else is inherited.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Optional

from crow_cli.agent.prompt import SystemPromptResponse, default_system_prompt
from crow_cli.config import Config

#: ``(config, cwd, session_id) -> SystemPromptResponse`` — what ``CrowAgentV2``
#: accepts as ``system_prompt=``.
SystemPromptFactory = Callable[[Config, str, Optional[str]], SystemPromptResponse]

_SECTION = re.compile(r"^<([A-Z0-9_]+)>$(.*?)^</[A-Z0-9_]+>$", re.M | re.S)


def sections(template: str) -> dict[str, str]:
    """Every ``<NAME>`` section body in a template, keyed by name."""
    return {m.group(1): m.group(2) for m in _SECTION.finditer(template)}


def patch_section(template: str, name: str, body: str) -> str:
    """Replace one section's body, keeping its tags and everything else intact.

    Raises rather than silently doing nothing: a role whose ``<ROLE>`` patch
    missed because the section was renamed upstream is an agent that quietly
    kept the wrong job, and that is the failure mode this whole module exists to
    avoid.
    """
    pattern = re.compile(rf"^(<{name}>$)(.*?)(^</{name}>$)", re.M | re.S)
    if not pattern.search(template):
        raise KeyError(f"system prompt has no <{name}> section to patch")
    return pattern.sub(lambda m: f"{m.group(1)}\n{body.strip()}\n{m.group(3)}", template, count=1)


def insert_section(template: str, name: str, body: str, *, after: str) -> str:
    """Add a new ``<name>`` section immediately after the ``<after>`` section.

    Placement is the reason this exists instead of appending: a contract that
    sits after ``<IMPORTANT>`` is read as part of the operating instructions,
    while one dangling at the end of 19k characters is read as an afterthought.
    """
    pattern = re.compile(rf"^</{after}>$", re.M)
    if not pattern.search(template):
        raise KeyError(f"system prompt has no </{after}> to insert <{name}> after")
    block = f"</{after}>\n\n<{name}>\n{body.strip()}\n</{name}>"
    # A lambda, not a replacement string: ``re.sub`` reads every backslash in a
    # replacement as an escape, and a prompt that quotes a regex or a Windows
    # path is not a thing to break over.
    return pattern.sub(lambda _m: block, template, count=1)


def role_prompt(
    patches: Optional[dict[str, str]] = None,
    extra: Optional[Iterable[tuple[str, str, str]]] = None,
    *,
    role: str = "crow-research",
) -> SystemPromptFactory:
    """A ``SystemPromptFactory`` that is crow's prompt with your edits applied.

    ``patches`` maps a section name to its new body; ``extra`` is
    ``(name, body, after_section)`` triples for sections crow does not have.
    The template args are inherited untouched — ``workspace`` and ``rlm_depth``
    are load-bearing for session lookup and the delegation budget, and a role
    that dropped them would vanish from the session list.
    """

    def factory(config: Config, cwd: str, session_id: Optional[str] = None) -> SystemPromptResponse:
        base = default_system_prompt(config, cwd, session_id)
        template = base.template
        for name, body in (patches or {}).items():
            template = patch_section(template, name, body)
        for name, body, after in extra or ():
            template = insert_section(template, name, body, after=after)
        args: dict[str, Any] = {**base.template_args, "crow_role": role}
        return SystemPromptResponse(template=template, template_args=args)

    return factory
