"""The Agents page's chat against a real `homalawn serve` paired only with the test AVD
(HOMALAWN_SERIAL=emulator-5560), in the offscreen test shell (its own throwaway home; never the
live shell).

- Pictures (a fake agent program on PATH, so no account is used): the empty chat, an answer being
  streamed with its tool step, and a default agent the chat does not support; plus Settings with
  the coding agents list. They go to HOMALAWN_CHAT_SHOTS (default /tmp/claude-1000/homalawn-chat-shots).
- The real thing, opt-in with HOMALAWN_TEST_REAL_CLAUDE=1 (it costs a few cents): the installed,
  signed-in `claude` answers "what apps are in the dock?" through Homa Lawn's tools, run by the
  service with the window's message; the tool server it starts gets this test's own config (paired
  only with the AVD). A small wrapper adds `--model haiku --max-budget-usd 0.30` and keeps the
  stream (HOMALAWN_CHAT_SHOTS/claude-real.jsonl).
- The same for Codex, opt-in with HOMALAWN_TEST_REAL_CODEX=1 (gpt-6-luna at low effort; stream in
  HOMALAWN_CHAT_SHOTS/codex-real.jsonl), and for Gemini, HOMALAWN_TEST_REAL_GEMINI=1 (flash-lite; a
  temporary GEMINI_CLI_HOME with copies of the sign-in files and its own settings, so ~/.gemini is
  only read).

Omarchy's default agent is not read from this computer: the service runs with
agents.omarchy_default answering from a file of the test's own, and agents.sync switched off (the
service would otherwise register Homa Lawn with this computer's agents)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

import test_chat
from test_device_follow import ask
from test_device_shell_apply import HARNESS

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

SHOTS = Path(os.environ.get("HOMALAWN_CHAT_SHOTS", "/tmp/claude-1000/homalawn-chat-shots"))
REAL_CLAUDE = shutil.which("claude")
REAL_CODEX = shutil.which("codex")
REAL_GEMINI = shutil.which("gemini")
REAL_HOMALAWN = shutil.which("homalawn")
GEMINI_AUTH = ("oauth_creds.json", "google_accounts.json")     # what a signed-in Gemini CLI keeps

SERVE = """
import sys
from pathlib import Path
from homalawn import agents, daemon
agents.omarchy_default = lambda: Path(sys.argv[1]).read_text().strip() or None
agents.sync = lambda run=None, quiet=False: []
daemon.run()
"""


class ChatWindow:
    """A temp service (default agent from `default_file`, programs from `bin_dir` first on PATH)
    and the offscreen shell on the Agents page."""

    def __init__(self, tmp_path, name, bin_dir, default="claude", extra_env=None):
        self.name = name
        self.run_dir = Path(tempfile.mkdtemp(prefix="hc"))   # short: unix socket paths are capped
        self.sock = self.run_dir / "homalawnd.sock"
        self.default_file = tmp_path / "default-agent"
        self.default_file.write_text(default)
        self.env = dict(os.environ, HOMALAWN_SOCKET=str(self.sock), HOMALAWN_STATE_DIR=str(tmp_path / "state"),
                        HOMALAWN_OMARCHY_STATE=str(tmp_path / "current"),
                        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", **(extra_env or {}))
        self.env.pop("XDG_RUNTIME_DIR", None)
        self.log = open(tmp_path / "serve.log", "w")
        self.svc = subprocess.Popen([sys.executable, "-c", SERVE, str(self.default_file)],
                                    env=self.env, stdout=self.log, stderr=self.log)
        self.shell_dir = tmp_path / "shell"
        self.shell_log = self.shell_dir / "shell.log"

    def wait_connected(self):
        for _ in range(100):
            if self.sock.exists():
                break
            time.sleep(0.1)
        for _ in range(300):
            if any(p["name"] == self.name and p["connected"] for p in ask(self.sock, "phones")):
                return
            time.sleep(0.1)
        pytest.fail("the service never connected to the phone")

    def harness(self, *args):
        return subprocess.run([str(HARNESS), *args], env=self.env, capture_output=True, text=True, timeout=60)

    def call(self, fn, arg=""):
        r = self.harness("call", str(self.shell_dir), fn, arg)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout.strip()

    def open(self, section="agents"):
        assert self.harness("start", str(self.shell_dir), "--socket", str(self.sock)).returncode == 0
        for _ in range(150):
            if self.shell_log.exists() and "homalawn" in self.shell_log.read_text().lower():
                break
            time.sleep(0.2)
        time.sleep(3)
        self.harness("summon", str(self.shell_dir), section)

    def facts(self):
        return json.loads(self.call("agentsFacts"))

    def edit(self, **e):
        return self.call("agentsEdit", json.dumps(e))

    def wait_for(self, check, what, tries=120):
        for _ in range(tries):
            try:
                got = check()
            except (json.JSONDecodeError, KeyError, TypeError):
                got = None
            if got:
                return got
            time.sleep(0.25)
        pytest.fail(what + "\n" + (self.shell_log.read_text()[-3000:] if self.shell_log.exists() else ""))

    def shot(self, section, file):
        out = SHOTS / file
        out.unlink(missing_ok=True)
        self.call("snapshot", f"{section}:{out}")
        for _ in range(50):
            if out.exists():
                break
            time.sleep(0.2)
        time.sleep(0.5)
        assert out.exists(), f"no picture {out}"
        return out

    def close(self):
        self.harness("stop", str(self.shell_dir))
        self.svc.terminate()
        try:
            self.svc.wait(15)
        except subprocess.TimeoutExpired:
            self.svc.kill()
        self.log.close()
        shutil.rmtree(self.run_dir, ignore_errors=True)


@pytest.fixture
def window_ready(avd_hygiene, link_transport):
    if not shutil.which("qs"):
        pytest.skip("needs quickshell (qs)")
    SHOTS.mkdir(parents=True, exist_ok=True)
    return link_transport.label


# A Claude Code stream that writes, uses a tool, starts its answer and then keeps working.
STREAMING = [
    test_chat.CLAUDE[0], test_chat.CLAUDE[1],
    {"type": "stream_event", "session_id": test_chat.SID, "parent_tool_use_id": None,
     "event": {"type": "content_block_delta", "index": 0,
               "delta": {"type": "text_delta", "text": "I'll read the home screen first."}}},
    test_chat.CLAUDE[5],
    {"type": "stream_event", "session_id": test_chat.SID, "parent_tool_use_id": None,
     "event": {"type": "message_start", "message": {"id": "m2", "role": "assistant", "content": []}}},
    {"type": "stream_event", "session_id": test_chat.SID, "parent_tool_use_id": None,
     "event": {"type": "content_block_delta", "index": 0,
               "delta": {"type": "text_delta", "text": "Your dock has four apps: Phone, Messages, "}}},
    {"type": "stream_event", "session_id": test_chat.SID, "parent_tool_use_id": None,
     "event": {"type": "content_block_start", "index": 1,
               "content_block": {"type": "tool_use", "id": "t2", "name": "mcp__homalawn__phone_apps", "input": {}}}},
]


def test_chat_pictures(window_ready, tmp_path):
    bin_dir = tmp_path / "bin"
    test_chat.fake_agent(bin_dir, "claude", STREAMING, wait=60)
    w = ChatWindow(tmp_path, window_ready, bin_dir)
    try:
        w.wait_connected()
        w.open("agents")
        f = w.wait_for(lambda: (lambda f: f["loaded"] and f["canChat"] and f)(w.facts()), "the chat never loaded")
        assert f["agent"]["name"] == "Claude Code" and f["messages"] == []
        w.shot("agents", "chat-empty.png")

        assert w.edit(send="What apps are in the dock?") == "started"
        f = w.wait_for(lambda: (lambda f: f["running"] and f["messages"][-1]["role"] == "step"
                                and len(f["messages"]) >= 5 and f)(w.facts()), "the answer never streamed in")
        assert [m["role"] for m in f["messages"]] == ["user", "agent", "step", "agent", "step"]
        assert f["messages"][3]["text"].startswith("Your dock has four apps")
        w.shot("agents", "chat-streaming.png")

        assert w.edit(stop=True) == "started"
        f = w.wait_for(lambda: (lambda f: not f["running"] and f)(w.facts()), "Stop did not stop it")
        assert f["messages"][-1]["text"] == "Stopped."
        assert w.edit(new=True) == "started"
        w.wait_for(lambda: w.facts()["messages"] == [], "New chat did not start over")

        w.default_file.write_text("hermes")
        f = w.wait_for(lambda: (lambda f: f["agent"]["id"] == "hermes" and f)(w.facts()),
                       "the page never noticed the new default agent", tries=60)
        assert f["canChat"] is False and "terminal" in f["agent"]["why"]
        w.shot("agents", "chat-unsupported.png")

        # The coding agents card sits on the Agents page (moved from the Service page).
        w.harness("summon", str(w.shell_dir), "agents")
        w.wait_for(lambda: json.loads(w.call("settingsFacts"))["agents"] is not None, "the coding agents never loaded")
        time.sleep(1)
        w.shot("agents", "coding-agents.png")
    finally:
        w.close()


def real_wrapper(bin_dir, exe, real, stream, argv_log, after=1, extra=()):
    """`exe` on PATH that records its arguments, runs the real program with `extra` inserted after
    the first `after` arguments, and keeps a copy of its stream in `stream`."""
    bin_dir.mkdir(exist_ok=True)
    stream.unlink(missing_ok=True)
    (bin_dir / exe).write_text(f"""#!{sys.executable}
import json, subprocess, sys
a = sys.argv[1:]
json.dump(a, open({str(argv_log)!r}, "w"))
p = subprocess.Popen([{real!r}, *a[:{after}], *{list(extra)!r}, *a[{after}:]], stdout=subprocess.PIPE)
with open({str(stream)!r}, "wb") as keep:
    for line in p.stdout:
        keep.write(line); keep.flush()
        sys.stdout.buffer.write(line); sys.stdout.buffer.flush()
sys.exit(p.wait())
""")
    (bin_dir / exe).chmod(0o755)


def ask_dock(w):
    """Send the dock question in the window and wait for the whole answer; the messages."""
    w.wait_connected()
    w.open("agents")
    w.wait_for(lambda: (lambda f: f["loaded"] and f["canChat"])(w.facts()), "the chat never loaded")
    assert w.edit(send="What apps are in the dock?") == "started"
    f = w.wait_for(lambda: (lambda f: not f["running"] and len(f["messages"]) > 1 and f)(w.facts()),
                   "the agent never finished", tries=4 * 240)
    roles = [m["role"] for m in f["messages"]]
    assert roles[0] == "user" and "step" in roles and roles[-1] == "agent", f["messages"]
    assert f["messages"][-1]["text"].strip()
    served = ask(w.sock, "chat.history")                 # the same conversation in the service
    assert served["messages"][-1]["text"] == f["messages"][-1]["text"] and served["continues"]
    return f["messages"]


@pytest.mark.skipif(os.environ.get("HOMALAWN_TEST_REAL_CLAUDE") != "1" or not REAL_CLAUDE,
                    reason="the real Claude Code run is opt-in: HOMALAWN_TEST_REAL_CLAUDE=1 (and claude installed)")
def test_real_claude_answers_through_the_window(window_ready, tmp_path):
    """The installed, signed-in `claude` (haiku, at most $0.30) answers through Homa Lawn's tools
    only, without the user's own plugins, hooks, output style or CLAUDE.md (an "Insight" block from
    an output-style plugin once leaked into the answer)."""
    bin_dir, stream, argv_log = tmp_path / "bin", SHOTS / "claude-real.jsonl", tmp_path / "claude-argv.json"
    real_wrapper(bin_dir, "claude", REAL_CLAUDE, stream, argv_log, after=999,
                 extra=("--model", "haiku", "--max-budget-usd", "0.30"))
    w = ChatWindow(tmp_path, window_ready, bin_dir)
    try:
        msgs = ask_dock(w)
        w.shot("agents", "chat-real-claude.png")
        print("\nreal Claude Code in the window:", json.dumps(msgs, indent=1))
        assert not any("Insight" in m["text"] or "\u2605" in m["text"] for m in msgs), msgs

        # the tool server Claude started saw only this test's config (paired with the AVD alone)
        argv = json.loads(argv_log.read_text())
        server = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["homalawn"]
        assert server["env"]["HOMALAWN_CONFIG"] == os.environ["HOMALAWN_CONFIG"]
        assert server["env"]["HOMALAWN_PHONE"] == window_ready
        events = [json.loads(line) for line in stream.read_text().splitlines() if line.strip()]
        assert not [e for e in events if e.get("type") == "system" and str(e.get("subtype", "")).startswith("hook_")]
        init = next(e for e in events if e.get("type") == "system" and e.get("subtype") == "init")
        print("tools:", init.get("tools"), "servers:", init.get("mcp_servers"),
              "plugins:", [p.get("name") for p in init.get("plugins", [])], "style:", init.get("output_style"))
        assert [s["name"] for s in init.get("mcp_servers", [])] == ["homalawn"]
        assert init["mcp_servers"][0]["status"] == "connected"
        assert all(t.startswith("mcp__homalawn__") for t in init["tools"]), init["tools"]
        assert "explanatory-output-style" not in [p.get("name") for p in init.get("plugins", [])]
        used = [b["name"] for e in events if e.get("type") == "assistant"
                for b in e["message"]["content"] if b.get("type") == "tool_use"]
        assert used and all(n.startswith("mcp__homalawn__") for n in used), used
        result = events[-1]
        print("cost:", result.get("total_cost_usd"), "turns:", result.get("num_turns"), "usage:", result.get("usage"))
        assert result["type"] == "result" and result["subtype"] == "success"
    finally:
        w.close()


@pytest.mark.skipif(os.environ.get("HOMALAWN_TEST_REAL_CODEX") != "1" or not REAL_CODEX,
                    reason="the real Codex run is opt-in: HOMALAWN_TEST_REAL_CODEX=1 (and codex installed)")
def test_real_codex_answers_through_the_window(window_ready, tmp_path):
    """The installed, signed-in `codex` (its cheapest model, gpt-6-luna at low effort, as listed by
    `codex debug models`; -m is `codex exec --help`'s model flag) answers through Homa Lawn's tools
    only: the tool runs without asking although Codex never asks here (approval_policy="never"),
    because the chat marks Homa Lawn's tools approved."""
    bin_dir, stream, argv_log = tmp_path / "bin", SHOTS / "codex-real.jsonl", tmp_path / "codex-argv.json"
    real_wrapper(bin_dir, "codex", REAL_CODEX, stream, argv_log,
                 extra=("-m", "gpt-6-luna", "-c", 'model_reasoning_effort="low"'))
    w = ChatWindow(tmp_path, window_ready, bin_dir, default="codex")
    try:
        msgs = ask_dock(w)
        w.shot("agents", "chat-real-codex.png")
        print("\nreal Codex in the window:", json.dumps(msgs, indent=1))

        # the tool server Codex started saw only this test's config (paired with the AVD alone)
        argv = json.loads(argv_log.read_text())
        spec = next(a for a in argv if a.startswith("mcp_servers.homalawn="))
        assert f'HOMALAWN_CONFIG={json.dumps(os.environ["HOMALAWN_CONFIG"])}' in spec
        assert f"HOMALAWN_PHONE={json.dumps(window_ready)}" in spec

        events = [json.loads(line) for line in stream.read_text().splitlines() if line.strip()]
        items = [e["item"] for e in events if e.get("type") == "item.completed"]
        print("items:", [(i.get("type"), i.get("server"), i.get("tool"), i.get("status")) for i in items])
        calls = [i for i in items if i.get("type") == "mcp_tool_call"]
        assert calls and all(i["server"] == "homalawn" for i in calls), calls
        # approved without asking: under approval_policy="never" an unapproved call would fail
        assert all(i["status"] == "completed" and not i.get("error") for i in calls), calls
        assert not [i for i in items if i.get("type") in ("command_execution", "file_change", "web_search")], items
        done = events[-1]
        print("usage:", done.get("usage"))
        assert done["type"] == "turn.completed"
    finally:
        w.close()


@pytest.mark.skipif(os.environ.get("HOMALAWN_TEST_REAL_GEMINI") != "1" or not REAL_GEMINI or not REAL_HOMALAWN
                    or not (Path.home() / ".gemini" / GEMINI_AUTH[0]).exists(),
                    reason="the real Gemini run is opt-in: HOMALAWN_TEST_REAL_GEMINI=1 (Gemini installed, signed in)")
def test_real_gemini_answers_through_the_window(window_ready, tmp_path):
    """The installed, signed-in `gemini` (flash-lite, `gemini --help` -m; aliases in its
    docs/cli/cli-reference.md) answers through Homa Lawn's tools only. It reads Homa Lawn from its own
    settings, so it runs with GEMINI_CLI_HOME (its docs/reference/configuration.md) set to a
    temporary home: copies of the sign-in files and a settings.json with the same homalawn entry
    `homalawn connect` writes. A `homalawn` wrapper first on PATH records what the tool server saw."""
    real_home = Path.home() / ".gemini"
    home = tmp_path / "gemini-home"
    (home / ".gemini").mkdir(parents=True)
    for f in GEMINI_AUTH:
        if (real_home / f).exists():
            shutil.copy2(real_home / f, home / ".gemini" / f)
    security = json.loads((real_home / "settings.json").read_text()).get("security", {})
    (home / ".gemini" / "settings.json").write_text(json.dumps(
        {"security": security, "mcpServers": {"homalawn": {"command": "homalawn", "args": ["mcp"]}}}))

    bin_dir, stream, argv_log = tmp_path / "bin", SHOTS / "gemini-real.jsonl", tmp_path / "gemini-argv.json"
    real_wrapper(bin_dir, "gemini", REAL_GEMINI, stream, argv_log, after=0, extra=("-m", "flash-lite"))
    seen = tmp_path / "server-env.json"
    (bin_dir / "homalawn").write_text(f"""#!{sys.executable}
import json, os, sys
json.dump({{k: os.environ.get(k) for k in ("HOMALAWN_CONFIG", "HOMALAWN_PHONE", "HOMALAWN_SERIAL")}},
          open({str(seen)!r}, "w"))
os.execv({REAL_HOMALAWN!r}, [{REAL_HOMALAWN!r}, *sys.argv[1:]])
""")
    (bin_dir / "homalawn").chmod(0o755)
    w = ChatWindow(tmp_path, window_ready, bin_dir, default="gemini", extra_env={"GEMINI_CLI_HOME": str(home)})
    try:
        msgs = ask_dock(w)
        w.shot("agents", "chat-real-gemini.png")
        print("\nreal Gemini in the window:", json.dumps(msgs, indent=1))

        server = json.loads(seen.read_text())          # the tool server Gemini started
        print("tool server saw:", server)
        assert server["HOMALAWN_CONFIG"] == os.environ["HOMALAWN_CONFIG"] and server["HOMALAWN_PHONE"] == window_ready

        events = [json.loads(line) for line in stream.read_text().splitlines() if line.strip()]
        used = [e["tool_name"] for e in events if e.get("type") == "tool_use"]
        results = [e.get("status") for e in events if e.get("type") == "tool_result"]
        print("tools:", used, results)
        assert used and all(n.startswith("mcp_homalawn_") for n in used), used
        assert results and all(r == "success" for r in results), events
        done = events[-1]
        print("stats:", done.get("stats"))
        assert done["type"] == "result" and done["status"] == "success"
    finally:
        w.close()
