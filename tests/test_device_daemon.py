"""The Homa Lawn service against the test AVD (HOMALAWN_SERIAL=emulator-5560), over the paired link.

The one test here drives the real seam the unit tests fake: the service's own link session per
paired phone (`ops.Session(via="link", phone=NAME)`), its connection check and the screen size,
while the session fixture's link stays open beside it. Read-only: nothing on the phone changes."""
import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from homalawn import daemon

pytestmark = pytest.mark.device
if not os.environ.get("HOMALAWN_SERIAL"):
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)


def test_service_connects_to_the_paired_avd(link_transport, tmp_path):
    run_dir = Path(tempfile.mkdtemp(prefix="hs"))   # short: unix socket paths are capped at 108 bytes
    svc = daemon.Service(socket_path=run_dir / "homalawn" / "homalawnd.sock", look_path=tmp_path / "look.json",
                         applied_path=tmp_path / "applied.json",
                         current=lambda: (None, None), list_themes=lambda: [])

    async def go():
        await svc.start()
        try:
            fp = link_transport.peer.fp
            assert list(svc.phones) == [fp]
            for _ in range(300):
                if svc.phones[fp].connected:
                    break
                await asyncio.sleep(0.1)
            r, w = await asyncio.open_unix_connection(str(svc.socket_path))
            w.write(b'{"id": 1, "method": "phones"}\n')
            await w.drain()
            got = json.loads(await asyncio.wait_for(r.readline(), 10))
            w.close()
            ph, = got["result"]
            assert ph["id"] == fp and ph["name"] == link_transport.label
            assert ph["connected"] is True and ph["error"] is None
            assert ph["screen"]["w"] > 0 and ph["screen"]["h"] > ph["screen"]["w"]
            assert link_transport.call("launcher.ping")["default_home"] is True  # the other link still works
        finally:
            await svc.stop()

    try:
        asyncio.run(asyncio.wait_for(go(), 60))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
