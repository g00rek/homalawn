import os

import pytest

# Device tests drive the debuggable test build (app.homalawn.debug): its shell-only test hooks and
# `run-as` are what they use. The emulator may also hold the release build (app.homalawn) or the
# old app next to it; HOMALAWN_PACKAGE picks one explicitly.
DEVICE_PACKAGE = os.environ.setdefault("HOMALAWN_PACKAGE", "app.homalawn.debug")


@pytest.fixture(autouse=True)
def _own_config(request, tmp_path, monkeypatch):
    """Unit tests never read this computer's real config, data, cache, agent configs or paired phones (the CLI now dials
    a paired phone on its own), nor move its Homar- or OmaLawn-era folders. The pre-rename HOMAR_*
    and OMALAWN_* names are only a fallback for HOMALAWN_*, so they are cleared here or a shell that
    still sets them would leak in. Device tests manage HOMALAWN_CONFIG and HOMALAWN_DATA themselves."""
    if "device" not in request.keywords:
        for k in [k for k in os.environ if k.startswith(("HOMAR_", "OMALAWN_"))]:
            monkeypatch.delenv(k)
        monkeypatch.delenv("HOMALAWN_PACKAGE", raising=False)  # set above for the device tests
        monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "own-config" / "config.toml"))
        monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "own-data"))
        monkeypatch.setenv("HOMALAWN_STATE_DIR", str(tmp_path / "own-state"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "own-cache"))   # the icon cache
        # the agents' own config files (Agents page): never this computer's Claude Code or Hermes
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "own-claude"))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "own-hermes"))
        # every other agent in homalawn.agents.REGISTRY: its own variable, and the home folder the
        # module (and the agent commands it runs, which get HOME from it) uses
        for var, d in (("CODEX_HOME", "own-codex"), ("GEMINI_CLI_HOME", "own-gemini"),
                       ("COPILOT_HOME", "own-copilot"), ("GROK_HOME", "own-grok"),
                       ("PI_CODING_AGENT_DIR", "own-pi"), ("CRUSH_GLOBAL_CONFIG", "own-crush"),
                       ("OPENCLAW_STATE_DIR", "own-openclaw"), ("OPENCLAW_HOME", "own-home")):
            monkeypatch.setenv(var, str(tmp_path / d))
        for var in ("OPENCLAW_CONFIG_PATH", "OPENCLAW_PROFILE"):
            monkeypatch.delenv(var, raising=False)
        from homalawn import agents
        monkeypatch.setattr(agents, "home", lambda: tmp_path / "own-home")
        # nor search this computer's home network for a phone
        from homalawn.link import addresses
        monkeypatch.setattr(addresses, "lan_sweep", lambda port=None, hosts=None: [])
        # nor ask GitHub for a release, nor pick up the builds in this checkout's lawnchair/ folder
        from homalawn import appid, release
        monkeypatch.delenv("HOMALAWN_RELEASES_REPO", raising=False)
        monkeypatch.delenv("HOMALAWN_RELEASES_CERT_SHA256", raising=False)

        def offline(*a, **k):
            raise release.ReleaseError(release.NO_NETWORK)
        monkeypatch.setattr(release, "_get", offline)
        monkeypatch.setattr(release, "_stream", offline)
        monkeypatch.setattr(appid, "in_checkout", lambda: False)


# Device-session fixtures live here so every device module shares ONE instance of each: a
# session fixture imported into two test modules is two fixtures, set up and torn down twice,
# and the second hygiene check then saw the first module's pairing still pinned.
@pytest.fixture(scope="session")
def avd_hygiene(tmp_path_factory):
    import test_device_seam
    yield from test_device_seam.avd_hygiene_impl(tmp_path_factory)


@pytest.fixture(scope="session")
def link_transport(avd_hygiene, tmp_path_factory):
    import test_device_seam
    yield from test_device_seam.link_transport_impl(avd_hygiene, tmp_path_factory)


@pytest.fixture
def d(avd_hygiene):
    """adb to the AVD, for checking what the phone did (whatever transport did it)."""
    return avd_hygiene.transport.device


@pytest.fixture
def second(tmp_path, monkeypatch, d):
    """A second computer: its own link identity (own config folder), pinned on the phone as
    'Laptop' by the debug hook. Unpinned again at the end."""
    from homalawn.link import identity
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "c2" / "config.toml"))
    ident = identity.ensure()
    d.call("debug_pin", ident.fingerprint, extras={"name": "Laptop"})
    yield ident
    d.call("debug_unpin", ident.fingerprint)


@pytest.fixture
def second_transport(link_transport, second):
    """The phone, called as the `second` computer (nothing in its peers.json: it dials the
    address the session's link uses)."""
    from homalawn.link import identity
    from homalawn.link.client import LinkTransport
    peer = identity.Peer(fp=link_transport.peer.fp, name=link_transport.peer.name, hosts=list(link_transport.peer.hosts))
    t = LinkTransport(peer, ident=second, discover=None, only_hosts=True, hosts=list(peer.hosts))
    t.connect()
    yield t
    t.close()


def computers(d):
    """The phone's computer list by fingerprint (debug hook)."""
    return {c["fp"]: c for c in d.call("debug_computers")["computers"]}


def screen_text(t):
    """All the text on the phone's screen, one string (over screen control)."""
    nodes = t.call("screen.tree").get("nodes", [])
    return " | ".join(n.get("text") or n.get("desc") or "" for n in nodes)


def tap_text(t, text):
    """Tap the node showing exactly `text` (screen control)."""
    nodes = t.call("screen.tree")["nodes"]
    node = next(n for n in nodes if text in (n.get("text"), n.get("desc")))
    t.call("screen.tap", {"node": node["id"]})
