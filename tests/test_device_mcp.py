"""The real seam for `homalawn mcp`: the unit tests fake both the MCP wire and the phone; this one
replaces neither. It starts the user's installed `homalawn mcp` (the `uv tool` install agents
launch, not this checkout's .venv) over stdio, lists its tools and calls
phone_status against the test AVD over adb (screen_* need the link, so it does not call them).

Own temp config and data: never the real pairing in ~/.config/homalawn."""
import asyncio
import os
import shutil
from pathlib import Path

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

pytestmark = pytest.mark.device

VENV_BIN = (Path(__file__).resolve().parents[1] / ".venv" / "bin").resolve()


def installed_homalawn() -> str | None:
    """The `homalawn` an agent starts: ~/.local/bin first, else PATH without this checkout's .venv
    (under `uv run` that one comes first and has every dependency, which would hide a stale tool
    install)."""
    local = Path.home() / ".local" / "bin" / "homalawn"
    if local.exists():
        return str(local)
    path = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                           if d and Path(d).resolve() != VENV_BIN)
    return shutil.which("homalawn", path=path)


def test_homalawn_mcp_over_stdio_reaches_the_avd(tmp_path):
    exe = installed_homalawn() or pytest.skip(
        "no installed homalawn (~/.local/bin or PATH outside .venv): "
        "uv tool install --editable <this checkout>")
    serial = os.environ.get("HOMALAWN_SERIAL", "emulator-5560")
    params = StdioServerParameters(command=exe, args=["mcp"], env={
        **os.environ, "HOMALAWN_SERIAL": serial,
        "HOMALAWN_CONFIG": str(tmp_path / "config" / "config.toml"), "HOMALAWN_DATA": str(tmp_path / "data")})

    async def go():
        async with Client(params) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            st = await c.call_tool("phone_status", {})
            return names, st
    names, st = asyncio.run(go())
    assert "screen_tree" in names and "recipe_list" in names
    assert not st.is_error, st.content[0].text
    assert serial in st.content[0].text
