"""The cast: two new roles, and the prompts behind them.

Specialization here means *editing* crow's system prompt, never writing a fresh
one, so what is under test is exactly that: a rendered role prompt is crow's own
prompt with a named handful of sections replaced and one added, and every other
byte inherited. The assertions are derived from the agent file rather than
hardcoded against it — which sections it patches, what it calls them, how many
there are — so the test also catches the failure nothing else would: a section
written into the file and never wired into ``role_prompt``, which renders as an
agent that quietly kept crow's job.

The char counts are deliberately relational. A hardcoded one is a false red the
first time crow-cli's prompt changes upstream, and inheriting upstream changes is
the entire reason ``role_prompt`` patches a template instead of freezing a copy.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from crow_cli.agent.prompt import default_system_prompt
from crow_cli.config import Config

from crow_research import BUILTIN, DRAFTER, WRITER, Peer, Role, Stdio, Ws, resolve
from crow_research.prompts import sections
from test_http import free_port, wait_for_port  # noqa: E402  (pytest puts tests/ on sys.path)
from test_orchestrator import Recorder  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"

#: The two roles this phase adds. The other four are covered by the pipeline and
#: orchestrator tests, which drive them over a real socket.
NEW = ("drafter", "writer")

#: ``crow-cli acp2`` binds this one on this machine, and the orchestrator owns its
#: own default, so no child agent may take either.
CROW_CLI_ACP2_PORT = 2771
ORCHESTRATOR = "orchestrator_agent.py"


@pytest.fixture(scope="module")
def config() -> Config:
    return Config.load(None)


def load_agent(name: str):
    """Import ``agents/<name>_agent.py`` by path — it is a script, not a package."""
    path = AGENTS / f"{name}_agent.py"
    spec = importlib.util.spec_from_file_location(f"_agent_under_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def port_default(path: Path) -> int:
    """The ``--port`` default an agent file ships with, read out of its AST.

    Resolves a module-level constant as well as a literal, because
    ``orchestrator_agent.py`` names its default ``DEFAULT_PORT`` instead of
    inlining the number.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_consts = {
        target.id: node.value.value
        for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets if isinstance(target, ast.Name)
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument" or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "--port"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if isinstance(kw.value, ast.Constant):
                return int(kw.value.value)
            if isinstance(kw.value, ast.Name) and kw.value.id in module_consts:
                return int(module_consts[kw.value.id])
    raise AssertionError(f"{path.name} has no --port default")


# --- 3.1 the roles exist and resolve ---------------------------------------


@pytest.mark.parametrize("name", NEW)
def test_the_role_is_builtin(name):
    role = BUILTIN[name]
    assert role.name == name
    assert role.label == name.title()
    assert role.brief.strip(), "a role with no brief hands off nothing"
    assert resolve(name) == role


@pytest.mark.parametrize("name", NEW)
def test_resolve_still_parses_an_endpoint_for_the_new_roles(name):
    """``name=endpoint`` is what makes a role configurable from a command line."""
    role = resolve(f"{name}=ws://127.0.0.1:9999/acp")
    assert role.name == name
    assert role.brief == BUILTIN[name].brief
    assert role.title == BUILTIN[name].title
    assert isinstance(role.endpoint, Ws)
    assert role.endpoint.url == "ws://127.0.0.1:9999/acp"


def test_the_builtin_cast_has_no_duplicate_names():
    assert len(BUILTIN) == len({r.name for r in BUILTIN.values()})
    assert set(NEW) <= set(BUILTIN)
    assert DRAFTER is BUILTIN["drafter"] and WRITER is BUILTIN["writer"]


def test_an_unknown_role_is_still_a_working_role():
    """A name with no agent file behind it is not an error — it is a plain crow agent."""
    role = resolve("nobody")
    assert isinstance(role, Role)
    assert role.name == "nobody" and role.label == "Nobody"
    assert role.endpoint == Stdio(("crow-cli", "acp2"))


# --- 3.2 / 3.3 the prompts are crow's prompt, edited ------------------------


@pytest.mark.parametrize("name", NEW)
def test_the_agent_file_renders_crows_prompt_edited(name, config, tmp_path):
    module = load_agent(name)
    base = default_system_prompt(config, str(tmp_path), None)
    out = module.system_prompt(config, str(tmp_path), None)

    # The role stamp is what a UI shows and what crow.db filters on.
    assert out.template_args["crow_role"] == name
    # Everything else about the template args is inherited untouched: workspace and
    # session lookup are load-bearing, and a role that dropped them would vanish
    # from the session list.
    assert {k: v for k, v in out.template_args.items() if k != "crow_role"} == base.template_args

    before, after = sections(base.template), sections(out.template)
    consts = {k: v.strip() for k, v in vars(module).items()
              if k.isupper() and isinstance(v, str) and not k.startswith("_")}
    assert consts, f"{name}_agent.py defines no section bodies"

    # Every constant the file defines lands in exactly one section, so a section
    # written and never wired into role_prompt() fails here.
    landed: dict[str, str] = {}
    for const, body in consts.items():
        hits = [s for s, text in after.items() if text.strip() == body]
        assert len(hits) == 1, f"{name}_agent.py's {const} landed in {hits}, expected exactly one section"
        landed[hits[0]] = const

    added = set(after) - set(before)
    patched = set(landed) - added
    assert len(added) == 1, f"{name} adds {sorted(added)}, expected exactly one new section"
    assert patched <= set(before), f"{name} patches sections crow does not have: {sorted(patched - set(before))}"
    assert len(patched) == len(consts) - 1

    # Specializing adds, it never subtracts.
    assert len(after) == len(before) + 1
    assert len(out.template) > len(base.template)

    # Every section the role did not touch is inherited byte for byte.
    for section, text in before.items():
        if section not in patched:
            assert after[section] == text, f"{name} changed <{section}> without saying so"


@pytest.mark.parametrize("name", NEW)
def test_the_added_section_sits_after_important(name, config, tmp_path):
    """Placement is the reason ``insert_section`` exists: a contract dangling at
    the end of 19k characters reads as an afterthought."""
    module = load_agent(name)
    base = default_system_prompt(config, str(tmp_path), None)
    out = module.system_prompt(config, str(tmp_path), None)
    added = (set(sections(out.template)) - set(sections(base.template))).pop()

    tail = out.template.split("</IMPORTANT>", 1)[1]
    assert re.match(rf"\s*<{added}>", tail), f"<{added}> is not the section after </IMPORTANT>"


def test_patch_section_still_raises_on_a_section_crow_does_not_have():
    """The guard the whole approach rests on: a renamed upstream section is a loud
    failure, not an agent that quietly kept the wrong job."""
    from crow_research.prompts import patch_section

    with pytest.raises(KeyError):
        patch_section("<ROLE>\nbody\n</ROLE>", "NO_SUCH_SECTION", "x")


# --- 3.4 the command line ---------------------------------------------------


@pytest.mark.parametrize("name", NEW)
def test_the_agent_file_help_runs(name):
    done = subprocess.run([sys.executable, str(AGENTS / f"{name}_agent.py"), "--help"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    for flag in ("--http", "--host", "--port", "--model", "--config-dir", "--config-file", "--debug"):
        assert flag in done.stdout, f"{name}_agent.py --help does not offer {flag}"


@pytest.mark.parametrize("name", NEW)
async def test_the_agent_file_serves_http(name):
    """``--http --port N`` binds, negotiates v2 and opens a session.

    Only the handshake: a prompt would start a real turn on a real model, and this
    test has no business paying for that.
    """
    port = free_port()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(AGENTS / f"{name}_agent.py"), "--http", "--port", str(port),
        stderr=asyncio.subprocess.PIPE,
    )
    peer = None
    try:
        await wait_for_port(port)
        peer = Peer(Role(name=name, endpoint=Ws(f"ws://127.0.0.1:{port}/acp")), client_factory=Recorder)
        session_id = await peer.start(str(ROOT))
        assert session_id, "session/new came back with no id"
    finally:
        if peer is not None:
            await peer.close()
        proc.terminate()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=20)
        assert proc.returncode is not None, f"the served {name} agent did not exit on SIGTERM"


def test_no_two_agent_files_share_a_default_port():
    """A campaign serves them side by side, and a collision is a silent one: the
    second agent to bind loses, and the client dials whichever won."""
    files = sorted(AGENTS.glob("*_agent.py"))
    assert {p.name for p in files} >= {"drafter_agent.py", "writer_agent.py", ORCHESTRATOR}
    ports = {p.name: port_default(p) for p in files}

    assert len(set(ports.values())) == len(ports), ports
    for name, port in ports.items():
        if name == ORCHESTRATOR:
            continue
        assert port != CROW_CLI_ACP2_PORT, f"{name} defaults to a port crow-cli acp2 already binds"
        assert port != ports[ORCHESTRATOR], f"{name} defaults to the orchestrator's own port"
