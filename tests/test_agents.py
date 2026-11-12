"""agents: the registry of coding agents, whether each is installed (Omarchy's cold wrappers do not
count), whether `homalawn mcp` is registered (read from the agent's own file), registering it on
install / by the service / from the Agents page, and removing only what Homa Lawn added.

Every path is a temp one: conftest points every agent's own variable and agents.home() into
tmp_path, and the commands run here get that HOME. Agent commands are faked (FakeRun, or scripts
on a temp PATH), except the tests marked "real", which run the installed Claude Code and Codex
against a temp home and read the entry back with the agent itself."""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from homalawn import agents, mcp_server, ops, snapshots

HERMES_YAML = """# Hermes config
model:
  default: qwen
mcp_servers:
  github:
    command: npx
    args:
      - "@modelcontextprotocol/server-github"
  homalawn:
    command: homalawn
    args:
      - mcp
    enabled: {enabled}
  other:
    url: https://x
# ── Security ──
security:
  redact: true
"""

REAL_PATH = os.environ.get("PATH", "")


def exe(d: Path, name: str, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


@pytest.fixture
def bin_(tmp_path, monkeypatch):
    """An empty PATH of fake programs: add an agent with exe(bin_, name)."""
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    return d


def by_id(rows):
    return {r["id"]: r for r in rows}


class FakeRun:
    """Records the commands (by program name); `effect(name, argv)` changes the temp config the way
    the agent would. A command whose program is in `fail` exits 1 with `err`."""

    def __init__(self, effect=None, fail=(), err="boom", mise_has=()):
        self.cmds, self.inputs = [], []
        self.effect, self.fail, self.err, self.mise_has = effect, fail, err, mise_has

    def __call__(self, cmd, input=None, capture_output=True, text=True, timeout=None, env=None, cwd=None):
        name = Path(cmd[0]).name
        argv = [name, *cmd[1:]]
        if name == "mise":
            return subprocess.CompletedProcess(argv, 0 if cmd[2] in self.mise_has else 1, "", "")
        self.cmds.append(argv)
        self.inputs.append(input)
        if name in self.fail:
            return subprocess.CompletedProcess(argv, 1, "", self.err)
        if self.effect:
            self.effect(name, argv)
        return subprocess.CompletedProcess(argv, 0, "", "")


def claude_file():
    return Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"


def codex_file():
    return Path(os.environ["CODEX_HOME"]) / "config.toml"


def fake_cli(name, argv):
    """What the CLI agents write: enough of each format for agents' readers."""
    if name == "claude":
        f = claude_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(f.read_text()) if f.exists() else {}
        servers = data.setdefault("mcpServers", {})
        if argv[2] == "add":
            servers["homalawn"] = {"type": "stdio", "command": "homalawn", "args": ["mcp"], "env": {}}
        else:
            servers.pop("homalawn", None)
        f.write_text(json.dumps(data))
    elif name == "codex":
        f = codex_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('[mcp_servers.homalawn]\ncommand = "homalawn"\nargs = ["mcp"]\n' if argv[2] == "add" else "")
    elif name == "hermes":
        f = agents.hermes_config()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(HERMES_YAML.format(enabled="true") if argv[2] == "add" else "model:\n  default: x\n")


# -- where each agent keeps its servers ---------------------------------------------------------
def test_config_paths_follow_the_agents_own_variables(monkeypatch, tmp_path):
    e = os.environ
    assert agents.claude_config() == Path(e["CLAUDE_CONFIG_DIR"]) / ".claude.json"
    assert agents.hermes_config() == Path(e["HERMES_HOME"]) / "config.yaml"
    assert agents.codex_config() == Path(e["CODEX_HOME"]) / "config.toml"
    assert agents.gemini_config() == Path(e["GEMINI_CLI_HOME"]) / ".gemini" / "settings.json"
    assert agents.copilot_config() == Path(e["COPILOT_HOME"]) / "mcp-config.json"
    assert agents.grok_config() == Path(e["GROK_HOME"]) / "config.toml"
    assert agents.pi_config() == Path(e["PI_CODING_AGENT_DIR"]) / "mcp.json"
    assert agents.crush_config() == Path(e["CRUSH_GLOBAL_CONFIG"]) / "crushrc"
    assert agents.openclaw_config() == Path(e["OPENCLAW_STATE_DIR"]) / "openclaw.json"
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(tmp_path / "oc.json5"))
    assert agents.openclaw_config() == tmp_path / "oc.json5"
    for var in ("CLAUDE_CONFIG_DIR", "HERMES_HOME", "CODEX_HOME", "GEMINI_CLI_HOME", "COPILOT_HOME",
                "GROK_HOME", "PI_CODING_AGENT_DIR", "CRUSH_GLOBAL_CONFIG", "OPENCLAW_CONFIG_PATH",
                "OPENCLAW_STATE_DIR", "OPENCLAW_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    h = agents.home()
    assert agents.claude_config() == h / ".claude.json"
    assert agents.codex_config() == h / ".codex" / "config.toml"
    assert agents.gemini_config() == h / ".gemini" / "settings.json"
    assert agents.hermes_config() == h / ".hermes" / "config.yaml"
    assert agents.copilot_config() == h / ".copilot" / "mcp-config.json"
    assert agents.grok_config() == h / ".grok" / "config.toml"
    assert agents.pi_config() == h / ".pi" / "agent" / "mcp.json"
    assert agents.omp_config() == h / ".omp" / "agent" / "mcp.json"
    assert agents.cursor_config() == h / ".cursor" / "mcp.json"
    assert agents.crush_config() == h / ".config" / "crush" / "crushrc"
    assert agents.openclaw_config() == h / ".openclaw" / "openclaw.json"
    assert agents.omarchy_default_file() == h / ".config" / "omarchy" / "defaults" / "agent"


def test_every_omarchy_agent_is_supported_or_says_why_not():
    omarchy = {"pi", "omp", "opencode", "claude", "codex", "grok", "gemini", "openclaw", "hermes",
               "copilot", "crush", "cursor-agent", "muse"}
    assert set(agents.REGISTRY) | set(agents.UNSUPPORTED) == omarchy
    assert not set(agents.REGISTRY) & set(agents.UNSUPPORTED)
    for a in agents.REGISTRY.values():
        assert a.source and (a.add and a.remove or a.write and a.erase)


# -- installed? ---------------------------------------------------------------------------------
def test_an_omarchy_wrapper_counts_only_when_mise_has_its_package(bin_, tmp_path):
    ran = tmp_path / "wrapper-ran"
    exe(bin_, "crush", f'#!/bin/bash\nexport MISE_MINIMUM_RELEASE_AGE=0\ntouch {ran}\n'
                       'mise use -g --quiet "crush" || exit 1\nexec mise x "crush" -- "crush" "$@"\n')
    exe(bin_, "grok", '#!/bin/bash\nmise use -g "npm:@xai-official/grok" || exit 1\n'
                      'exec mise x "npm:@xai-official/grok" -- "grok" "$@"\n')
    exe(bin_, "mise")
    exe(bin_, "codex", "\x7fELF a real program")
    assert agents._stub_package(bin_ / "grok") == "npm:@xai-official/grok"
    assert agents.installed(agents.REGISTRY["crush"], FakeRun()) is False
    assert agents.installed(agents.REGISTRY["crush"], FakeRun(mise_has=("crush",))) is True
    assert agents.installed(agents.REGISTRY["grok"], FakeRun(mise_has=("npm:@xai-official/grok",))) is True
    assert agents.installed(agents.REGISTRY["codex"], FakeRun()) is True
    assert agents.installed(agents.REGISTRY["claude"], FakeRun()) is False      # not on PATH
    assert not ran.exists()                                                   # a wrapper is never run


def test_hermes_wrapper_package_is_its_pinned_tool_not_its_helper(tmp_path):
    p = exe(tmp_path, "hermes", "#!/bin/bash\n\n# Written by omarchy-install-hermes-cli.\n\n"
                                "if ! [[ -d x ]]; then\n  if omarchy-cmd-missing uv; then\n"
                                "    mise use -g --quiet uv@latest || exit 1\n  fi\n\n"
                                "  mise use -g --quiet --force 'pipx:hermes-agent[extras=all]' || exit 1\nfi\n")
    assert agents._stub_package(p) == "pipx:hermes-agent[extras=all]"
    link = tmp_path / "linked"
    link.symlink_to(p)
    assert agents._stub_package(link) is None          # a link is the user's own arrangement


# -- status ---------------------------------------------------------------------------------------
def test_status_lists_the_installed_agents_default_first(bin_):
    for n in ("claude", "hermes", "codex"):
        exe(bin_, n)
    f = agents.omarchy_default_file()
    f.parent.mkdir(parents=True)
    f.write_text("codex\n")
    claude_file().parent.mkdir(parents=True)
    claude_file().write_text(json.dumps({"mcpServers": {"homalawn": {"type": "stdio", "command": "homalawn",
                                                                     "args": ["mcp"], "env": {}}}}))
    agents.hermes_config().parent.mkdir(parents=True)
    agents.hermes_config().write_text(HERMES_YAML.format(enabled="false"))
    rows = agents.status(run=FakeRun())
    assert [r["id"] for r in rows] == ["codex", "claude", "hermes"]
    r = by_id(rows)
    assert r["codex"]["default"] is True and r["codex"]["registered"] is False
    assert r["claude"] | {"config": None} == {"id": "claude", "name": "Claude Code", "installed": True,
                                              "default": False, "config": None, "registered": True,
                                              "enabled": True, "command": "homalawn mcp", "error": None,
                                              "managed": None}
    assert r["hermes"]["registered"] is True and r["hermes"]["enabled"] is False


def test_other_servers_and_project_scopes_do_not_count(bin_):
    exe(bin_, "claude")
    exe(bin_, "hermes")
    claude_file().parent.mkdir(parents=True)
    claude_file().write_text(json.dumps({"mcpServers": {"x": {}}, "projects": {"/p": {"mcpServers": {"homalawn": {}}}}}))
    agents.hermes_config().parent.mkdir(parents=True)
    agents.hermes_config().write_text("mcp_servers:\n  github:\n    command: npx\nhomalawn:\n  command: homalawn\n")
    rows = by_id(agents.status(run=FakeRun()))
    assert rows["claude"]["registered"] is False and rows["hermes"]["registered"] is False


def test_an_unreadable_file_says_so(bin_):
    exe(bin_, "claude")
    claude_file().parent.mkdir(parents=True)
    claude_file().write_text("{not json")
    assert "cannot read" in by_id(agents.status(run=FakeRun()))["claude"]["error"]


def test_readers_of_the_other_formats(tmp_path):
    g = tmp_path / "settings.json"
    g.write_text('{\n  // Gemini allows comments\n  "mcpServers": {"homalawn": {"command": "homalawn", "args": ["mcp"]}}\n}')
    assert agents._json_entry("mcpServers", jsonc=True)(g) == {"command": "homalawn", "args": ["mcp"]}
    o = tmp_path / "openclaw.json"
    o.write_text('{"mcp": {"servers": {"homalawn": {"command": "homalawn", "args": ["mcp"]}}}}')
    assert agents._command_of(agents._json_entry("mcp", "servers")(o)) == "homalawn mcp"
    t = tmp_path / "config.toml"
    t.write_text('model = "x"\n[mcp_servers.homalawn]\ncommand = "homalawn"\nargs = ["mcp"]\nenabled = false\n')
    e = agents._toml_entry(t)
    assert agents._command_of(e) == "homalawn mcp" and agents._enabled_of(e) is False


# -- registering on install and by the service ------------------------------------------------------
def test_install_registers_every_installed_agent_and_says_one_line_each(bin_):
    exe(bin_, "claude")
    exe(bin_, "codex")
    run = FakeRun(fake_cli)
    lines = agents.sync(run=run)
    assert run.cmds == [["claude", "mcp", "add", "--scope", "user", "homalawn", "--", "homalawn", "mcp"],
                        ["codex", "mcp", "add", "homalawn", "--", "homalawn", "mcp"]]
    assert lines == ["Claude Code can now use your phone: Homa Lawn was added to its settings.",
                     "Codex can now use your phone: Homa Lawn was added to its settings."]
    state = json.loads(agents.state_path().read_text())["agents"]
    assert {k: v["state"] for k, v in state.items()} == {"claude": "added", "codex": "added"}
    # again: nothing is run, each agent still gets its line
    run2 = FakeRun(fake_cli)
    assert len(agents.sync(run=run2)) == 2 and run2.cmds == []


def test_agent_commands_run_from_the_home_folder_with_its_home(bin_):
    exe(bin_, "codex")
    seen = {}

    def run(cmd, **kw):
        seen.update(cwd=kw["cwd"], home=kw["env"]["HOME"])
        fake_cli("codex", ["codex", "mcp", "add"])
        return subprocess.CompletedProcess(cmd, 0, "", "")
    agents.sync(run=run)
    assert seen == {"cwd": str(agents.home()), "home": str(agents.home())}


def test_an_entry_the_user_made_is_left_alone_and_never_removed(bin_):
    exe(bin_, "codex")
    fake_cli("codex", ["codex", "mcp", "add"])
    run = FakeRun(fake_cli)
    assert agents.sync(run=run) == ["Codex already had Homa Lawn; left as it was."]
    assert run.cmds == []
    assert agents.unregister_all(run=run) == [] and run.cmds == []
    assert agents._toml_entry(codex_file()) is not None
    fake_cli("codex", ["codex", "mcp", "remove"])        # the user removes their own entry
    assert agents.sync(run=run, quiet=True) == [] and run.cmds == []


def test_an_entry_the_user_removed_after_we_added_it_is_not_added_again(bin_):
    exe(bin_, "codex")
    agents.sync(run=FakeRun(fake_cli))
    fake_cli("codex", ["codex", "mcp", "remove"])        # the user takes it out
    run = FakeRun(fake_cli)
    lines = agents.sync(run=run, quiet=True)             # the service's check
    assert run.cmds == [] and "was not added again" in lines[0]
    assert agents.sync(run=run, quiet=True) == []        # said once; the service stays quiet after
    assert "was not added again" in agents.sync(run=run)[0]   # a re-install says it again
    assert run.cmds == []
    # Connect on the Agents page is the user asking: it is added, and counts as ours again
    assert "registered in Codex" in agents.connect("codex", run=run)
    assert json.loads(agents.state_path().read_text())["agents"]["codex"]["state"] == "added"


def test_an_agent_installed_later_is_registered_by_the_service(bin_):
    assert agents.sync(run=FakeRun(fake_cli), quiet=True) == []
    exe(bin_, "claude")
    run = FakeRun(fake_cli)
    assert agents.sync(run=run, quiet=True) == ["Claude Code can now use your phone: Homa Lawn was added to its settings."]
    assert agents.sync(run=run, quiet=True) == []


def test_no_agent_installed_says_so_on_install(bin_):
    assert "No coding agent" in agents.sync(run=FakeRun())[0]


def test_a_failed_registration_says_why_and_is_tried_again(bin_):
    exe(bin_, "codex")
    lines = agents.sync(run=FakeRun(fake_cli, fail=("codex",), err="no network"))
    assert lines == ["Codex: could not add Homa Lawn (Codex did not register Homa Lawn: no network); "
                     "it will be tried again."]
    run = FakeRun(fake_cli)
    agents.sync(run=run, quiet=True)
    assert run.cmds == [["codex", "mcp", "add", "homalawn", "--", "homalawn", "mcp"]]


# -- uninstall ------------------------------------------------------------------------------------
def test_uninstall_removes_only_what_we_added_and_still_runs_homalawn(bin_):
    for n in ("claude", "codex", "hermes"):
        exe(bin_, n)
    fake_cli("hermes", ["hermes", "mcp", "add"])          # the user's own
    agents.sync(run=FakeRun(fake_cli))                    # adds claude and codex
    codex_file().write_text('[mcp_servers.homalawn]\ncommand = "/opt/homalawn"\nargs = ["mcp"]\n')  # edited since
    run = FakeRun(fake_cli)
    lines = agents.unregister_all(run=run)
    assert run.cmds == [["claude", "mcp", "remove", "--scope", "user", "homalawn"]]
    assert lines == ["Removed Homa Lawn from Claude Code.",
                     "Codex: its Homa Lawn entry was changed since, so it was left as it is."]
    assert agents._json_entry("mcpServers")(claude_file()) is None
    assert agents._hermes_entry(agents.hermes_config()) is not None
    assert {k: v["state"] for k, v in json.loads(agents.state_path().read_text())["agents"].items()} == {"hermes": "user"}


def test_hermes_answers_its_questions(bin_):
    exe(bin_, "hermes")
    run = FakeRun(fake_cli)
    agents.sync(run=run)
    agents.unregister_all(run=run)
    assert run.cmds == [["hermes", "mcp", "add", "homalawn", "--command", "homalawn", "--args", "mcp"],
                        ["hermes", "mcp", "remove", "homalawn"]]
    assert run.inputs == ["y\ny\n", "y\n"]


# -- agents whose file Homa Lawn edits --------------------------------------------------------------
def test_json_file_agents_keep_everything_else(bin_):
    exe(bin_, "cursor-agent")
    f = agents.cursor_config()
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"mcpServers": {"github": {"command": "npx"}}, "other": [1, "ą"]}))
    f.chmod(0o640)
    assert "Cursor CLI can now use your phone" in agents.sync(run=FakeRun())[0]
    data = json.loads(f.read_text())
    assert data == {"mcpServers": {"github": {"command": "npx"},
                                   "homalawn": {"type": "stdio", "command": "homalawn", "args": ["mcp"]}},
                    "other": [1, "ą"]}
    assert f.stat().st_mode & 0o777 == 0o640
    assert agents.unregister_all(run=FakeRun()) == ["Removed Homa Lawn from Cursor CLI."]
    assert json.loads(f.read_text()) == {"mcpServers": {"github": {"command": "npx"}}, "other": [1, "ą"]}


def test_a_json_file_with_comments_is_not_rewritten(bin_):
    exe(bin_, "omp")
    f = agents.omp_config()
    f.parent.mkdir(parents=True)
    f.write_text('{ // mine\n "mcpServers": {} }')
    lines = agents.sync(run=FakeRun())
    assert lines[0].startswith("Oh My Pi: could not read its settings") and "was not added" in lines[0]
    assert json.loads(agents.state_path().read_text())["agents"] == {}      # no decision: tried again
    assert f.read_text() == '{ // mine\n "mcpServers": {} }'


def test_a_new_json_file_is_private(bin_):
    exe(bin_, "omp")
    agents.sync(run=FakeRun())
    assert agents.omp_config().stat().st_mode & 0o777 == 0o600


def test_crush_gets_one_line_in_its_crushrc_and_the_last_line_wins(bin_):
    exe(bin_, "crush")
    f = agents.crush_config()
    f.parent.mkdir(parents=True)
    f.write_text("set -e\nmcp add github --command npx --args -y")
    agents.sync(run=FakeRun())
    assert f.read_text() == ("set -e\nmcp add github --command npx --args -y\n" + agents.CRUSH_MARK +
                             "\nmcp add homalawn --command homalawn --args mcp\n")
    assert agents._command_of(agents._crush_entry(f)) == "homalawn mcp"
    f.write_text(f.read_text() + "mcp rm homalawn\n")       # the user switches it off below ours
    assert agents._crush_entry(f) is None
    agents.unregister_all(run=FakeRun())
    assert f.read_text() == "set -e\nmcp add github --command npx --args -y\nmcp rm homalawn\n"


# -- the Agents page's Connect --------------------------------------------------------------------
def test_claude_connect_removes_an_old_registration_then_adds(bin_):
    exe(bin_, "claude")
    claude_file().parent.mkdir(parents=True)
    claude_file().write_text(json.dumps({"mcpServers": {"homalawn": {"command": "old"}}}))
    run = FakeRun(fake_cli)
    text = agents.connect("claude", run=run)
    assert run.cmds == [["claude", "mcp", "remove", "--scope", "user", "homalawn"],
                        ["claude", "mcp", "add", "--scope", "user", "homalawn", "--", "homalawn", "mcp"]]
    assert "registered in Claude Code" in text


def test_connect_failures_say_why(bin_):
    exe(bin_, "hermes")
    exe(bin_, "claude")
    with pytest.raises(agents.AgentError, match="did not register Homa Lawn: boom"):
        agents.connect("hermes", run=FakeRun(fail=("hermes",)))

    def off(name, argv):
        agents.hermes_config().parent.mkdir(parents=True, exist_ok=True)
        agents.hermes_config().write_text(HERMES_YAML.format(enabled="false"))
    with pytest.raises(agents.AgentError, match="switched off"):
        agents.connect("hermes", run=FakeRun(off))
    with pytest.raises(agents.AgentError, match="is not in"):
        agents.connect("claude", run=FakeRun())                 # says ok, writes nothing
    with pytest.raises(agents.AgentError, match="unknown agent"):
        agents.connect("cursor", run=FakeRun())
    run = FakeRun()
    with pytest.raises(agents.AgentError, match="not installed"):
        agents.connect("codex", run=run)
    assert run.cmds == []


# -- the real Claude Code and Codex, against a temp home --------------------------------------------
def real_env():
    return {**os.environ, "PATH": REAL_PATH, "HOME": str(agents.home())}


def real(name):
    found = shutil.which(name, path=REAL_PATH)
    if not found:
        pytest.skip(f"{name} is not installed here")
    return found


def only(monkeypatch, aid):
    """Just that agent, found on the real PATH, as a fresh user: no config variable, an empty temp
    home (the agent's default folder under it)."""
    monkeypatch.setattr(agents, "REGISTRY", {aid: agents.REGISTRY[aid]})
    monkeypatch.setenv("PATH", REAL_PATH)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setenv("HOME", str(agents.home()))     # the readers in this process, like the commands
    agents.home().mkdir(parents=True, exist_ok=True)


def test_real_claude_reads_back_what_install_added_and_uninstall_removed(monkeypatch):
    claude = real("claude")
    only(monkeypatch, "claude")
    assert agents.sync() == ["Claude Code can now use your phone: Homa Lawn was added to its settings."]
    assert agents.claude_config() == agents.home() / ".claude.json" and agents.claude_config().is_file()
    r = subprocess.run([claude, "mcp", "get", "homalawn"], capture_output=True, text=True, timeout=120,
                       env=real_env(), cwd=agents.home())
    assert r.returncode == 0, r.stderr
    assert "User config" in r.stdout and "Command: homalawn" in r.stdout and "Args: mcp" in r.stdout
    assert agents.unregister_all() == ["Removed Homa Lawn from Claude Code."]
    r = subprocess.run([claude, "mcp", "get", "homalawn"], capture_output=True, text=True, timeout=120,
                       env=real_env(), cwd=agents.home())
    assert r.returncode != 0


def test_real_codex_reads_back_what_install_added_and_uninstall_removed(monkeypatch):
    codex = real("codex")
    only(monkeypatch, "codex")
    assert agents.sync() == ["Codex can now use your phone: Homa Lawn was added to its settings."]
    assert agents.codex_config() == agents.home() / ".codex" / "config.toml" and agents.codex_config().is_file()
    r = subprocess.run([codex, "mcp", "get", "homalawn", "--json"], capture_output=True, text=True,
                       timeout=120, env=real_env(), cwd=agents.home())
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["enabled"] is True and got["transport"] == {**got["transport"], "type": "stdio",
                                                          "command": "homalawn", "args": ["mcp"]}
    assert agents.unregister_all() == ["Removed Homa Lawn from Codex."]
    r = subprocess.run([codex, "mcp", "get", "homalawn", "--json"], capture_output=True, text=True,
                       timeout=120, env=real_env(), cwd=agents.home())
    assert r.returncode != 0


# -- agent-made snapshots ---------------------------------------------------------------------------
def test_the_agent_server_marks_its_snapshots(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    snapshots.save("prefs-20261015-100000", {"prefs.json": b"{}"}, device="link:a")
    monkeypatch.setattr(snapshots, "ORIGIN", "agent")
    snapshots.save("prefs-20261015-100001", {"prefs.json": b"{}"}, device="link:a")
    assert snapshots.by_of("prefs-20261015-100000") is None
    assert snapshots.by_of("prefs-20261015-100001") == "agent"
    assert snapshots.device_of("prefs-20261015-100001") == "link:a"
    rows = {r["name"]: r["by"] for r in ops.snapshot_list(ops.Session()).data}
    assert rows == {"prefs-20261015-100000": None, "prefs-20261015-100001": "agent"}


def test_mcp_serve_sets_the_origin(monkeypatch):
    seen = {}

    class Server:
        def run(self, how):
            seen["origin"] = snapshots.ORIGIN
    monkeypatch.setattr(snapshots, "ORIGIN", None)
    monkeypatch.setattr(mcp_server, "build_server", lambda s: Server())
    mcp_server.serve(None, None, None)
    assert seen["origin"] == "agent"
