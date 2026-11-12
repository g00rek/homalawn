import json
import pytest
from homalawn.adb import Device, ProviderError
from homalawn.transport import NotSupported

PKG = "app.homalawn.debug"

class FakeRunner:
    def __init__(self, outputs): self.calls, self.outputs = [], list(outputs)
    def __call__(self, argv, stdin=None):
        self.calls.append((argv, stdin)); return self.outputs.pop(0)

def bundle(ok, **kv):
    parts = [f"ok={'true' if ok else 'false'}"] + [f"{k}={v}" for k, v in kv.items()]
    return (0, f"Result: Bundle[{{{', '.join(parts)}}}]\n".encode(), b"")

def test_call_parses_result_json():
    r = FakeRunner([bundle(True, result=json.dumps({"package": PKG, "x": "a, b"}))])
    assert Device("emu", r, PKG).call("ping") == {"package": PKG, "x": "a, b"}
    argv = r.calls[0][0]
    assert argv[:3] == ["adb", "-s", "emu"]
    assert "content://app.homalawn.debug.omalawn" in " ".join(argv) and "ping" in argv[-1]


def test_the_old_app_is_still_reached_by_its_own_authority():
    r = FakeRunner([bundle(True, result="{}")])
    Device("emu", r, "app.lawnchair.debug").call("ping")
    assert "content://app.lawnchair.debug.omalawn" in r.calls[0][0][-1]

def test_call_parses_result_first_order():
    out = b'Result: Bundle[{result={"a":"x, ok=false","b":1}, ok=true}]\n'
    assert Device("emu", package=PKG, runner=FakeRunner([(0, out, b"")])).call("ping") == {"a": "x, ok=false", "b": 1}

def test_call_passes_extras_and_arg():
    r = FakeRunner([bundle(True, result="{}")])
    Device("emu", r, PKG).call("pref_set", arg="z", extras={"key": "k", "value": "v w"})
    argv = " ".join(r.calls[0][0])
    assert "--arg z" in argv and "--extra key:s:k" in argv
    assert "'value:s:v w'" in argv

def test_call_raises_on_error():
    with pytest.raises(ProviderError, match="unknown method"):
        Device("emu", package=PKG, runner=FakeRunner([bundle(False, error="unknown method nope")])).call("nope")

def test_run_as_write_streams_stdin():
    r = FakeRunner([(0, b"", b"")])
    Device("emu", r, PKG).run_as_write("databases/launcher.db", b"DATA")
    argv, stdin = r.calls[0]
    assert stdin == b"DATA" and "-T" in argv and "run-as app.homalawn.debug" in " ".join(argv)


def test_run_as_write_is_atomic_and_quotes():
    r = FakeRunner([(0, b"", b"")])
    Device("emu", r, PKG).run_as_write("databases/a b.db", b"X")
    cmd = r.calls[0][0][-1]
    assert "databases/a b.db.omalawn-tmp" in cmd and " mv " in cmd and "&&" in cmd
    assert cmd.index("umask 077") < cmd.index("cat >") < cmd.index(" mv ")


def test_is_running():
    assert Device("emu", package=PKG, runner=FakeRunner([(0, b"1234\n", b"")])).is_running()
    assert not Device("emu", package=PKG, runner=FakeRunner([(1, b"", b"")])).is_running()


def test_start_home_is_explicit():
    r = FakeRunner([(0, b"", b"")])
    Device("emu", r, PKG).start_home()
    assert "-n app.homalawn.debug/app.lawnchair.LawnchairLauncher" in r.calls[0][0][-1]


@pytest.mark.parametrize("first", [(0, b"Error\n", b""),                       # no "Success" printed
                                   (255, b"", b"Exception occurred: no such command")])  # non-zero exit
def test_set_home_falls_back_to_role(first):
    r = FakeRunner([first, (0, b"", b"")])
    Device("emu", r, PKG).set_home()
    assert "set-home-activity" in r.calls[0][0][-1] and "add-role-holder android.app.role.HOME" in r.calls[1][0][-1]


def test_runner_timeout_and_missing_adb(monkeypatch):
    import subprocess
    from homalawn import adb
    from homalawn.adb import AdbError

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("adb", 60)
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(AdbError, match="did not answer within 60s"):
        adb.subprocess_runner(["adb", "-s", "x", "shell", "true"])

    def missing(*a, **k):
        raise FileNotFoundError("adb")
    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(AdbError, match="not installed"):
        adb.subprocess_runner(["adb"])


def test_release_build_files_say_to_use_the_link():
    """run-as works only for a debuggable app; the release build's files go over the link."""
    r = FakeRunner([(1, b"", b"run-as: package not debuggable: app.homalawn\n")])
    with pytest.raises(NotSupported, match="over the paired link"):
        Device("emu", r, "app.homalawn").run_as_write("files/omalawn/x", b"X")
    r = FakeRunner([(0, b"run-as: package not debuggable: app.homalawn\n", b"")])
    with pytest.raises(NotSupported, match="over the paired link"):
        Device("emu", r, "app.homalawn").run_as_read("files/omalawn/x")


def test_package_found_on_the_phone(monkeypatch):
    monkeypatch.delenv("HOMALAWN_PACKAGE", raising=False)
    listing = b"package:com.a\npackage:app.homalawn\npackage:app.lawnchair.debug\n"
    r = FakeRunner([(0, listing, b""), (0, b"app.lawnchair.debug\n", b"")])
    assert Device("emu", r).package == "app.lawnchair.debug"   # two installed: the default home app
    r = FakeRunner([(0, listing, b""), (0, b"com.other.launcher\n", b"")])
    assert Device("emu", r).package == "app.homalawn"          # neither is home: the new one first
    r = FakeRunner([(0, b"package:app.lawnchair.debug\n", b"")])
    assert Device("emu", r).package == "app.lawnchair.debug"   # only the old one
    r = FakeRunner([(0, b"package:com.a\n", b"")])
    assert Device("emu", r).package == "app.homalawn"          # none yet: setup installs the new one


def test_package_from_the_environment(monkeypatch):
    monkeypatch.setenv("HOMALAWN_PACKAGE", "app.homalawn.debug")
    r = FakeRunner([])
    assert Device("emu", r).package == "app.homalawn.debug" and r.calls == []
