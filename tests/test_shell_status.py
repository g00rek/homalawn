"""What the bar mark says about the phones (shell/HomaLawn/Status.js, shared by BarWidget.qml and the
window's rail), run under node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).parent.parent / "src" / "homalawn" / "shell" / "HomaLawn" / "Status.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="needs node")

ALL = {"home": True, "background": True, "notifications": True, "screen_control": True, "install_updates": True}


def phone(name, connected=True, error=None, permissions=ALL, app_latest=None):
    return {"name": name, "connected": connected, "error": error, "permissions": permissions,
            "app_latest": app_latest}


def summary(phones, up=True, present=True):
    prelude = (f"const vm=require('vm');const src=require('fs').readFileSync({json.dumps(str(JS))},'utf8')"
               ".replace(/^\\.pragma library/,'');const module={exports:{}};"
               "vm.runInNewContext(src,{module,JSON,String});const S=module.exports;")
    script = f"console.log(JSON.stringify(S.summary({json.dumps(phones)}, {json.dumps(up)}, {json.dumps(present)})))"
    r = subprocess.run([NODE, "-e", prelude + script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_all_well_lists_phones_connected_first():
    s = summary([phone("Galaxy", connected=False, permissions=None), phone("Pixel")])
    assert s == {"problem": False, "connected": True, "updates": [],
                 "tooltip": "Pixel · connected\nGalaxy · offline"}


def test_a_required_permission_not_allowed_needs_attention():
    s = summary([phone("Pixel", permissions=dict(ALL, background=False))])
    assert s["problem"] is True
    assert s["tooltip"] == "Pixel · needs attention: run in background is not allowed"
    s = summary([phone("Pixel", permissions=dict(ALL, home=False, notifications=False))])
    assert s["tooltip"] == "Pixel · needs attention: home app and notifications are not allowed"


def test_optional_unknown_and_offline_permissions_do_not_count():
    assert summary([phone("Pixel", permissions=dict(ALL, screen_control=False, install_updates=False))])["problem"] is False
    assert summary([phone("Pixel", permissions=dict(ALL, background=None))])["problem"] is False
    assert summary([phone("Pixel", connected=False, permissions=dict(ALL, background=False))])["problem"] is False
    assert summary([phone("Pixel", permissions=None)])["problem"] is False     # an older phone app


def test_an_error_needs_attention_in_its_own_words():
    s = summary([phone("Pixel", error="Homa Lawn on Pixel cannot read the current wallpaper.")])
    assert s["problem"] is True
    assert s["tooltip"] == "Pixel · needs attention: Homa Lawn on Pixel cannot read the current wallpaper."


def test_update_available_adds_a_line():
    s = summary([phone("Pixel", app_latest={"version": "16.0+homalawn.325", "newer": True, "source": "release"}),
                 phone("Moto", app_latest={"version": "16.0+homalawn.325", "newer": False, "source": "release"})])
    assert s["updates"] == ["Pixel"] and s["problem"] is False
    assert s["tooltip"].splitlines()[-1] == "Pixel · update available: 16.0+homalawn.325"


def test_service_down_and_no_phones():
    assert summary([], up=False)["tooltip"] == "Homa Lawn service is not running"
    assert summary([])["tooltip"] == "Homa Lawn · no paired phones"
    assert summary([], present=False)["tooltip"] == "Homa Lawn"
