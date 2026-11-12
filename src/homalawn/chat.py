"""The chat on the Agents page: the user's Omarchy default coding agent, run without a terminal,
answering about the selected phone through Homa Lawn's tool server (`homalawn mcp`).

One conversation at a time. Each message the user sends runs the agent once, non-interactively;
the agent's own session id is kept, so the next message continues the same conversation. What
the agent writes streams to the window as it arrives, and each tool it uses shows as a short step
in plain words ("Placed an app"). Only Homa Lawn's tools are allowed where the agent lets us say
so: no shell, no files, no web.

The pattern (run the agent's CLI headless with streamed JSON, keep only answer text and fixed
step labels, continue by session id) follows Omamail's agent bridge (scripts/agent-job.py and
src/agent/provider.rs, MIT, Copyright (c) 2026 Jason Lee); no code was copied.

Every flag below was checked against the installed CLI and its vendor's documentation or source;
the source is named next to each agent in SPECS. An agent not listed there is not supported in
the chat yet (the window says so; it still works from the terminal).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import time
import types
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import env

log = logging.getLogger("homalawn.chat")

SERVER = "homalawn"
RUN_TIMEOUT_S = 15 * 60          # one answer; tool calls on a slow phone can take minutes
EVENT_LIMIT = 1024 * 1024        # one line of the agent's stream
STDERR_KEEP = 16 * 1024
TEXT_LIMIT = 64 * 1024           # one message
MESSAGES_KEEP = 300
INPUT_LIMIT = 8 * 1024           # what the user may type in one message
PUSH_EVERY_S = 0.1               # streamed text reaches the window at most this often

PREAMBLE = (
    "You are answering in the chat of the Homa Lawn window on the user's Omarchy desktop. The user "
    "asks about their Android phone. Act only through the homalawn tools (phone_*, screen_*, recipe_*) "
    "and follow that tool server's rules; you have no shell, files or web here. Answer briefly, in "
    "plain words for someone who does not read code: never show tool names, file paths or JSON."
)

# Homa Lawn's tools as a person would say what happened (the window shows "used: ...").
TOOL_LABELS = {
    "phone_status": "Checked the phone", "phone_apps": "Listed the apps", "phone_layout": "Read the home screen",
    "phone_layout_apply": "Rearranged the home screen", "phone_place": "Placed an app",
    "phone_remove": "Removed an item", "phone_folder_create": "Made a folder", "phone_grid": "Changed the grid",
    "phone_prefs": "Read the launcher settings", "phone_pref_set": "Changed a launcher setting",
    "phone_widgets": "Listed the widgets", "phone_widget_add": "Added a widget",
    "phone_wallpaper": "Changed the wallpaper", "phone_screenshot": "Took a screenshot",
    "phone_snapshots": "Listed the saved states", "phone_snapshot_save": "Saved the home screen",
    "phone_snapshot_restore": "Put back a saved home screen", "phone_undo": "Undid the last change",
    "phone_nova_import": "Imported a Nova home screen",
    "screen_status": "Checked screen control", "screen_tree": "Read the screen", "screen_shot": "Looked at the screen",
    "screen_tap": "Tapped", "screen_long_press": "Long-pressed", "screen_swipe": "Swiped",
    "screen_type": "Typed text", "screen_key": "Pressed a key", "screen_launch": "Opened an app",
    "recipe_list": "Looked up the recipes", "recipe_get": "Read a recipe",
}


def tool_label(name: str) -> str:
    """A tool name as an agent reports it (Claude `mcp__homalawn__phone_place`, Gemini
    `mcp_homalawn_phone_place`, Codex `phone_place` from server homalawn) in plain words."""
    for prefix in (f"mcp__{SERVER}__", f"mcp_{SERVER}_", f"{SERVER}__", f"{SERVER}."):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    else:
        if name not in TOOL_LABELS:
            return "Used a tool"
    return TOOL_LABELS.get(name) or name.replace("_", " ").capitalize()


def _clean(text: str) -> str:
    """Text fit to show: no control characters but line breaks and tabs."""
    return "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) <= 159))


def _reason(stderr: str) -> str:
    """The line of an agent's error output worth showing: the last one naming an error, else the
    last one that is not a stack frame or a dumped object (a real Gemini CLI that cannot sign in
    ends its output with `    at process.processTicksAndRejections (node:internal/...)`)."""
    lines = [l.strip() for l in _clean(stderr).splitlines()]
    lines = [l for l in lines if l and not l.startswith("at ") and l not in ("{", "}", "[", "]", "},", "],")]
    named = [l for l in lines if re.search(r"error", l, re.I) and not re.match(r"^\w+:\s*['\"\[{]", l)]
    pick = (named or lines or [""])[-1]
    return pick[:300]


# -- stream parsers ---------------------------------------------------------------------------------
# Each turns one JSON line of an agent's stream into actions for the conversation:
#   ("session", id)  ("text", delta)  ("tool", raw name)  ("break",)  ("done", final text or "")
#   ("fail", message)


class ClaudeParser:
    """`claude -p --output-format stream-json --verbose --include-partial-messages`: `system`
    (init, with session_id), `stream_event` (the API's message_start / content_block_start /
    content_block_delta text_delta), `assistant` and `user` snapshots, and a final `result`
    {subtype, is_error, result, session_id}. Shapes: Claude Code 2.1.300 and Omamail's tested
    parser (scripts/agent-job.py ClaudeStream)."""

    def __init__(self):
        self.streamed = False      # partial events arrived: the snapshots only repeat them
        self.wrote = False

    def feed(self, ev: dict) -> list[tuple]:
        if not isinstance(ev, dict) or ev.get("parent_tool_use_id"):
            return []              # a subagent's inner messages are not the answer
        out: list[tuple] = []
        sid = ev.get("session_id")
        if isinstance(sid, str) and sid:
            out.append(("session", sid))
        kind = ev.get("type")
        if kind == "stream_event":
            self.streamed = True
            e = ev.get("event") or {}
            t = e.get("type")
            if t == "message_start":
                out.append(("break",))
            elif t == "content_block_start":
                block = e.get("content_block") or {}
                if block.get("type") == "tool_use":
                    out.append(("tool", str(block.get("name") or "")))
                elif block.get("type") == "text" and block.get("text"):
                    self.wrote = True
                    out.append(("text", block["text"]))
            elif t == "content_block_delta":
                d = e.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    self.wrote = True
                    out.append(("text", d["text"]))
        elif kind == "assistant" and not self.streamed:
            out.append(("break",))
            for block in (ev.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    self.wrote = True
                    out.append(("text", block["text"]))
                elif block.get("type") == "tool_use":
                    out.append(("tool", str(block.get("name") or "")))
                    out.append(("break",))
        elif kind == "result":
            if ev.get("is_error") or ev.get("subtype") != "success":
                msg = ev.get("result") if isinstance(ev.get("result"), str) else ""
                errs = ev.get("errors")
                if not msg and isinstance(errs, list) and errs:
                    msg = str(errs[0])
                out.append(("fail", msg or f"it stopped ({ev.get('subtype') or 'error'})"))
            else:
                out.append(("done", "" if self.wrote else str(ev.get("result") or "")))
        return out


class CodexParser:
    """`codex exec --json`: thread.started {thread_id}, turn.started, item.started /
    item.updated / item.completed {item: {type: agent_message {text} | mcp_tool_call {server,
    tool, status} | ...}}, turn.completed, turn.failed {error: {message}}, error {message}.
    Shapes: openai/codex sdk/typescript/src/events.ts and items.ts (main 111362a6, 2026-10-15),
    learn.chatgpt.com/docs/non-interactive-mode, codex-cli 0.162.1. Codex sends each message
    whole (no deltas)."""

    def feed(self, ev: dict) -> list[tuple]:
        if not isinstance(ev, dict):
            return []
        kind = ev.get("type")
        item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
        if kind == "thread.started" and isinstance(ev.get("thread_id"), str):
            return [("session", ev["thread_id"])]
        if kind == "item.started" and item.get("type") == "mcp_tool_call":
            server, tool = str(item.get("server") or ""), str(item.get("tool") or "")
            return [("tool", f"{server}__{tool}" if server else tool)]
        if kind == "item.started" and item.get("type") in ("command_execution", "file_change", "web_search"):
            return [("tool", item["type"])]
        if kind == "item.completed" and item.get("type") == "agent_message" and item.get("text"):
            return [("break",), ("text", str(item["text"])), ("break",)]
        if kind == "turn.completed":
            return [("done", "")]
        if kind == "turn.failed":
            err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
            return [("fail", str(err.get("message") or "the turn failed"))]
        if kind == "error":
            return [("fail", str(ev.get("message") or "error"))]
        return []


class GeminiParser:
    """`gemini --output-format stream-json`: init {session_id, model}, message {role, content,
    delta}, tool_use {tool_name, tool_id, parameters}, tool_result {tool_id, status}, error
    {severity, message}, result {status, stats}. Shapes: the emitter in Gemini CLI 0.64.0's own
    bundle (packages/cli nonInteractiveCli, JsonStreamEventType) and geminicli.com/docs/cli/headless."""

    def feed(self, ev: dict) -> list[tuple]:
        if not isinstance(ev, dict):
            return []
        kind = ev.get("type")
        if kind == "init" and isinstance(ev.get("session_id"), str):
            return [("session", ev["session_id"])]
        if kind == "message" and ev.get("role") == "assistant" and ev.get("content"):
            return [("text", str(ev["content"]))]
        if kind == "tool_use":
            return [("tool", str(ev.get("tool_name") or "")), ("break",)]
        if kind == "error" and ev.get("severity") == "error":
            return [("fail", str(ev.get("message") or "error"))]
        if kind == "result":
            if ev.get("status") == "success":
                return [("done", "")]
            return [("fail", "it could not finish")]
        return []


# -- the agents ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Spec:
    id: str
    name: str
    exe: str
    parser: Callable[[], object]
    source: str
    stdin_prompt: bool = True
    # the chat cannot hand this agent the tool server itself: Homa Lawn must be in its settings
    needs_entry: bool = False


SPECS: dict[str, Spec] = {s.id: s for s in [
    Spec("claude", "Claude Code", "claude", ClaudeParser,
         source="claude 2.1.300 `claude --help` (-p, --output-format stream-json, --verbose, "
                "--include-partial-messages, --permission-mode dontAsk, --tools \"\", --allowedTools, "
                "--strict-mcp-config, --mcp-config, --append-system-prompt, --setting-sources, -r/--resume <session id>); "
                "code.claude.com/docs/en/cli-reference (--setting-sources: which of user, project, local "
                "settings load; empty loads none: a real run then listed no user plugins, ran no hooks and "
                "had no CLAUDE.md; --safe-mode was not usable, it dropped --mcp-config servers too); "
                "code.claude.com/docs/en/permissions (`mcp__<server>__*` allow rules; dontAsk denies "
                "anything not allowed); Omamail src/agent/provider.rs uses the same flags"),
    Spec("codex", "Codex", "codex", CodexParser,
         source="codex-cli 0.162.1 `codex exec --help`, `codex exec resume --help`, `codex features list` "
                "(shell_tool, unified_exec, multi_agent, apps, plugins, remote_plugin, tool_suggest, image_generation, "
                "view_image, goals, hooks; `--disable <FEATURE>` = `-c features.<name>=false`); learn.chatgpt.com/docs/config-file/config-reference "
                "(approval_policy, sandbox_mode, web_search, mcp_servers.<id>.{command,args,env,enabled,"
                "default_tools_approval_mode,required}); flag order as openai/codex sdk/typescript/src/exec.ts"),
    Spec("gemini", "Gemini", "gemini", GeminiParser, stdin_prompt=False, needs_entry=True,
         source="gemini 0.64.0 `gemini --help` (--prompt, --output-format stream-json, --skip-trust, "
                "--allowed-mcp-server-names, --policy, --session-id, -r/--resume <uuid>); its bundle: policy "
                "TOML [[rule]] {toolName, mcpName, decision, priority}, headless turns `ask_user` into deny, "
                "MCP tools named mcp_<server>_<tool>, sessionUtils `--resume {uuid}`"),
]}


def names() -> dict[str, str]:
    """Omarchy's agent ids and names, for agents the chat does not support."""
    from . import agents
    return {a.id: a.name for a in agents.REGISTRY.values()} | {"opencode": "OpenCode", "muse": "Muse Code"}


def workdir() -> Path:
    """Where the agents run: their sessions are kept per folder, so always this one."""
    d = env.state_dir() / "chat"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _mcp_command() -> list[str]:
    found = shutil.which("homalawn")
    return [found or "homalawn", "mcp"]


def _server_env(phone: str | None) -> dict[str, str]:
    """What the tool server needs to find the same phones as this service: our HOMALAWN_* and XDG
    folders, and the chosen phone (HOMALAWN_PHONE)."""
    out = {k: v for k, v in os.environ.items()
           if k.startswith("HOMALAWN_") or k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                                                  "XDG_CACHE_HOME", "XDG_RUNTIME_DIR")}
    out.pop("HOMALAWN_SOCKET", None)
    if phone:
        out["HOMALAWN_PHONE"] = phone
    return out


def _toml_str(s: str) -> str:
    return json.dumps(s)          # a JSON string is a TOML basic string


def _codex_other_servers() -> list[str]:
    """The MCP servers in the user's Codex config other than ours (switched off for the chat)."""
    import tomllib
    from . import agents
    try:
        data = tomllib.loads(agents.codex_config().read_text())
    except (OSError, ValueError):
        return []
    servers = data.get("mcp_servers") if isinstance(data.get("mcp_servers"), dict) else {}
    return [k for k in servers if k != SERVER and re.fullmatch(r"[A-Za-z0-9_-]+", k)]


# Codex features that add tools beyond Homa Lawn's (a real run listed them: ChatGPT apps and
# plugins, image generation, reading local images, goals) or run the user's own hooks.
CODEX_OFF = ("apps", "plugins", "remote_plugin", "tool_suggest", "image_generation", "view_image",
             "goals", "hooks")


def command(agent: str, prompt: str, session: str | None, phone: str | None, cwd: Path) -> tuple[list[str], bytes | None, str | None]:
    """(argv, stdin, the session id this run will have when the agent lets us choose it)."""
    srv_env = _server_env(phone)
    cmd = _mcp_command()
    if agent == "claude":
        mcp = {"mcpServers": {SERVER: {"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": srv_env}}}
        argv = ["claude", "-p", "--verbose", "--output-format", "stream-json", "--include-partial-messages",
                "--permission-mode", "dontAsk", "--tools", "", "--allowedTools", f"mcp__{SERVER}__*",
                "--strict-mcp-config", "--mcp-config", json.dumps(mcp), "--append-system-prompt", PREAMBLE,
                # none of the user's settings: their plugins, hooks, output style and CLAUDE.md stay out
                # of the chat (sign-in is not a setting and still works)
                "--setting-sources", ""]
        if session:
            argv += ["--resume", session]
        return argv, prompt.encode(), None
    if agent == "codex":
        env_toml = "{" + ",".join(f"{k}={_toml_str(v)}" for k, v in srv_env.items()) + "}"
        server = (f"{{command={_toml_str(cmd[0])},args=[{','.join(_toml_str(a) for a in cmd[1:])}],"
                  f"env={env_toml},default_tools_approval_mode=\"approve\",required=true}}")
        argv = ["codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only",
                "--disable", "shell_tool", "--disable", "unified_exec", "--disable", "multi_agent",
                *[a for f in CODEX_OFF for a in ("--disable", f)],
                "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                "-c", f"mcp_servers.{SERVER}={server}"]
        for other in _codex_other_servers():
            argv += ["-c", f"mcp_servers.{other}.enabled=false"]
        if session:
            argv += ["resume", session]
        argv.append("-")
        text = prompt if session else PREAMBLE + "\n\n" + prompt
        return argv, text.encode(), None
    if agent == "gemini":
        policy = cwd / "gemini-policy.toml"
        policy.write_text(
            "# Written by Homa Lawn for its chat: only Homa Lawn's tools.\n"
            "[[rule]]\ntoolName = \"*\"\ndecision = \"deny\"\npriority = 900\n\n"
            f"[[rule]]\nmcpName = \"{SERVER}\"\ntoolName = \"*\"\ndecision = \"allow\"\npriority = 999\n")
        new = None
        argv = ["gemini", "--output-format", "stream-json", "--skip-trust", "--allowed-mcp-server-names", SERVER,
                "--policy", str(policy)]
        if session:
            argv += ["--resume", session]
        else:
            new = str(uuid.uuid4())
            argv += ["--session-id", new]
        text = prompt if session else PREAMBLE + "\n\n" + prompt
        argv.append("--prompt=" + text)
        return argv, None, new
    raise ValueError(f"unsupported agent {agent!r}")


def _installed(exe: str) -> bool:
    from . import agents
    return agents.installed(types.SimpleNamespace(exe=exe))


def _has_entry(agent: str) -> bool:
    """Homa Lawn is in that agent's own settings, switched on."""
    from . import agents
    a = agents.REGISTRY.get(agent)
    if a is None:
        return False
    entry, _ = agents._entry(a)
    return entry is not None and agents._enabled_of(entry)


class ChatError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Chat:
    """The conversation and the one agent run that may be going. `on_change()` is called (from the
    event loop) whenever the window should be told; `default()` names Omarchy's default agent."""

    def __init__(self, default: Callable[[], str | None], on_change: Callable[[], None] = lambda: None,
                 path: Path | None = None, installed: Callable[[str], bool] = _installed,
                 has_entry: Callable[[str], bool] = _has_entry, timeout: float = RUN_TIMEOUT_S):
        self._default = default
        self.on_change = on_change
        self.path = path or (env.state_dir() / "chat.json")
        self._installed = installed
        self._has_entry = has_entry
        self.timeout = timeout
        self.messages: list[dict] = []
        self.session: str | None = None        # the agent's own conversation id
        self.session_agent: str | None = None  # which agent it belongs to
        self.phone: str | None = None          # the phone's name, as the window and the agent are told
        self.running = False
        self.who: dict | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._cancelled = False
        self._open = False                     # the last agent message is still being written
        self._push: asyncio.TimerHandle | None = None
        self._pushed = 0.0
        self._load()

    # -- saved conversation ---------------------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        msgs = data.get("messages")
        if isinstance(msgs, list):
            self.messages = [m for m in msgs if isinstance(m, dict) and isinstance(m.get("text"), str)
                             and m.get("role") in ("user", "agent", "step", "note")][-MESSAGES_KEEP:]
        for k in ("session", "session_agent", "phone"):
            v = data.get(k)
            setattr(self, k, v if isinstance(v, str) and v else None)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"messages": self.messages, "session": self.session,
                                       "session_agent": self.session_agent, "phone": self.phone}))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("cannot save the chat in %s: %s", self.path, e)

    # -- what the window shows ------------------------------------------------------------------
    def agent(self) -> dict:
        """Who answers: {id, name, supported, installed, setup ("install" / "connect" when it
        cannot answer yet), why}. May run `mise where` (an Omarchy
        wrapper not yet installed): call it in a thread; snapshot() reuses the last answer."""
        self.who = self._who()
        return self.who

    def _who(self) -> dict:
        aid = self._default()
        if not aid:
            return {"id": None, "name": None, "supported": False, "installed": False,
                    "why": "No default coding agent is chosen in Omarchy yet."}
        spec = SPECS.get(aid)
        name = spec.name if spec else names().get(aid, aid)
        if spec is None:
            return {"id": aid, "name": name, "supported": False, "installed": True,
                    "why": f"{name} is not supported in this chat yet. You can still use it from the "
                           "terminal: Homa Lawn is in its settings."}
        if not self._installed(spec.exe):
            return {"id": aid, "name": name, "supported": True, "installed": False, "setup": "install",
                    "why": f"{name} is your default agent but is not installed yet. Open it once from "
                           "the terminal to install it and sign in."}
        if spec.needs_entry and not self._has_entry(aid):
            return {"id": aid, "name": name, "supported": True, "installed": False, "setup": "connect",
                    "why": f"Homa Lawn is not in {name}'s settings, so it cannot reach your phone. "
                           "Connect it in Settings, then come back."}
        return {"id": aid, "name": name, "supported": True, "installed": True, "why": None}

    def snapshot(self) -> dict:
        return {"agent": self.who or self.agent(), "messages": list(self.messages), "running": self.running,
                "phone": self.phone, "continues": bool(self.session)}

    # -- the conversation -------------------------------------------------------------------------
    def _add(self, role: str, text: str, **extra) -> dict:
        m = {"role": role, "text": text[:TEXT_LIMIT], "at": time.time(), **extra}
        self.messages.append(m)
        del self.messages[:-MESSAGES_KEEP]
        return m

    def _apply(self, action: tuple) -> None:
        kind = action[0]
        if kind == "session":
            self.session = action[1]
        elif kind == "break":
            self._open = False
        elif kind == "text":
            text = _clean(action[1])
            if not text:
                return
            if self._open and self.messages and self.messages[-1]["role"] == "agent":
                last = self.messages[-1]
                last["text"] = (last["text"] + text)[:TEXT_LIMIT]
            else:
                self._add("agent", text.lstrip("\n"))
                self._open = True
        elif kind == "tool":
            self._open = False
            self._add("step", tool_label(action[1]), tool=action[1][:200])

    def _soon(self) -> None:
        """Tell the window, at most every PUSH_EVERY_S; the last change is never held back."""
        if self._push is not None:
            return
        wait = self._pushed + PUSH_EVERY_S - time.monotonic()
        if wait <= 0:
            self._pushed = time.monotonic()
            self.on_change()
            return

        def later():
            self._push = None
            self._pushed = time.monotonic()
            self.on_change()
        self._push = asyncio.get_running_loop().call_later(wait, later)

    def new(self) -> None:
        """Forget the conversation (a running answer is stopped first by the caller)."""
        self.messages, self.session, self.session_agent = [], None, None
        self._save()
        self.on_change()

    async def send(self, text: str, phone: str | None, who: dict | None = None, name: str | None = None) -> dict:
        """`phone` is what the tool server is given (the fingerprint); `name` what people are shown."""
        if self.running:
            raise ChatError("busy", "The agent is still answering; wait for it or press Stop.")
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            raise ChatError("invalid", "Type a message first.")
        if len(text) > INPUT_LIMIT:
            raise ChatError("invalid", f"That message is too long (at most {INPUT_LIMIT} characters).")
        who = who or self.agent()
        if not who["supported"] or not who["installed"]:
            raise ChatError("unsupported", who["why"])
        if self.session_agent and self.session_agent != who["id"]:
            self.session = None
            self._add("note", f"Your default agent is now {who['name']}; it starts a new conversation.")
        self.session_agent = who["id"]
        self.phone = name or phone
        self._add("user", text)
        self.running, self._cancelled, self._open = True, False, False
        self._save()
        self.on_change()
        self._task = asyncio.get_running_loop().create_task(self._run(who, text, phone, name or phone))
        return self.snapshot()

    async def cancel(self) -> bool:
        if not self.running:
            return False
        self._cancelled = True
        self._kill()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(self._task), 10)
        return True

    def _kill(self) -> None:
        p = self._proc
        if p is None or p.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(p.pid, signal.SIGTERM)

    async def close(self) -> None:
        """The service stops: stop a running answer."""
        if self.running:
            await self.cancel()

    async def _run(self, who: dict, text: str, phone: str | None, name: str | None) -> None:
        spec = SPECS[who["id"]]
        parser = spec.parser()
        prompt = (f"[Selected phone: {name}]\n" if name else "") + text
        failure: str | None = None
        done = False
        err_tail = bytearray()
        try:
            cwd = workdir()
            argv, stdin, chosen = command(spec.id, prompt, self.session, phone, cwd)
            if chosen:
                self.session = chosen
            exe = shutil.which(argv[0])
            if not exe:
                raise ChatError("not_installed", f"{spec.name} is not installed (not on PATH).")
            child_env = {**os.environ}
            if phone:
                child_env["HOMALAWN_PHONE"] = phone
            self._proc = await asyncio.create_subprocess_exec(
                exe, *argv[1:], cwd=str(cwd), env=child_env, start_new_session=True,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=EVENT_LIMIT)
            proc = self._proc
            if self._cancelled:                 # Stop was pressed while it was starting
                self._kill()

            async def feed_stdin():
                if stdin is None:
                    return
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    proc.stdin.write(stdin)
                    await proc.stdin.drain()
                proc.stdin.close()

            async def read_stderr():
                while True:
                    chunk = await proc.stderr.read(4096)
                    if not chunk:
                        return
                    err_tail.extend(chunk)
                    del err_tail[:-STDERR_KEEP]

            async def read_stdout():
                nonlocal failure, done
                while True:
                    try:
                        line = await proc.stdout.readline()
                    except (asyncio.LimitOverrunError, ValueError):
                        failure = "its answer had a line too long to read"
                        self._kill()
                        return
                    if not line:
                        return
                    if not line.strip():
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue           # a stray non-JSON line (some CLIs print notices)
                    for action in parser.feed(ev):
                        if action[0] == "done":
                            done = True
                            if action[1]:
                                self._apply(("text", action[1]))
                        elif action[0] == "fail":
                            failure = _clean(action[1])[:500]
                        else:
                            self._apply(action)
                    self._soon()

            await asyncio.wait_for(asyncio.gather(feed_stdin(), read_stderr(), read_stdout()), self.timeout)
            code = await asyncio.wait_for(proc.wait(), 10)
            if not self._cancelled and not done and failure is None:
                failure = _reason(err_tail.decode("utf-8", "replace")) or f"it stopped (exit code {code})"
        except asyncio.TimeoutError:
            failure = f"it did not finish within {int(self.timeout // 60) or 1} minutes"
            self._kill()
        except ChatError as e:
            failure = str(e)
        except OSError as e:
            failure = f"it could not start ({e.strerror or e})"
        except Exception as e:          # never leave the chat stuck in "running"
            log.exception("chat run failed")
            failure = str(e) or type(e).__name__
        finally:
            p = self._proc
            if p is not None and p.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(p.pid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(p.wait(), 3)
            self._proc = None
            self._open = False
            if self._cancelled:
                self._add("note", "Stopped.")
            elif failure is not None:
                self._add("note", f"{spec.name} could not answer: {failure}", error=True)
            self.running = False
            if self._push is not None:
                self._push.cancel()
                self._push = None
            self._save()
            self.on_change()
