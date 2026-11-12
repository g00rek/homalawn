import types
import pytest
from typer.testing import CliRunner
from homalawn import agents, cli, desktop


class Run:
    def __init__(self, enabled=False, service=False, fail=()):
        self.calls, self.enabled, self.service, self.fail = [], enabled, service, fail
        self.restarts = 0

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = ""
        if cmd[:3] == ["systemctl", "--user", "is-enabled"] or cmd[:3] == ["systemctl", "--user", "is-active"]:
            return types.SimpleNamespace(returncode=0 if self.service else 3, stdout="", stderr="")
        if cmd[:3] == ["systemctl", "--user", "enable"]:
            if "enable" in self.fail:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="boom unit")
            self.service = True
        if cmd[:3] == ["systemctl", "--user", "try-restart"]:
            if "restart" in self.fail:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="boom restart")
            self.restarts += 1
        if cmd[:3] == ["omarchy", "restart", "shell"] and "shell" in self.fail:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="boom shell")
        if cmd[:3] == ["omarchy", "plugin", "enable"]:
            if "enable-silent" in self.fail:      # timed out, but shell.json was written
                self.enabled = True
                return types.SimpleNamespace(returncode=1, stdout="omarchy-shell is not responding\n", stderr="")
            if "enable-dead" in self.fail:        # timed out and nothing changed
                return types.SimpleNamespace(returncode=1, stdout="omarchy-shell is not responding\n", stderr="")
            self.enabled = True
        if cmd[:3] == ["omarchy", "plugin", "list"]:
            out = f"ID STATE SOURCE KINDS NAME\nhomalawn {'enabled' if self.enabled else 'disabled'} third-party x Homa Lawn\n"
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "shell"
    (src / "sub").mkdir(parents=True)
    (src / "manifest.json").write_text("{}")
    (src / "Service.qml").write_text("v1")
    (src / "sub" / "A.qml").write_text("a")
    unit = tmp_path / "homalawnd.service"
    unit.write_text("[Service]\nExecStart=%h/.local/bin/homalawn serve\n")
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "homalawn").write_text("#!/bin/sh\n")   # the program the unit runs
    return types.SimpleNamespace(src=src, unit=unit, home=home,
                                 pdir=home / ".config/omarchy/plugins/homalawn",
                                 upath=home / ".config/systemd/user/homalawnd.service")


def have(cmd):
    return f"/usr/bin/{cmd}"


def inst(e, run, **kw):
    kw.setdefault("which", have)
    kw.setdefault("sleep", lambda s: None)
    return desktop.install(e.home, run, plugin_src=e.src, unit_src=e.unit, **kw)


def test_first_install_writes_all_and_runs_in_order(env):
    run = Run()
    inst(env, run)
    assert env.upath.read_text().count("ExecStart=%h/.local/bin/homalawn serve") == 1
    assert (env.pdir / "sub" / "A.qml").read_text() == "a"
    assert (env.pdir / ".homalawn-files").read_text().split() == ["Service.qml", "manifest.json", "sub/A.qml"]
    assert run.calls == [["systemctl", "--user", "daemon-reload"],
                         ["systemctl", "--user", "is-enabled", "homalawnd"],
                         ["systemctl", "--user", "enable", "--now", "homalawnd"],
                         ["systemctl", "--user", "is-active", "homalawnd"],     # still running a moment later
                         ["omarchy-shell", "shell", "rescanPlugins"],
                         ["omarchy", "plugin", "list"],
                         ["omarchy", "plugin", "enable", "homalawn", "--section", "right"]]


def test_exe_overrides_exec_start(env):
    inst(env, Run(), exe="/h/.local/bin/homalawn")
    assert "ExecStart=/h/.local/bin/homalawn serve" in env.upath.read_text()


def test_second_install_changes_nothing(env):
    inst(env, Run())
    mt = (env.pdir / "Service.qml").stat().st_mtime_ns
    run = Run(enabled=True, service=True)
    inst(env, run)
    assert run.calls == [["systemctl", "--user", "is-enabled", "homalawnd"],
                         ["systemctl", "--user", "is-active", "homalawnd"],
                         ["omarchy", "plugin", "list"]]
    assert (env.pdir / "Service.qml").stat().st_mtime_ns == mt


def test_changed_file_rewritten_removed_deleted_user_kept(env):
    inst(env, Run())
    (env.pdir / "mine.txt").write_text("keep")
    (env.src / "Service.qml").write_text("v2")
    (env.src / "sub" / "A.qml").unlink()
    (env.src / "sub").rmdir()
    run = Run(enabled=True)
    inst(env, run)
    assert (env.pdir / "Service.qml").read_text() == "v2"
    assert not (env.pdir / "sub").exists()
    assert (env.pdir / "mine.txt").read_text() == "keep"
    assert ["omarchy", "restart", "shell"] in run.calls      # an upgrade: the shell reloads the QML
    assert ["omarchy-shell", "shell", "rescanPlugins"] not in run.calls


def test_symlinked_plugin_dir_refused(env, tmp_path):
    env.pdir.parent.mkdir(parents=True)
    co = tmp_path / "checkout"
    co.mkdir()
    env.pdir.symlink_to(co)
    run = Run()
    with pytest.raises(desktop.DesktopError, match="link"):
        inst(env, run)
    assert run.calls == [] and not env.upath.exists()


def test_symlink_inside_plugin_dir_refused(env, tmp_path):
    env.pdir.mkdir(parents=True)
    (env.pdir / "HomaLawn").symlink_to(tmp_path)
    with pytest.raises(desktop.DesktopError):
        inst(env, Run())


def test_uninstall(env):
    inst(env, Run())
    (env.pdir / "mine.txt").write_text("keep")
    run = Run()
    desktop.uninstall(env.home, run)
    assert not env.upath.exists()
    assert sorted(p.name for p in env.pdir.iterdir()) == ["mine.txt"]
    assert ["systemctl", "--user", "disable", "--now", "homalawnd"] in run.calls


def test_uninstall_leaves_no_dir_when_nothing_user_added(env):
    inst(env, Run())
    desktop.uninstall(env.home, Run())
    assert not env.pdir.exists()


def test_shipped_sources_exist():
    plugin, unit = desktop.default_sources()
    assert (plugin / "manifest.json").is_file()
    assert "ExecStart=%h/.local/bin/homalawn serve" in unit.read_text()


def _says(*lines):
    def fake(home, say=None, **kw):
        for line in lines:
            say(line)
        return list(lines)
    return fake


@pytest.fixture
def agent_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_STATE_DIR", str(tmp_path / "state"))


def test_cli_says_each_step_names_the_agents_and_what_next(monkeypatch, agent_state):
    monkeypatch.setattr(desktop, "install", _says("one", "two"))
    monkeypatch.setattr(agents, "installed_names", lambda: ["Claude Code", "Codex"])
    monkeypatch.setattr(agents, "sync", lambda: ["agent-added"])
    r = CliRunner().invoke(cli.app, ["desktop", "install"])
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert lines[:2] == ["one", "two"]
    assert lines[2] == ("Adding Homa Lawn's tool server to the coding agents found here (Claude Code, Codex), so they "
                        "can work with your phone. Skip this with --no-agents; `homalawn desktop uninstall` takes it "
                        "out again.")
    assert lines[3:] == ["agent-added", "Next: click the phone icon in the bar to open Homa Lawn, then Add phone."]
    assert agents.auto_add() is True


def test_cli_no_agents_touches_no_agent_and_keeps_the_service_from_it(monkeypatch, agent_state):
    monkeypatch.setattr(desktop, "install", _says("one"))
    monkeypatch.setattr(agents, "_register", lambda a, run: pytest.fail("an agent was registered"))
    r = CliRunner().invoke(cli.app, ["desktop", "install", "--no-agents"])
    assert r.exit_code == 0, r.output
    assert "Skipped the coding agents (--no-agents)" in r.output
    assert agents.auto_add() is False and agents.sync() == []      # the service's hourly run adds nothing
    monkeypatch.setattr(agents, "sync", lambda: [])
    monkeypatch.setattr(agents, "installed_names", lambda: [])
    assert CliRunner().invoke(cli.app, ["desktop", "install"]).exit_code == 0
    assert agents.auto_add() is True                               # a plain install switches it back on


def test_cli_no_agents_is_recorded_before_the_service_starts(monkeypatch, agent_state):
    """The service starts inside desktop.install and its agent loop runs at once: with --no-agents
    it must already find adding switched off, so no agent file is touched at any point."""
    monkeypatch.setattr(agents, "_register", lambda a, run: pytest.fail("an agent was registered"))
    monkeypatch.setattr(agents, "installed_names", lambda *a, **k: pytest.fail("agents looked up"))

    def install_and_start_the_service(home, say=None, **kw):
        assert agents.auto_add() is False        # the choice is on disk before the service runs
        assert agents.sync() == []               # what the service's agent loop does on start
        return []
    monkeypatch.setattr(desktop, "install", install_and_start_the_service)
    r = CliRunner().invoke(cli.app, ["desktop", "install", "--no-agents"])
    assert r.exit_code == 0, r.output


def test_cli_uninstall_says_what_stays_with_the_real_folders(monkeypatch, agent_state, tmp_path):
    monkeypatch.setattr(desktop, "uninstall", _says("gone"))
    monkeypatch.setattr(agents, "unregister_all", lambda: ["agent-removed"])
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    r = CliRunner().invoke(cli.app, ["desktop", "uninstall"])
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert lines[:2] == ["gone", "agent-removed"]
    assert lines[2].startswith(f"Kept: the pairings and this computer's key in {tmp_path / 'cfg'}, ")
    assert str(tmp_path / "state") in lines[2]
    assert "still trusts this computer" in lines[3] and "homalawn unpair" in lines[3]
    assert lines[4].startswith("To remove everything: rm -r ") and "uv tool uninstall homalawn" in lines[4]


def test_kept_lines_name_the_default_folders(tmp_path):
    lines = desktop.kept_lines(tmp_path)
    assert lines[0] == ("Kept: the pairings and this computer's key in ~/.config/homalawn, the History (snapshots) in "
                        "~/.local/share/homalawn, and small working files in ~/.local/state/homalawn and "
                        "~/.cache/homalawn.")


@pytest.mark.parametrize("gone,word", [("systemctl", "systemctl"), ("omarchy", "`omarchy`"),
                                       ("omarchy-shell", "omarchy-shell")])
def test_a_missing_command_stops_the_install_before_anything_is_written(env, gone, word):
    run = Run()
    with pytest.raises(desktop.DesktopError) as e:
        inst(env, run, which=lambda c: None if c == gone else have(c))
    msg = str(e.value)
    assert word in msg and "Nothing was installed" in msg and msg.count(". ") <= 1
    assert run.calls == [] and not env.upath.exists() and not env.pdir.exists()


def test_cli_missing_omarchy_is_one_sentence_and_no_traceback(monkeypatch, agent_state):
    monkeypatch.setattr(desktop.shutil, "which", lambda c: None if c == "omarchy" else have(c))
    monkeypatch.setattr(agents, "sync", lambda: pytest.fail("agents must not be touched"))
    r = CliRunner().invoke(cli.app, ["desktop", "install"])
    assert r.exit_code == 1 and isinstance(r.exception, SystemExit)     # a clean exit, not a traceback
    assert r.output.strip().splitlines() == [desktop.NEEDS[1][1]]


def test_a_missing_service_program_stops_the_install(env):
    (env.home / ".local" / "bin" / "homalawn").unlink()
    run = Run()
    with pytest.raises(desktop.DesktopError, match="~/.local/bin/homalawn, which is not there"):
        inst(env, run)
    assert run.calls == [] and not env.upath.exists()


def test_a_service_that_does_not_stay_running_is_not_reported_started(env):
    class Dies(Run):
        def __call__(self, cmd, **kw):
            if cmd[:3] == ["systemctl", "--user", "is-active"] and self.service:
                self.calls.append(cmd)
                return types.SimpleNamespace(returncode=3, stdout="failed\n", stderr="")
            return super().__call__(cmd, **kw)
    slept = []
    out = inst(env, Dies(), sleep=slept.append)
    assert "Started the Homa Lawn service; it starts with the desktop session from now on." not in out
    assert desktop.NOT_RUNNING in out and slept == [desktop.SETTLE_S]
    assert not (env.home / MARKER).exists()          # the next install tries again


def test_each_line_is_said_as_it_happens(env):
    said = []
    out = inst(env, Run(), say=said.append)
    assert said == list(out) and said[-1] == "Added the Homa Lawn icon to the right side of the bar."


def test_uninstall_shows_no_raw_command_output_and_survives_missing_commands(env):
    inst(env, Run())
    calls = []

    def run(cmd, **kw):
        calls.append(kw)
        if cmd[0] == "omarchy":
            raise FileNotFoundError(cmd[0])
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    out = desktop.uninstall(env.home, run)
    assert all(kw.get("capture_output") for kw in calls)
    assert out[0] == "Stopped and removed the Homa Lawn service."
    assert out[1].startswith("Removed the Homa Lawn plugin from Omarchy's bar.") and "omarchy is not installed" in out[1]


def test_cli_refusal_exits_1(monkeypatch):
    monkeypatch.setattr(agents, "sync", lambda: pytest.fail("agents must not be touched after a refusal"))
    def boom(home, **kw):
        raise desktop.DesktopError("nope")
    monkeypatch.setattr(desktop, "install", boom)
    r = CliRunner().invoke(cli.app, ["desktop", "install"])
    assert r.exit_code == 1


def test_tracking_entries_outside_plugin_dir_never_deleted(env, tmp_path):
    inst(env, Run())
    victim = tmp_path / "home" / ".config" / "omarchy" / "plugins" / "x"
    victim.write_text("precious")
    outside = tmp_path / "outside.txt"
    outside.write_text("precious")
    esc = tmp_path / "escape"
    esc.mkdir()
    (esc / "f").write_text("precious")
    track = env.pdir / ".homalawn-files"
    track.write_text(f"../x\n{outside}\n")
    (env.src / "Service.qml").write_text("v2")
    out = inst(env, Run(enabled=True, service=True))
    assert victim.read_text() == outside.read_text() == (esc / "f").read_text() == "precious"
    assert any("outside" in l for l in out)
    track.write_text(f"../x\n{outside}\n")
    desktop.uninstall(env.home, Run())
    assert victim.read_text() == outside.read_text() == (esc / "f").read_text() == "precious"


def test_enable_failure_reported_and_retried(env):
    run = Run(fail=("enable",))
    out = inst(env, run)
    assert not any(l.startswith("Started the Homa Lawn service") for l in out)
    assert any("boom unit" in l for l in out)
    run2 = Run(enabled=True)  # unit unchanged now, service still not enabled -> retry
    out2 = inst(env, run2)
    assert ["systemctl", "--user", "enable", "--now", "homalawnd"] in run2.calls
    assert "Started the Homa Lawn service; it starts with the desktop session from now on." in out2


def test_safe_rejects_symlink_escape(tmp_path):
    pdir = tmp_path / "p"
    pdir.mkdir()
    esc = tmp_path / "esc"
    esc.mkdir()
    (pdir / "lnk").symlink_to(esc)
    assert not desktop._safe(pdir, "lnk/f")
    assert not desktop._safe(pdir, "../x") and not desktop._safe(pdir, "/etc/passwd")
    assert desktop._safe(pdir, "sub/A.qml")


def test_test_tools_and_readme_stay_out_of_the_install(env):
    (env.src / "tools").mkdir()
    (env.src / "tools" / "TestPanel.qml").write_text("t")
    (env.src / "tools" / "test-offscreen.sh").write_text("#!/bin/sh")
    (env.src / "README.md").write_text("dev notes")
    (env.src / "sub" / "README.md").write_text("nested readme ships")
    inst(env, Run())
    assert not (env.pdir / "tools").exists()
    assert not (env.pdir / "README.md").exists()
    assert (env.pdir / "sub" / "README.md").is_file()
    assert "tools/TestPanel.qml" not in (env.pdir / ".homalawn-files").read_text()


def test_shipped_manifest_is_one_omarchy_loads():
    import json
    plugin, _ = desktop.default_sources()
    m = json.loads((plugin / "manifest.json").read_text())
    assert m["schemaVersion"] == 1 and m["id"] == "homalawn" and m["keepLoaded"] is True
    assert m["kinds"] == ["service", "bar-widget", "panel"]
    for entry in m["entryPoints"].values():
        assert (plugin / entry).is_file(), entry
    assert (plugin / "HomaLawn" / "pages" / "LookPage.qml").is_file()


RESTART = ["systemctl", "--user", "try-restart", "homalawnd"]
MARKER = ".config/systemd/user/.homalawnd.version"


def test_start_records_the_version_the_service_runs(env):
    inst(env, Run(), version="1.0")
    assert (env.home / MARKER).read_text().strip() == "1.0"


def test_upgrade_restarts_the_running_service(env):
    inst(env, Run(), version="1.0")
    run = Run(enabled=True, service=True)
    out = inst(env, run, version="1.1")
    assert run.calls.count(RESTART) == 1 and run.restarts == 1
    assert any("Restarted the Homa Lawn service" in l for l in out)
    assert (env.home / MARKER).read_text().strip() == "1.1"
    run = Run(enabled=True, service=True)
    inst(env, run, version="1.1")                     # nothing new: no restart
    assert RESTART not in run.calls


def test_changed_unit_restarts_the_running_service(env):
    inst(env, Run(), version="1.0")
    env.unit.write_text("[Service]\nExecStart=%h/.local/bin/homalawn serve\nRestart=always\n")
    run = Run(enabled=True, service=True)
    inst(env, run, version="1.0")
    assert run.calls.index(["systemctl", "--user", "daemon-reload"]) < run.calls.index(RESTART)


def test_running_service_without_a_record_is_restarted(env):
    inst(env, Run(), version="1.0")
    (env.home / MARKER).unlink()                      # installed by a homalawn that kept no record
    run = Run(enabled=True, service=True)
    inst(env, run, version="1.0")
    assert run.restarts == 1 and (env.home / MARKER).read_text().strip() == "1.0"


def test_restart_failure_reported_and_retried(env):
    inst(env, Run(), version="1.0")
    run = Run(enabled=True, service=True, fail=("restart",))
    out = inst(env, run, version="1.1")
    assert any("Could not restart the Homa Lawn service: boom restart" in l for l in out)
    assert (env.home / MARKER).read_text().strip() == "1.0"   # the next install tries again
    run = Run(enabled=True, service=True)
    inst(env, run, version="1.1")
    assert run.restarts == 1


def test_uninstall_removes_the_version_record(env):
    inst(env, Run(), version="1.0")
    desktop.uninstall(env.home, Run())
    assert not (env.home / MARKER).exists()


def test_installed_version_names_the_package_version_and_its_code():
    # an editable install keeps one version number across code changes; the code digest tells them apart
    from importlib.metadata import version
    v = desktop.installed_version()
    assert v.startswith(version("homalawn") + "+") and len(v.split("+")[1]) == 12
    assert desktop.installed_version() == v


def test_upgrade_restarts_the_shell_so_it_loads_the_new_plugin(env):
    inst(env, Run())
    (env.src / "Service.qml").write_text("v2")
    run = Run(enabled=True, service=True)
    out = inst(env, run)
    assert ["omarchy", "restart", "shell"] in run.calls
    assert "Restarted the Omarchy shell to load the new Homa Lawn plugin." in out
    run2 = Run(enabled=True, service=True)        # nothing changed: the shell is left alone
    inst(env, run2)
    assert ["omarchy", "restart", "shell"] not in run2.calls


def test_shell_restart_failure_is_reported(env):
    inst(env, Run())
    (env.src / "Service.qml").write_text("v2")
    out = inst(env, Run(enabled=True, service=True, fail=("shell",)))
    assert any("Could not restart the Omarchy shell: boom shell" in l for l in out)
    assert "Restarted the Omarchy shell to load the new Homa Lawn plugin." not in out


def test_first_install_does_not_restart_the_shell(env):
    run = Run()
    inst(env, run)
    assert ["omarchy", "restart", "shell"] not in run.calls


def test_enable_that_timed_out_but_was_written_counts_as_done(env):
    run = Run(fail=("enable-silent",))
    out = inst(env, run)
    assert run.calls[-1] == ["omarchy", "plugin", "list"]       # checked after the timeout
    assert "Added the Homa Lawn icon to the right side of the bar." in out


def test_enable_that_timed_out_and_did_nothing_is_reported(env):
    out = inst(env, Run(fail=("enable-dead",)))
    assert any(l.startswith("Could not add the Homa Lawn icon to the bar: omarchy-shell is not responding")
               for l in out)


class SlowShell(Run):
    """A shell that, after `omarchy restart shell`, lists no plugins for `quiet` list calls."""

    def __init__(self, quiet, **kw):
        super().__init__(**kw)
        self.quiet, self.down = quiet, 0

    def __call__(self, cmd, **kw):
        if cmd[:3] == ["omarchy", "restart", "shell"]:
            self.down = self.quiet
        if cmd[:3] == ["omarchy", "plugin", "list"] and self.down:
            self.down -= 1
            self.calls.append(cmd)
            return types.SimpleNamespace(returncode=1, stdout="", stderr="omarchy-shell is not responding")
        if cmd[:3] == ["omarchy", "plugin", "enable"] and self.down:
            self.calls.append(cmd)
            return types.SimpleNamespace(returncode=1, stdout="omarchy-shell is not responding\n", stderr="")
        return super().__call__(cmd, **kw)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_after_a_shell_restart_the_install_waits_for_the_shell_before_enabling(env):
    inst(env, Run())
    (env.src / "Service.qml").write_text("v2")
    run, clock = SlowShell(quiet=6, enabled=True, service=True), Clock()
    out = inst(env, run, sleep=clock.sleep, clock=clock)
    assert ["omarchy", "plugin", "enable", "homalawn", "--section", "right"] not in run.calls
    assert not any(l.startswith("Could not add the Homa Lawn icon") for l in out)
    assert 0 < clock.t <= desktop.RESTART_WAIT_S


def test_after_a_shell_restart_a_disabled_plugin_is_enabled_after_the_wait(env):
    inst(env, Run())
    (env.src / "Service.qml").write_text("v2")
    run, clock = SlowShell(quiet=0, enabled=False, service=True), Clock()
    out = inst(env, run, sleep=clock.sleep, clock=clock)
    assert clock.t >= desktop.RESTART_WAIT_S                    # it waited before giving up on the list
    assert ["omarchy", "plugin", "enable", "homalawn", "--section", "right"] in run.calls
    assert "Added the Homa Lawn icon to the right side of the bar." in out


def _homar_era(env, user_file=False):
    """What `homar desktop install` (2026-10-13 to 2026-10-15) left on a desktop."""
    old_unit = env.home / ".config/systemd/user/homard.service"
    old_unit.parent.mkdir(parents=True, exist_ok=True)
    old_unit.write_text("[Service]\nExecStart=%h/.local/bin/homar serve\n")
    (old_unit.parent / ".homard.version").write_text("0.1.0+abc\n")
    old = env.home / ".config/omarchy/plugins/homar"
    (old / "Homar" / "pages").mkdir(parents=True)
    for rel in ("manifest.json", "Panel.qml", "Homar/HomarView.qml", "Homar/pages/LookPage.qml"):
        (old / rel).write_text("old")
    (old / ".homar-files").write_text("Homar/HomarView.qml\nHomar/pages/LookPage.qml\nPanel.qml\nmanifest.json\n")
    if user_file:
        (old / "notes.txt").write_text("mine")
    return old_unit, old


def test_install_over_homar_removes_the_old_service_and_plugin_and_restarts_the_shell_once(env):
    old_unit, old = _homar_era(env)
    run, clock = Run(service=False), Clock()
    out = inst(env, run, sleep=clock.sleep, clock=clock)
    assert not old_unit.exists() and not (old_unit.parent / ".homard.version").exists()
    assert not old.exists()
    assert env.upath.is_file() and (env.pdir / ".homalawn-files").is_file()
    c = run.calls
    assert c.index(["systemctl", "--user", "disable", "--now", "homard"]) \
        < c.index(["systemctl", "--user", "enable", "--now", "homalawnd"])
    assert c.index(["omarchy", "plugin", "disable", "homar"]) < c.index(["omarchy", "restart", "shell"])
    assert c.count(["omarchy", "restart", "shell"]) == 1
    assert ["omarchy-shell", "shell", "rescanPlugins"] not in c
    assert c[-1] == ["omarchy", "plugin", "enable", "homalawn", "--section", "right"]
    assert "Stopped and removed the old Homar service (homard); Homar is now Homa Lawn." in out
    assert "Removed the old Homar plugin." in out
    assert "Restarted the Omarchy shell to load the new Homa Lawn plugin." in out
    assert "Added the Homa Lawn icon to the right side of the bar." in out


def test_install_over_homar_keeps_files_the_user_added_to_the_old_plugin(env):
    _, old = _homar_era(env, user_file=True)
    out = inst(env, Run())
    assert sorted(p.name for p in old.iterdir()) == ["notes.txt"]
    assert f"Removed the old Homar plugin (files you added were kept in {old})." in out


def test_second_install_after_the_move_leaves_the_shell_alone(env):
    _homar_era(env)
    inst(env, Run())
    run = Run(enabled=True, service=True)
    inst(env, run)
    assert run.calls == [["systemctl", "--user", "is-enabled", "homalawnd"],
                         ["systemctl", "--user", "is-active", "homalawnd"],
                         ["omarchy", "plugin", "list"]]


def test_a_homar_folder_no_install_made_is_left_alone(env):
    old = env.home / ".config/omarchy/plugins/homar"
    old.mkdir(parents=True)
    (old / "manifest.json").write_text("mine")
    run = Run()
    inst(env, run)
    assert (old / "manifest.json").read_text() == "mine"
    assert ["omarchy", "plugin", "disable", "homar"] not in run.calls
    assert ["omarchy", "restart", "shell"] not in run.calls


def test_install_over_homar_waits_only_until_the_restarted_shell_lists_homa_lawn(env):
    _homar_era(env)
    run, clock = SlowShell(quiet=4), Clock()
    out = inst(env, run, sleep=clock.sleep, clock=clock)
    assert 0 < clock.t < desktop.RESTART_WAIT_S               # listed (disabled) after 4 tries: no full wait
    assert run.calls[-1] == ["omarchy", "plugin", "enable", "homalawn", "--section", "right"]
    assert "Added the Homa Lawn icon to the right side of the bar." in out


REENABLE = ["systemctl", "--user", "reenable", "homalawnd"]


def test_shipped_unit_runs_inside_the_desktop_session():
    # Omarchy's environment (OMARCHY_PATH) reaches systemd only when the session comes up; started
    # from default.target the service found no Omarchy themes
    _, unit = desktop.default_sources()
    lines = unit.read_text().splitlines()
    for line in ("PartOf=graphical-session.target", "After=graphical-session.target",
                 "WantedBy=graphical-session.target"):
        assert line in lines
    assert not any(l.startswith("WantedBy=default.target") for l in lines)


def test_changed_unit_of_an_enabled_service_is_re_enabled_before_the_restart(env):
    inst(env, Run(), version="1.0")
    env.unit.write_text("[Service]\nExecStart=%h/.local/bin/homalawn serve\n[Install]\nWantedBy=graphical-session.target\n")
    run = Run(enabled=True, service=True)
    out = inst(env, run, version="1.0")
    c = run.calls
    assert c.index(["systemctl", "--user", "daemon-reload"]) < c.index(REENABLE) < c.index(RESTART)
    assert any("Re-enabled the Homa Lawn service" in l for l in out)


def test_unit_left_on_default_target_is_re_enabled_even_when_the_file_is_current(env):
    inst(env, Run(), version="1.0")
    wants = env.upath.parent / "default.target.wants"
    wants.mkdir()
    (wants / "homalawnd.service").symlink_to(env.upath)    # an earlier re-enable failed
    run = Run(enabled=True, service=True)
    inst(env, run, version="1.0")
    assert REENABLE in run.calls and RESTART not in run.calls


def test_a_disabled_service_is_not_re_enabled_behind_the_users_back(env):
    inst(env, Run(), version="1.0")
    env.unit.write_text("[Service]\nExecStart=/x serve\n")
    run = Run()
    inst(env, run, version="1.0")
    assert REENABLE not in run.calls
