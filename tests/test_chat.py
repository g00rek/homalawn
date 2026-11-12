"""The Agents page's chat (homalawn.chat): each supported agent's command line, its stream read into
the conversation, and whole runs against fake agent programs on PATH that print each agent's
stream format. No real agent, account or phone is used."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from homalawn import chat

SID = "8f1c2d3e-4b5a-4c6d-8e7f-901234567890"

# Stream shapes: Claude Code 2.1.300 stream-json with partial messages (see ClaudeParser);
# a real run's lines are in fixtures/chat/claude-real.jsonl (the end-to-end run).
CLAUDE = [
    {"type": "system", "subtype": "init", "session_id": SID, "tools": ["mcp__homalawn__phone_layout"],
     "mcp_servers": [{"name": "homalawn", "status": "connected"}]},
    {"type": "stream_event", "session_id": SID, "parent_tool_use_id": None,
     "event": {"type": "message_start", "message": {"id": "m1", "role": "assistant", "content": []}}},
    {"type": "stream_event", "session_id": SID, "parent_tool_use_id": None,
     "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}},
    {"type": "stream_event", "session_id": SID, "parent_tool_use_id": None,
     "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Let me "}}},
    {"type": "stream_event", "session_id": SID, "parent_tool_use_id": None,
     "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "look."}}},
    {"type": "stream_event", "session_id": SID, "parent_tool_use_id": None,
     "event": {"type": "content_block_start", "index": 1,
               "content_block": {"type": "tool_use", "id": "t1", "name": "mcp__homalawn__phone_layout", "input": {}}}},
    {"type": "assistant", "session_id": SID, "parent_tool_use_id": None,
     "message": {"content": [{"type": "text", "text": "Let me look."},
                             {"type": "tool_use", "id": "t1", "name": "mcp__homalawn__phone_layout", "input": {}}]}},
    {"type": "user", "session_id": SID, "parent_tool_use_id": None,
     "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}]}},
    {"type": "stream_event", "session_id": SID, "parent_tool_use_id": None,
     "event": {"type": "message_start", "message": {"id": "m2", "role": "assistant", "content": []}}},
    {"type": "stream_event", "session_id": SID, "parent_tool_use_id": None,
     "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Your dock has Phone and Chrome."}}},
    {"type": "assistant", "session_id": SID, "parent_tool_use_id": None,
     "message": {"content": [{"type": "text", "text": "Your dock has Phone and Chrome."}]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "Your dock has Phone and Chrome.",
     "session_id": SID},
]

# codex exec --json (openai/codex sdk/typescript events.ts, items.ts)
THREAD = "0199a213-81c0-7800-8aa1-bbab2a035a53"
CODEX = [
    {"type": "thread.started", "thread_id": THREAD},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "secret thoughts"}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "Let me look."}},
    {"type": "item.started", "item": {"id": "item_2", "type": "mcp_tool_call", "server": "homalawn",
                                      "tool": "phone_place", "arguments": {}, "status": "in_progress"}},
    {"type": "item.completed", "item": {"id": "item_2", "type": "mcp_tool_call", "server": "homalawn",
                                        "tool": "phone_place", "arguments": {}, "status": "completed"}},
    {"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "Moved Chrome to the dock."}},
    {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5}},
]

# gemini --output-format stream-json (Gemini CLI 0.64.0 bundle, nonInteractiveCli)
GSID = "11111111-2222-4333-8444-555555555555"
GEMINI = [
    {"type": "init", "timestamp": "2026-10-15T10:00:00Z", "session_id": GSID, "model": "gemini-2.5-pro"},
    {"type": "message", "timestamp": "t", "role": "user", "content": "what is in the dock?"},
    {"type": "message", "timestamp": "t", "role": "assistant", "content": "Checking", "delta": True},
    {"type": "tool_use", "timestamp": "t", "tool_name": "mcp_homalawn_phone_layout", "tool_id": "c1", "parameters": {}},
    {"type": "tool_result", "timestamp": "t", "tool_id": "c1", "status": "success", "output": "..."},
    {"type": "message", "timestamp": "t", "role": "assistant", "content": "The dock has ", "delta": True},
    {"type": "message", "timestamp": "t", "role": "assistant", "content": "Phone.", "delta": True},
    {"type": "result", "timestamp": "t", "status": "success", "stats": {}},
]


def replay(parser, lines):
    c = chat.Chat(default=lambda: "claude", path=Path(os.environ["HOMALAWN_STATE_DIR"]) / "c.json",
                  installed=lambda exe: True)
    done = None
    for ev in lines:
        for a in parser.feed(ev):
            if a[0] == "done":
                done = a[1]
            elif a[0] == "fail":
                done = ("fail", a[1])
            else:
                c._apply(a)
    return c, done


# -- reading each agent's stream ----------------------------------------------------------------------
def test_claude_stream_becomes_bubbles_and_a_plain_step():
    c, done = replay(chat.ClaudeParser(), CLAUDE)
    assert c.session == SID and done == ""
    assert [(m["role"], m["text"]) for m in c.messages] == [
        ("agent", "Let me look."), ("step", "Read the home screen"), ("agent", "Your dock has Phone and Chrome.")]


def test_claude_without_partial_messages_uses_the_snapshots_and_a_failed_result_says_why():
    snaps = [e for e in CLAUDE if e["type"] != "stream_event"]
    c, done = replay(chat.ClaudeParser(), snaps)
    assert [m["role"] for m in c.messages] == ["agent", "step", "agent"]
    assert c.messages[-1]["text"] == "Your dock has Phone and Chrome."
    only_result = [{"type": "result", "subtype": "success", "is_error": False, "result": "Done."}]
    _, done = replay(chat.ClaudeParser(), only_result)
    assert done == "Done."                                   # nothing written before: the result is the answer
    _, done = replay(chat.ClaudeParser(), [{"type": "result", "subtype": "error_max_turns", "is_error": True}])
    assert done == ("fail", "it stopped (error_max_turns)")


def test_claude_subagent_messages_are_not_shown():
    inner = {**CLAUDE[3], "parent_tool_use_id": "t9"}
    c, _ = replay(chat.ClaudeParser(), [inner])
    assert c.messages == []


def test_codex_stream_keeps_messages_whole_and_hides_reasoning():
    c, done = replay(chat.CodexParser(), CODEX)
    assert c.session == THREAD and done == ""
    assert [(m["role"], m["text"]) for m in c.messages] == [
        ("agent", "Let me look."), ("step", "Placed an app"), ("agent", "Moved Chrome to the dock.")]
    _, done = replay(chat.CodexParser(), [{"type": "turn.failed", "error": {"message": "auth expired"}}])
    assert done == ("fail", "auth expired")


def test_gemini_stream_joins_deltas_and_shows_the_tool():
    c, done = replay(chat.GeminiParser(), GEMINI)
    assert c.session == GSID and done == ""
    assert [(m["role"], m["text"]) for m in c.messages] == [
        ("agent", "Checking"), ("step", "Read the home screen"), ("agent", "The dock has Phone.")]
    _, done = replay(chat.GeminiParser(), [{"type": "result", "status": "error"}])
    assert done[0] == "fail"


def test_tool_labels_are_plain_words():
    assert chat.tool_label("mcp__homalawn__screen_tap") == "Tapped"
    assert chat.tool_label("mcp_homalawn_phone_widget_add") == "Added a widget"
    assert chat.tool_label("homalawn__phone_undo") == "Undid the last change"
    assert chat.tool_label("mcp__homalawn__phone_new_thing") == "Phone new thing"
    assert chat.tool_label("Bash") == "Used a tool"


# -- command lines ------------------------------------------------------------------------------------
def test_claude_command_allows_only_homalawn_and_resumes(tmp_path):
    argv, stdin, chosen = chat.command("claude", "hi", None, "Pixel", tmp_path)
    assert argv[:2] == ["claude", "-p"] and stdin == b"hi" and chosen is None
    for flag, value in (("--output-format", "stream-json"), ("--permission-mode", "dontAsk"),
                        ("--tools", ""), ("--allowedTools", "mcp__homalawn__*"), ("--setting-sources", "")):
        assert argv[argv.index(flag) + 1] == value
    assert "--include-partial-messages" in argv and "--verbose" in argv and "--strict-mcp-config" in argv
    mcp = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["homalawn"]
    assert mcp["args"] == ["mcp"] and mcp["env"]["HOMALAWN_PHONE"] == "Pixel"
    assert mcp["env"]["HOMALAWN_STATE_DIR"] == os.environ["HOMALAWN_STATE_DIR"]   # the same phones as the service
    assert "--resume" not in argv
    argv, _, _ = chat.command("claude", "more", SID, "Pixel", tmp_path)
    assert argv[argv.index("--resume") + 1] == SID


def test_codex_command_turns_off_the_shell_and_other_servers(tmp_path):
    codex_home = Path(os.environ["CODEX_HOME"])
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        '[mcp_servers.homalawn]\ncommand = "homalawn"\nargs = ["mcp"]\n\n[mcp_servers.github]\ncommand = "gh"\n')
    argv, stdin, _ = chat.command("codex", "hi", None, "Pixel", tmp_path)
    assert argv[:3] == ["codex", "exec", "--json"] and argv[-1] == "-"
    assert stdin.decode().startswith(chat.PREAMBLE) and stdin.decode().endswith("hi")
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    off = {argv[i + 1] for i, a in enumerate(argv) if a == "--disable"}
    assert {"shell_tool", "unified_exec", "multi_agent", "apps", "plugins", "view_image", "image_generation",
            "goals", "hooks"} <= off
    configs = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert 'approval_policy="never"' in configs and "mcp_servers.github.enabled=false" in configs
    server = next(c for c in configs if c.startswith("mcp_servers.homalawn="))
    import tomllib
    table = tomllib.loads("x = " + server.split("=", 1)[1])["x"]
    assert table["args"] == ["mcp"] and table["env"]["HOMALAWN_PHONE"] == "Pixel"
    assert table["default_tools_approval_mode"] == "approve" and table["required"] is True
    argv, stdin, _ = chat.command("codex", "more", THREAD, "Pixel", tmp_path)
    assert argv[-3:] == ["resume", THREAD, "-"] and stdin == b"more"


def test_gemini_command_picks_its_session_and_writes_a_homalawn_only_policy(tmp_path):
    argv, stdin, chosen = chat.command("gemini", "hi", None, "Pixel", tmp_path)
    assert stdin is None and argv[argv.index("--session-id") + 1] == chosen
    assert argv[-1].startswith("--prompt=" + chat.PREAMBLE) and argv[-1].endswith("hi")
    assert argv[argv.index("--allowed-mcp-server-names") + 1] == "homalawn" and "--skip-trust" in argv
    import tomllib
    rules = tomllib.loads(Path(argv[argv.index("--policy") + 1]).read_text())["rule"]
    assert {"toolName": "*", "decision": "deny", "priority": 900} in rules
    assert {"mcpName": "homalawn", "toolName": "*", "decision": "allow", "priority": 999} in rules
    argv, _, chosen = chat.command("gemini", "more", GSID, "Pixel", tmp_path)
    assert argv[argv.index("--resume") + 1] == GSID and chosen is None and argv[-1] == "--prompt=more"


# -- whole runs against fake agents -------------------------------------------------------------------
def fake_agent(bin_dir: Path, exe: str, lines, wait: float = 0, code: int = 0, stderr: str = "") -> Path:
    """A program called `exe` that records its arguments and input, prints `lines` (one JSON
    object each), then waits `wait` seconds and exits with `code`."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = bin_dir / f"{exe}.calls"
    p = bin_dir / exe
    p.write_text(f"""#!{sys.executable}
import json, os, sys, time
data = sys.stdin.read() if not sys.stdin.isatty() else ""
with open({str(log)!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "stdin": data, "phone": os.environ.get("HOMALAWN_PHONE")}}) + "\\n")
for line in {[json.dumps(x) for x in lines]!r}:
    print(line, flush=True)
    time.sleep(0.01)
sys.stderr.write({stderr!r})
time.sleep({wait})
sys.exit({code})
""")
    p.chmod(0o755)
    return log


def calls(log: Path):
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    return d


def make_chat(tmp_path, agent="claude", **kw):
    events = []
    c = chat.Chat(default=lambda: agent, path=tmp_path / "chat.json", installed=lambda exe: True,
                  has_entry=lambda a: True, **kw)
    c.on_change = lambda: events.append(c.snapshot())
    return c, events


async def finished(c, timeout=10):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while c.running:
        assert loop.time() < end, "the run did not finish"
        await asyncio.sleep(0.02)


@pytest.mark.parametrize("agent,lines,reply", [
    ("claude", CLAUDE, "Your dock has Phone and Chrome."),
    ("codex", CODEX, "Moved Chrome to the dock."),
    ("gemini", GEMINI, "The dock has Phone."),
])
def test_a_message_streams_the_answer_and_the_next_one_continues(tmp_path, bin_dir, agent, lines, reply):
    log = fake_agent(bin_dir, agent, lines)
    c, events = make_chat(tmp_path, agent)

    async def go():
        snap = await c.send("what is in the dock?", "ab12", name="Pixel")
        assert snap["running"] and snap["messages"][-1] == {**snap["messages"][-1], "role": "user",
                                                           "text": "what is in the dock?"}
        await finished(c)
        assert c.messages[-1]["role"] == "agent" and c.messages[-1]["text"] == reply
        assert any(m["role"] == "step" for m in c.messages)
        assert len(events) >= 2 and events[-1]["running"] is False
        first = calls(log)[0]
        assert first["phone"] == "ab12"                      # the tool server gets the phone's fingerprint
        assert snap["phone"] == "Pixel" and c.snapshot()["phone"] == "Pixel"   # people are shown its name
        sent = first["stdin"] or first["argv"][-1]
        assert "[Selected phone: Pixel]" in sent and "ab12" not in sent and "what is in the dock?" in sent
        await c.send("and now?", "ab12", name="Pixel")
        await finished(c)
        second = calls(log)[1]["argv"]
        assert {"claude": SID, "codex": THREAD, "gemini": c.session}[agent] in second
        assert ("resume" in second) or ("--resume" in second)
        if agent == "gemini":          # we choose its id on the first run; the id it reports is the one kept
            assert "--session-id" in calls(log)[0]["argv"] and c.session == GSID
    asyncio.run(asyncio.wait_for(go(), 30))
    saved = json.loads((tmp_path / "chat.json").read_text())   # kept across service restarts
    assert saved["session_agent"] == agent and saved["messages"][0]["text"] == "what is in the dock?"
    again, _ = make_chat(tmp_path, agent)
    assert again.messages == saved["messages"] and again.session == saved["session"]


def test_stop_ends_the_agent_and_says_stopped(tmp_path, bin_dir):
    log = fake_agent(bin_dir, "claude", CLAUDE[:5], wait=30)
    c, _ = make_chat(tmp_path)

    async def go():
        await c.send("hi", "Pixel")
        loop = asyncio.get_running_loop()
        end = loop.time() + 5
        while not any(m["role"] == "agent" for m in c.messages):
            assert loop.time() < end
            await asyncio.sleep(0.02)
        t0 = loop.time()
        assert await c.cancel() is True
        assert loop.time() - t0 < 5 and c.running is False
        assert c.messages[-1]["text"] == "Stopped." and c.messages[-2]["text"] == "Let me look."
        assert c.session == SID                                  # the conversation can go on
        assert await c.cancel() is False
    asyncio.run(asyncio.wait_for(go(), 20))
    assert len(calls(log)) == 1


# What a real Gemini CLI 0.64.0 printed when its account could no longer use it (paths shortened).
GEMINI_SIGN_IN_FAILED = """Error authenticating: IneligibleTierError: This client is no longer supported for Gemini Code Assist for individuals.
    at throwIneligibleOrProjectIdError (file:///x/bundle/chunk-NR7L3FXV.js:311997:11)
    at process.processTicksAndRejections (node:internal/process/task_queues:104:5) {
  ineligibleTiers: [
    {
      reasonCode: 'UNSUPPORTED_CLIENT',
      reasonMessage: 'This client is no longer supported for Gemini Code Assist for individuals.',
      tierId: 'free-tier'
    }
  ]
}
An unexpected critical error occurred:IneligibleTierError: This client is no longer supported for Gemini Code Assist for individuals.
    at throwIneligibleOrProjectIdError (file:///x/bundle/chunk-NR7L3FXV.js:311997:11)
    at process.processTicksAndRejections (node:internal/process/task_queues:104:5)
"""


def test_the_reason_shown_is_the_error_line_not_a_stack_frame():
    assert chat._reason(GEMINI_SIGN_IN_FAILED) == (
        "An unexpected critical error occurred:IneligibleTierError: This client is no longer supported "
        "for Gemini Code Assist for individuals.")
    assert chat._reason("Error: not logged in. Run claude /login\n") == "Error: not logged in. Run claude /login"
    assert chat._reason("starting\nquota exhausted\n") == "quota exhausted"
    assert chat._reason("") == ""


def test_a_failing_agent_says_why_in_one_line(tmp_path, bin_dir):
    fake_agent(bin_dir, "claude", [], code=1, stderr="Error: not logged in. Run claude /login\n")
    c, _ = make_chat(tmp_path)

    async def go():
        await c.send("hi", None)
        await finished(c)
    asyncio.run(go())
    last = c.messages[-1]
    assert last["role"] == "note" and last["error"] is True
    assert last["text"] == "Claude Code could not answer: Error: not logged in. Run claude /login"


def test_unsupported_missing_and_busy_are_refused(tmp_path, bin_dir):
    c, _ = make_chat(tmp_path, agent="opencode")
    assert c.agent()["supported"] is False and "terminal" in c.agent()["why"]
    with pytest.raises(chat.ChatError) as e:
        asyncio.run(c.send("hi", None))
    assert e.value.code == "unsupported" and c.messages == []

    none, _ = make_chat(tmp_path, agent=None)
    assert none.agent()["why"] == "No default coding agent is chosen in Omarchy yet."

    missing = chat.Chat(default=lambda: "codex", path=tmp_path / "m.json", installed=lambda exe: False)
    assert missing.agent()["installed"] is False
    with pytest.raises(chat.ChatError):
        asyncio.run(missing.send("hi", None))

    # Gemini cannot be handed the tool server: without Homa Lawn in its settings it is refused
    plain = chat.Chat(default=lambda: "gemini", path=tmp_path / "g.json", installed=lambda exe: True)
    assert plain.agent()["installed"] is False and "Connect it in Settings" in plain.agent()["why"]
    gemini_home = Path(os.environ["GEMINI_CLI_HOME"]) / ".gemini"
    gemini_home.mkdir(parents=True)
    (gemini_home / "settings.json").write_text('{"mcpServers": {"homalawn": {"command": "homalawn", "args": ["mcp"]}}}')
    assert plain.agent()["installed"] is True and plain.agent()["why"] is None

    fake_agent(bin_dir, "claude", CLAUDE[:2], wait=30)
    busy, _ = make_chat(tmp_path)

    async def go():
        await busy.send("hi", None)
        with pytest.raises(chat.ChatError) as e:
            await busy.send("again", None)
        assert e.value.code == "busy"
        with pytest.raises(chat.ChatError):
            await busy.send("   ", None)
        await busy.cancel()
    asyncio.run(asyncio.wait_for(go(), 20))


def test_a_new_default_agent_starts_a_new_conversation(tmp_path, bin_dir):
    fake_agent(bin_dir, "claude", CLAUDE)
    log = fake_agent(bin_dir, "codex", CODEX)
    who = {"id": "claude"}
    c = chat.Chat(default=lambda: who["id"], path=tmp_path / "chat.json", installed=lambda exe: True)

    async def go():
        await c.send("hi", None)
        await finished(c)
        who["id"] = "codex"
        await c.send("hi again", None)
        await finished(c)
    asyncio.run(asyncio.wait_for(go(), 20))
    assert "resume" not in calls(log)[0]["argv"]
    assert any(m["role"] == "note" and "Codex" in m["text"] for m in c.messages)
    assert c.session == THREAD and c.session_agent == "codex"


def test_a_real_claude_code_stream_reads_as_one_step_and_one_answer():
    """fixtures/chat/claude-real.jsonl: a real run through the window (see fixtures/README.md)."""
    lines = [json.loads(l) for l in (Path(__file__).parent / "fixtures" / "chat" / "claude-real.jsonl")
             .read_text().splitlines()]
    c, done = replay(chat.ClaudeParser(), lines)
    assert done == "" and c.session == lines[0]["session_id"]
    assert [m["role"] for m in c.messages] == ["step", "agent"]
    assert c.messages[0]["text"] == "Read the home screen"
    assert c.messages[1]["text"].startswith("The dock") and "Chrome" in c.messages[1]["text"]
    assert c.messages[1]["text"] == lines[-1]["result"]       # what streamed is the whole answer
