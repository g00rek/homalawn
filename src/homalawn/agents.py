"""The coding agents Homa Lawn's tool server (`homalawn mcp`) is registered with, and registering it.

Installing the Omarchy plugin (`homalawn desktop install`) registers `homalawn mcp` with every
supported agent installed on this computer, and the service (`homalawn serve`) registers it with
agents installed later. What Homa Lawn added is recorded in its own state folder
(`agents.json`), so `desktop uninstall` removes only those entries, and an entry the user removed
after we added it (or one the user had made themselves) is never added again on its own; only
Connect on the Agents page adds it back.

Every agent's command or file format below was checked against that agent's own CLI (`--help`
and a run against a temporary home) or its current documentation; the source is named next to
each entry in REGISTRY. An agent whose format could not be verified is not listed (see
UNSUPPORTED).

"Installed" follows Omarchy's own test (omarchy-default-agent): on a fresh Omarchy every agent of
its menu has a small wrapper in ~/.local/bin that installs the agent with mise the first time it
runs. Such a wrapper counts only when mise has its package (`mise where <package>`); it is never
run to find out, since running it installs the agent.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import time
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import env

SERVER = "homalawn"
COMMAND = ["homalawn", "mcp"]
TIMEOUT_S = 120
STUB_MAX_BYTES = 4096        # Omarchy's wrappers are a few lines; anything bigger is a real program


class AgentError(Exception):
    """A registration failed; the message says why in one line."""


# -- where things are -----------------------------------------------------------------------------
def home() -> Path:
    return Path.home()


def omarchy_default_file() -> Path:
    """Omarchy's chosen coding agent (omarchy-default-agent writes $HOME/.config/omarchy/defaults/agent)."""
    return home() / ".config" / "omarchy" / "defaults" / "agent"


def omarchy_default() -> str | None:
    try:
        line = omarchy_default_file().read_text().strip().splitlines()
    except OSError:
        return None
    return line[0].strip() if line and line[0].strip() else None


def state_path() -> Path:
    return env.state_dir() / "agents.json"


def _env_dir(var: str, *default: str) -> Path:
    d = os.environ.get(var)
    return Path(d) if d else home().joinpath(*default)


def claude_config() -> Path:
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(d) / ".claude.json" if d else home() / ".claude.json"


def codex_config() -> Path:
    return _env_dir("CODEX_HOME", ".codex") / "config.toml"


def gemini_config() -> Path:
    # GEMINI_CLI_HOME stands in for the home folder (Gemini CLI 0.64 bundle: homedir()).
    return _env_dir("GEMINI_CLI_HOME") / ".gemini" / "settings.json"


def hermes_config() -> Path:
    return _env_dir("HERMES_HOME", ".hermes") / "config.yaml"


def copilot_config() -> Path:
    return _env_dir("COPILOT_HOME", ".copilot") / "mcp-config.json"


def grok_config() -> Path:
    return _env_dir("GROK_HOME", ".grok") / "config.toml"


def pi_config() -> Path:
    return _env_dir("PI_CODING_AGENT_DIR", ".pi", "agent") / "mcp.json"


def omp_config() -> Path:
    # PI_CONFIG_DIR / PI_CODING_AGENT_DIR / profiles can move it; the default profile's file only
    return home() / ".omp" / "agent" / "mcp.json"


def cursor_config() -> Path:
    return home() / ".cursor" / "mcp.json"


def crush_config() -> Path:
    d = os.environ.get("CRUSH_GLOBAL_CONFIG")
    if d:
        return Path(d) / "crushrc"
    return Path(os.environ.get("XDG_CONFIG_HOME") or home() / ".config") / "crush" / "crushrc"


def openclaw_config() -> Path:
    p = os.environ.get("OPENCLAW_CONFIG_PATH")
    if p:
        return Path(p)
    d = os.environ.get("OPENCLAW_STATE_DIR")
    if d:
        return Path(d) / "openclaw.json"
    base = Path(os.environ.get("OPENCLAW_HOME") or home())
    profile = os.environ.get("OPENCLAW_PROFILE")
    return base / (f".openclaw-{profile}" if profile else ".openclaw") / "openclaw.json"


# -- reading an agent's file ----------------------------------------------------------------------
def _strip_json_comments(text: str) -> str:
    """JSON with // and /* */ comments (JSONC) as plain JSON; strings are left alone."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _json_entry(*keys: str, jsonc: bool = False) -> Callable[[Path], dict | None]:
    """Reader for a JSON file whose servers sit under `keys` (e.g. "mcpServers")."""
    def read(path: Path) -> dict | None:
        text = path.read_text()
        if not text.strip():
            return None
        data = json.loads(_strip_json_comments(text) if jsonc else text)
        node = data
        for k in keys:
            node = node.get(k) if isinstance(node, dict) else None
        entry = node.get(SERVER) if isinstance(node, dict) else None
        return entry if isinstance(entry, dict) else None
    return read


def _toml_entry(path: Path) -> dict | None:
    """`[mcp_servers.homalawn]` of a TOML file (Codex, Grok)."""
    import tomllib
    data = tomllib.loads(path.read_text())
    entry = (data.get("mcp_servers") or {}).get(SERVER)
    return entry if isinstance(entry, dict) else None


def _hermes_entry(path: Path) -> dict | None:
    """The `mcp_servers: homalawn:` block of Hermes's YAML, as {command, args, enabled}; read by
    indentation (the block Hermes writes is plain YAML), so no YAML library is needed."""
    lines = path.read_text().splitlines()
    out: dict | None = None
    in_servers = False
    indent = None
    key = None
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        depth = len(raw) - len(raw.lstrip(" "))
        text = raw.strip()
        if depth == 0:
            if out is not None:
                break
            in_servers = text.split(":", 1)[0].strip() == "mcp_servers"
            continue
        if not in_servers:
            continue
        if indent is None:
            indent = depth
        if depth == indent:
            if out is not None:
                break
            if text.split(":", 1)[0].strip().strip("'\"") == SERVER:
                out = {"args": []}
            continue
        if out is None:
            continue
        if text.startswith("- ") and key == "args":
            out["args"].append(text[2:].strip().strip("'\""))
            continue
        k, _, v = text.partition(":")
        key, v = k.strip(), v.strip().strip("'\"")
        if key == "command":
            out["command"] = v
        elif key == "enabled":
            out["enabled"] = v.lower() not in ("false", "no", "off", "0")
        elif key == "args" and v.startswith("["):
            out["args"] = [a.strip().strip("'\"") for a in v.strip("[]").split(",") if a.strip()]
    return out


def _command_of(entry: dict) -> str | None:
    """What an entry runs, as one line ("homalawn mcp"): `command` plus `args`, or a command list."""
    cmd = entry.get("command")
    if isinstance(cmd, list):
        return " ".join(str(x) for x in cmd) or None
    if not cmd:
        return None
    return " ".join([str(cmd)] + [str(x) for x in entry.get("args") or []])


def _enabled_of(entry: dict) -> bool:
    return entry.get("enabled", True) is not False and entry.get("disabled") is not True


# -- editing an agent's file (agents without a command for it) ------------------------------------
def _load_plain_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        raise AgentError(f"{path} is not plain JSON (it may hold comments), so Homa Lawn did not change it")
    if not isinstance(data, dict):
        raise AgentError(f"{path} does not hold a JSON object, so Homa Lawn did not change it")
    return data


def _write_json(path: Path, data: dict) -> None:
    """Write atomically, keeping the file's permissions (a new file is private: 0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    tmp = path.with_name(f".{path.name}.homalawn-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _json_set(path: Path, keys: tuple[str, ...], entry: dict) -> None:
    data = _load_plain_json(path)
    node = data
    for k in keys:
        nxt = node.setdefault(k, {})
        if not isinstance(nxt, dict):
            raise AgentError(f"`{k}` in {path} is not an object, so Homa Lawn did not change it")
        node = nxt
    node[SERVER] = entry
    _write_json(path, data)


def _json_del(path: Path, keys: tuple[str, ...]) -> None:
    data = _load_plain_json(path)
    node = data
    for k in keys:
        node = node.get(k) if isinstance(node, dict) else None
    if isinstance(node, dict) and SERVER in node:
        del node[SERVER]
        _write_json(path, data)


# -- Crush: its settings file is a Bash script (crushrc) of `mcp add` / `mcp remove` lines ------
CRUSH_LINE = f"mcp add {SERVER} --command {COMMAND[0]} " + " ".join(f"--args {a}" for a in COMMAND[1:])
CRUSH_MARK = "# added by Homa Lawn (homalawn desktop uninstall removes it)"


def _crush_entry(path: Path) -> dict | None:
    """The last `mcp add homalawn` / `mcp remove homalawn` line wins (crushrc runs top to bottom)."""
    out = None
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        try:
            words = shlex.split(line)
        except ValueError:
            continue
        if len(words) < 3 or words[0] != "mcp" or words[2] != SERVER:
            continue
        if words[1] in ("remove", "rm"):
            out = None
        elif words[1] == "add":
            out, i = {"args": []}, 3
            while i < len(words) - 1:
                if words[i] == "--command":
                    out["command"] = words[i + 1]
                elif words[i] == "--args":
                    out["args"].append(words[i + 1])
                i += 2 if words[i].startswith("--") else 1
    return out


def _crush_write() -> None:
    path = crush_config()
    text = path.read_text() if path.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + CRUSH_MARK + "\n" + CRUSH_LINE + "\n")


def _crush_erase() -> None:
    path = crush_config()
    if not path.exists():
        return
    lines = path.read_text().splitlines(keepends=True)
    keep = [l for l in lines if l.strip() not in (CRUSH_LINE, CRUSH_MARK)]
    if keep != lines:
        path.write_text("".join(keep))


# -- the registry -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Agent:
    id: str                                   # Omarchy's name for it (omarchy-default-agent)
    name: str
    exe: str
    config: Callable[[], Path]                # where it keeps its user-level MCP servers
    read: Callable[[Path], dict | None]       # our entry there, or None
    source: str                               # where the format was checked
    # registering through the agent's own command: (argv, stdin)
    add: tuple[list[str], str | None] | None = None
    remove: tuple[list[str], str | None] | None = None
    add_refuses_existing: bool = False        # its add fails on a name it already has
    # or, for an agent without such a command, editing its file: write our entry / take it out
    write: Callable[[], None] | None = None
    erase: Callable[[], None] | None = None


REGISTRY: dict[str, Agent] = {a.id: a for a in [
    Agent("claude", "Claude Code", "claude", claude_config, _json_entry("mcpServers"),
          source="claude 2.1.300 `claude mcp add --help`, run against a temporary CLAUDE_CONFIG_DIR "
                 "(writes mcpServers.homalawn {type: stdio, command, args, env} to .claude.json)",
          add=(["claude", "mcp", "add", "--scope", "user", SERVER, "--", *COMMAND], None),
          remove=(["claude", "mcp", "remove", "--scope", "user", SERVER], None),
          add_refuses_existing=True),
    Agent("codex", "Codex", "codex", codex_config, _toml_entry,
          source="codex-cli 0.162.1 `codex mcp add --help`, run against a temporary CODEX_HOME "
                 "(writes [mcp_servers.homalawn] command/args to config.toml; add replaces)",
          add=(["codex", "mcp", "add", SERVER, "--", *COMMAND], None),
          remove=(["codex", "mcp", "remove", SERVER], None)),
    Agent("gemini", "Gemini", "gemini", gemini_config, _json_entry("mcpServers", jsonc=True),
          source="gemini 0.64.0 `gemini mcp add --help`, run against a temporary GEMINI_CLI_HOME "
                 "(writes mcpServers.homalawn {command, args} to ~/.gemini/settings.json; add replaces)",
          add=(["gemini", "mcp", "add", "--scope", "user", SERVER, *COMMAND], None),
          remove=(["gemini", "mcp", "remove", "--scope", "user", SERVER], None)),
    Agent("hermes", "Hermes", "hermes", hermes_config, _hermes_entry,
          source="hermes 0.19.0 `hermes mcp add --help`, run against a temporary HERMES_HOME "
                 "(asks to overwrite and to enable all tools; remove asks to confirm)",
          add=(["hermes", "mcp", "add", SERVER, "--command", COMMAND[0], "--args", *COMMAND[1:]], "y\ny\n"),
          remove=(["hermes", "mcp", "remove", SERVER], "y\n")),
    Agent("copilot", "GitHub Copilot", "copilot", copilot_config, _json_entry("mcpServers"),
          source="docs.github.com copilot-cli cli-command-reference (`copilot mcp add <name> -- <command>`, "
                 "`copilot mcp remove`; $COPILOT_HOME/mcp-config.json mcpServers), @github/copilot 1.0.98, 2026-10-15",
          add=(["copilot", "mcp", "add", SERVER, "--", *COMMAND], None),
          remove=(["copilot", "mcp", "remove", SERVER], None),
          add_refuses_existing=True),
    Agent("grok", "Grok", "grok", grok_config, _toml_entry,
          source="docs.x.ai/build/features/mcp-servers and /build/settings (`grok mcp add <name> -- <command>`, "
                 "`grok mcp remove`; $GROK_HOME/config.toml [mcp_servers.<name>]), @xai-official/grok 1.0.52",
          add=(["grok", "mcp", "add", SERVER, "--", *COMMAND], None),
          remove=(["grok", "mcp", "remove", SERVER], None),
          add_refuses_existing=True),
    Agent("pi", "Pi", "pi", pi_config, _json_entry("mcpServers"),
          source="earendil-works/pi a4f67eb docs/cli.md 'MCP commands' (`pi mcp add <name> -- <command>`, "
                 "`pi mcp remove`), src/config.ts getAgentDir ($PI_CODING_AGENT_DIR or ~/.pi/agent)/mcp.json; pi 1.2.0",
          add=(["pi", "mcp", "add", SERVER, "--", *COMMAND], None),
          remove=(["pi", "mcp", "remove", SERVER], None),
          add_refuses_existing=True),
    Agent("omp", "Oh My Pi", "omp", omp_config, _json_entry("mcpServers"),
          source="can1357/oh-my-pi 55c065e docs/mcp-config.md, mcp/config-writer.ts (no shell command; user "
                 "file ~/.omp/agent/mcp.json, plain JSON, mcpServers.<name> {type: stdio, command, args})",
          write=lambda: _json_set(omp_config(), ("mcpServers",),
                                  {"type": "stdio", "command": COMMAND[0], "args": COMMAND[1:]}),
          erase=lambda: _json_del(omp_config(), ("mcpServers",))),
    Agent("cursor-agent", "Cursor CLI", "cursor-agent", cursor_config, _json_entry("mcpServers"),
          source="cursor.com/docs/cli/mcp and /docs/context/mcp (no add command; ~/.cursor/mcp.json "
                 "mcpServers.<name> {type: stdio, command, args}), fetched 2026-10-15",
          write=lambda: _json_set(cursor_config(), ("mcpServers",),
                                  {"type": "stdio", "command": COMMAND[0], "args": COMMAND[1:]}),
          erase=lambda: _json_del(cursor_config(), ("mcpServers",))),
    Agent("crush", "Crush", "crush", crush_config, _crush_entry,
          source="charmbracelet/crush v0.98.1 docs/config/README.md 'mcp', internal/shellconfig/mcp.go "
                 "(crushrc line `mcp add <name> --command <cmd> --args <arg>`; later lines win), "
                 "internal/config/load.go ($CRUSH_GLOBAL_CONFIG or $XDG_CONFIG_HOME/crush)",
          write=_crush_write, erase=_crush_erase),
    Agent("openclaw", "OpenClaw", "openclaw", openclaw_config, _json_entry("mcp", "servers", jsonc=True),
          source="openclaw/openclaw 540d61c4 docs/cli/mcp/registry.md (`openclaw mcp set <name> <json>`, "
                 "`openclaw mcp unset`; needs the Gateway stopped), src/config/paths.ts; openclaw 2026.10.3",
          add=(["openclaw", "mcp", "set", SERVER, json.dumps({"command": COMMAND[0], "args": COMMAND[1:]})], None),
          remove=(["openclaw", "mcp", "unset", SERVER], None)),
]}

# Agents of Omarchy's menu Homa Lawn does not register with, and why.
UNSUPPORTED: dict[str, str] = {
    "opencode": "OpenCode: Omarchy installs it from mise, whose current version (2.0.x) has a new, not yet "
                "documented server format (mcp.servers.<name>, `mcp add --global`); the documented 1.19 "
                "format would be wrong for it, and it has no remove command.",
    "muse": "Muse Code: no command to add a server, and its documentation (mcp_servers + transport) and its "
            "1.4.9 program (mcpServers + type; both keys together drop every server) disagree.",
}

# Kept for callers from before the registry.
AGENTS = REGISTRY


# -- installed? ---------------------------------------------------------------------------------
def _stub_package(path: Path) -> str | None:
    """The mise package of an Omarchy wrapper (`mise use -g [--opts] <package>`), None when `path`
    is not one (a real program, a link, the user's own script)."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > STUB_MAX_BYTES:
            return None
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("#!"):
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("mise use -g"):
            continue
        try:
            words = shlex.split(line.split("||")[0])
        except ValueError:
            continue
        pkgs = [w for w in words[3:] if not w.startswith("-")]
        if pkgs and not re.search(r"[^/:]@", pkgs[-1]):   # not a helper such as `uv@latest`
            return pkgs[-1]
    return None


def installed(agent: Agent, run: Callable = subprocess.run) -> bool:
    """Whether the agent can run here without installing itself first."""
    found = shutil.which(agent.exe)
    if not found:
        return False
    pkg = _stub_package(Path(found))
    if pkg is None:
        return True
    if not shutil.which("mise"):
        return False
    try:
        r = run(["mise", "where", pkg], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


# -- what Homa Lawn added ------------------------------------------------------------------------
ADDED, USER, DECLINED = "added", "user", "declined"
# ADDED: Homa Lawn added the entry (uninstall removes it). USER: the entry was there before Homa
# Lawn looked (the user's own; never touched). DECLINED: Homa Lawn added it and the user removed it.


def _read_file() -> dict:
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_state() -> dict:
    agents = _read_file().get("agents")
    return {k: v for k, v in (agents if isinstance(agents, dict) else {}).items()
            if isinstance(v, dict) and v.get("state")}


def _write_file(data: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)


def _save_state(state: dict) -> None:
    _write_file({**_read_file(), "agents": state})


# `desktop install --no-agents` switches adding off (the service then adds Homa Lawn to no agent
# installed later either); a plain `desktop install` switches it back on. Connect on the Agents
# page works either way: it is the person's own choice for one agent.
def auto_add() -> bool:
    """Whether Homa Lawn adds its tool server to the coding agents installed here by itself."""
    return _read_file().get("auto", True) is not False


def set_auto_add(on: bool) -> None:
    with _locked():
        _write_file({**_read_file(), "auto": bool(on)})


def installed_names(run: Callable = subprocess.run) -> list[str]:
    """The supported coding agents installed here, by name, Omarchy's default first."""
    return [r["name"] for r in status(run)]


@contextlib.contextmanager
def _locked():
    """One writer of agents.json (and of the agents' files) at a time: the CLI and the service."""
    p = state_path().with_name("agents.lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _mark(state: dict, aid: str, what: str) -> None:
    state[aid] = {"state": what, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


# -- status -------------------------------------------------------------------------------------
def _entry(agent: Agent) -> tuple[dict | None, str | None]:
    path = agent.config()
    try:
        return (agent.read(path) if path.is_file() else None), None
    except (OSError, ValueError) as e:
        return None, f"cannot read {path}: {e}"


def _row(agent: Agent, state: dict, default: str | None) -> dict:
    entry, error = _entry(agent)
    return {"id": agent.id, "name": agent.name, "installed": True, "default": agent.id == default,
            "config": str(agent.config()), "registered": entry is not None,
            "enabled": _enabled_of(entry) if entry is not None else None,
            "command": _command_of(entry) if entry is not None else None, "error": error,
            "managed": (state.get(agent.id) or {}).get("state")}


def status(run: Callable = subprocess.run) -> list[dict]:
    """The supported agents installed here, Omarchy's default first: {id, name, installed (True),
    default, config (file path), registered, enabled (False when the agent has it switched off),
    command (what it runs, e.g. "homalawn mcp"), error (the file could not be read), managed
    ("added" by Homa Lawn, the "user"'s own, "declined" = removed after we added it, or None)}."""
    default, state = omarchy_default(), _load_state()
    rows = [_row(a, state, default) for a in REGISTRY.values() if installed(a, run)]
    rows.sort(key=lambda r: not r["default"])
    return rows


# -- registering --------------------------------------------------------------------------------
def _run(run, cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    found = shutil.which(cmd[0])
    if not found:
        raise AgentError(f"{cmd[0]} is not installed (not on PATH)")
    try:
        # from the home folder: some agents (Gemini) note the folder they were started in
        return run([found, *cmd[1:]], input=stdin, capture_output=True, text=True, timeout=TIMEOUT_S,
                   env={**os.environ, "HOME": str(home())}, cwd=str(home()))
    except FileNotFoundError:
        raise AgentError(f"{cmd[0]} is not installed (not on PATH)")
    except subprocess.TimeoutExpired:
        raise AgentError(f"`{' '.join(cmd)}` did not finish within {TIMEOUT_S} s")


def _tail(r: subprocess.CompletedProcess) -> str:
    text = ((r.stderr or "") + "\n" + (r.stdout or "")).strip().splitlines()
    return text[-1].strip() if text else f"exit code {r.returncode}"


def _register(agent: Agent, run: Callable) -> None:
    """Add (or replace) our entry; AgentError when the agent's file does not show it afterwards."""
    if agent.add is not None:
        if agent.add_refuses_existing and _entry(agent)[0] is not None:
            r = _run(run, *agent.remove)
            if r.returncode != 0:
                raise AgentError(f"{agent.name} did not remove the old registration: {_tail(r)}")
        r = _run(run, *agent.add)
        if r.returncode != 0:
            raise AgentError(f"{agent.name} did not register Homa Lawn: {_tail(r)}")
        said = _tail(r)
    else:
        agent.write()
        said = "nothing"
    entry, error = _entry(agent)
    if entry is None:
        raise AgentError(error or f"{agent.name} answered, but Homa Lawn is not in {agent.config()}: {said}")
    if not _enabled_of(entry):
        raise AgentError(f"{agent.name} saved Homa Lawn switched off (it could not start `homalawn mcp`): {said}")


def _unregister(agent: Agent, run: Callable) -> None:
    if agent.remove is not None:
        r = _run(run, *agent.remove)
        if r.returncode != 0 and _entry(agent)[0] is not None:
            raise AgentError(f"{agent.name} did not remove Homa Lawn: {_tail(r)}")
    else:
        agent.erase()
    if _entry(agent)[0] is not None:
        raise AgentError(f"Homa Lawn is still in {agent.config()}")


def connect(agent: str, run: Callable = subprocess.run) -> str:
    """(Re)register `homalawn mcp` in one agent (the Agents page's Connect); returns the line to
    show. The entry then counts as Homa Lawn's (uninstall removes it). AgentError when the agent is
    unknown, not installed, or its command fails."""
    a = REGISTRY.get(agent)
    if a is None:
        raise AgentError(f"unknown agent {agent!r} (one of {', '.join(REGISTRY)})")
    if not installed(a, run):
        raise AgentError(f"{a.name} is not installed here ({a.exe} is not on PATH)")
    with _locked():
        _register(a, run)
        state = _load_state()
        _mark(state, a.id, ADDED)
        _save_state(state)
    return f"Homa Lawn is registered in {a.name}; a new {a.name} session can use it."


def sync(run: Callable = subprocess.run, quiet: bool = False) -> list[str]:
    """Register `homalawn mcp` with every supported agent installed here that Homa Lawn has not
    decided about yet; one plain line per installed agent (with `quiet`, only lines for agents
    that changed: what the service logs). An entry the user made, or removed after we added it,
    is left alone. Nothing at all while adding is switched off (`desktop install --no-agents`)."""
    out: list[str] = []
    if not auto_add():
        return out
    with _locked():
        state = _load_state()
        for a in REGISTRY.values():
            if not installed(a, run):
                continue
            rec = (state.get(a.id) or {}).get("state")
            entry, error = _entry(a)
            if error:
                out.append(f"{a.name}: could not read its settings ({error}); Homa Lawn was not added.")
                continue
            if entry is not None:
                if rec is None or rec == DECLINED:
                    _mark(state, a.id, USER)
                    if not quiet:
                        out.append(f"{a.name} already had Homa Lawn; left as it was.")
                elif not quiet:
                    out.append(f"{a.name} can use your phone (Homa Lawn is in its settings).")
                continue
            if rec == ADDED:
                _mark(state, a.id, DECLINED)
                out.append(f"{a.name}: Homa Lawn was removed from it, so it was not added again "
                           "(Connect on the Agents page adds it back).")
                continue
            if rec in (USER, DECLINED):
                if not quiet:
                    out.append(f"{a.name}: Homa Lawn was removed from it, so it was not added again "
                               "(Connect on the Agents page adds it back).")
                continue
            try:
                _register(a, run)
            except AgentError as e:
                out.append(f"{a.name}: could not add Homa Lawn ({e}); it will be tried again.")
                continue
            _mark(state, a.id, ADDED)
            out.append(f"{a.name} can now use your phone: Homa Lawn was added to its settings.")
        _save_state(state)
    if not out and not quiet:
        out.append("No coding agent Homa Lawn works with is installed yet; the service adds it to "
                   "one installed later.")
    return out


def unregister_all(run: Callable = subprocess.run) -> list[str]:
    """Remove the entries Homa Lawn added (`desktop uninstall`), only while they still run
    `homalawn mcp`; the user's own entries are left. One line per agent touched."""
    out: list[str] = []
    with _locked():
        state = _load_state()
        for aid, rec in list(state.items()):
            if rec.get("state") != ADDED:
                continue
            a = REGISTRY.get(aid)
            del state[aid]
            if a is None:
                continue
            entry, _ = _entry(a)
            if entry is None:
                if a.erase is not None:
                    a.erase()             # Crush: our line, switched off by a line of the user's below it
                continue
            if _command_of(entry) != " ".join(COMMAND):
                out.append(f"{a.name}: its Homa Lawn entry was changed since, so it was left as it is.")
                continue
            if a.remove is not None and not shutil.which(a.exe):
                out.append(f"{a.name} is not installed any more; its settings were left as they are.")
                continue
            try:
                _unregister(a, run)
            except AgentError as e:
                out.append(f"{a.name}: could not remove Homa Lawn ({e}).")
                state[aid] = rec
                continue
            out.append(f"Removed Homa Lawn from {a.name}.")
        _save_state(state)
    return out
