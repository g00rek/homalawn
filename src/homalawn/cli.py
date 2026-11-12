from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import typer

from . import config, env, ops
from .session import set_home_hint
from .adb import AdbTransport
from .link import addresses

app = typer.Typer(help="Run your Android phone's Homa Lawn home screen from this computer: layout, wallpaper & "
                       "style, history, screen control and the tool server for coding agents.",
                  no_args_is_help=True)

CELL = "(0-based: 0 is the first page, the left column, the top row)"


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("homalawn")
    except PackageNotFoundError:
        return "unknown"


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"homalawn {_version()}")
        raise typer.Exit()


def _fail(msg: str) -> typer.Exit:
    typer.echo(msg, err=True)
    return typer.Exit(1)


_state: dict = {"s": None}  # the ops.Session built from the global options of this run
ADB_TCP_PORT = 5555  # `adb connect HOST` default; HOST:5555 is never tried as the link


def _say_err(msg: str) -> None:
    typer.echo(msg, err=True)


@app.callback()
def _main(ctx: typer.Context,
          via: str = typer.Option(None, "--via", metavar="adb|link",
                                  help="How to reach the phone (default: the paired link when it answers, else adb)"),
          serial: str = typer.Option(None, "--serial", metavar="SERIAL",
                                     help="The phone's adb serial (also HOMALAWN_SERIAL); without --via it means adb, "
                                          "never the link"),
          phone: str = typer.Option(None, "--phone", metavar="NAME",
                                    help="Which paired phone (its name, or the start of its id from `homalawn "
                                         "phones`; also HOMALAWN_PHONE), needed when several are paired"),
          version: bool = typer.Option(False, "--version", is_eager=True, callback=_print_version,
                                       help="Print the version of homalawn and exit")):
    if via is not None and via not in ("adb", "link"):
        raise typer.BadParameter("must be adb or link", param_hint="--via")
    try:
        addresses.migrate_config_hosts()
    except Exception as e:
        _say_err(f"Cannot move the phone addresses out of config.toml: {e}")
        raise typer.Exit(1)
    old = _state.get("s")
    if old is not None:
        old.reset()
    s = ops.Session(via=via, serial=serial, phone=phone, notify=_say_err)
    _state["s"] = s
    ctx.call_on_close(s.reset)


def _session() -> ops.Session:
    if _state["s"] is None:
        _state["s"] = ops.Session(notify=_say_err)
    return _state["s"]


def _notes(notes) -> None:
    for n in notes or []:
        typer.echo(n)


def _do(op, *args, **kwargs) -> ops.Result:
    """Run an op on this run's Session: its notes are printed, a failure becomes the CLI's one line."""
    try:
        r = op(_session(), *args, **kwargs)
    except ops.OpError as e:
        _notes(e.notes)
        raise _fail(str(e))
    _notes(r.notes)
    return r


def _show(r: ops.Result) -> None:
    if r.text:
        typer.echo(r.text)


def _resolve(la, spec: str) -> str:
    """ops._resolve with the CLI's failure line."""
    try:
        return ops._resolve(la, spec)
    except ops.OpError as e:
        raise _fail(str(e))


@app.command()
def connect(host: str = typer.Argument(None, metavar="[HOST]",
                                      help="The phone's address: IP[:port] or name; omit to find the phone")):
    """Connect to the phone, remember how, and check Homa Lawn answers.

    With a paired phone, HOST is tried over the link first (port 47100) and remembered for it;
    otherwise (or with an adb port such as :5555) it is an `adb connect`."""
    try:
        _connect(host)
    except ops.OpError as e:
        raise _fail(str(e))


def _connect(host: str | None) -> None:
    from .link import identity, wire
    s = _session()
    via = s.via
    if host:
        h, port = wire.parse_host(host)
        peers = identity.load_peers() if via != "adb" else []
        if peers and (port != ADB_TCP_PORT or via == "link"):
            addr = wire.fmt_host(h, port)
            t, why = ops._link_to(peers, only_hosts=[addr])
            if t is not None:
                try:
                    kept = ops.addresses_add(t.peer.fp, t.address, first=True).text
                except ops.OpError as e:   # the phone answered; only remembering it failed
                    kept = f"{e} {t.address} was not added to its addresses (it is still tried first while it answers)."
                s._t = t
                info = ops._ping(t)
                typer.echo(f"Connected {ops._via(t)} to {t.label}: Homa Lawn {info['version']}, "
                           f"home app: {_yes(info['default_home'])}. {kept}")
                if not info["default_home"]:
                    typer.echo(f"Homa Lawn is not the home app; {set_home_hint()}")
                return
            if via == "link":
                raise _fail(why)
            typer.echo(f"{why}. Trying adb at {host} instead.", err=True)
        elif via == "link":
            raise _fail("No phone is paired; run `homalawn pair` first.")
        serial = host if ":" in host else f"{host}:5555"
        try:
            r = subprocess.run(["adb", "connect", serial], capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            raise _fail("adb is not installed or not on PATH (install Android platform-tools)")
        except subprocess.TimeoutExpired:
            raise _fail(f"adb connect {serial} did not answer within 30 s.")
        if r.returncode != 0 or "connected" not in r.stdout:
            raise _fail((r.stdout + r.stderr).strip() or f"adb connect {serial} failed")
        t = AdbTransport(serial)
        s._t = t
    else:
        t = s.transport()
    info = ops._ping(t)
    if t.name == "adb":
        config.save_serial(t.label)
    typer.echo(f"Connected {ops._via(t)}: Homa Lawn {info['version']}, home app: {_yes(info['default_home'])}")
    if not info["default_home"]:
        typer.echo(f"Homa Lawn is not the home app; "
                   f"{set_home_hint(t.label if t.has_shell else None, info.get('package'))}")


def _yes(v) -> str:
    return "yes" if v else "no"


@app.command()
def status(as_json: bool = typer.Option(False, "--json", help="Print everything known as JSON, with technical "
                                                              "details")):
    """Show how the phone is reached and whether Homa Lawn answers."""
    r = _do(ops.status)
    typer.echo(json.dumps(r.data, indent=2) if as_json else r.text)


layout_app = typer.Typer(help="Read or replace the whole home screen layout.", no_args_is_help=True)
folder_app = typer.Typer(help="Folders on the home screen.", no_args_is_help=True)
snapshot_app = typer.Typer(help="History: save the phone's state as a snapshot, list snapshots, restore one.",
                           no_args_is_help=True)
widget_app = typer.Typer(help="Home screen widgets.", no_args_is_help=True)
app.add_typer(layout_app, name="layout")
app.add_typer(folder_app, name="folder")
app.add_typer(snapshot_app, name="snapshot")
prefs_app = typer.Typer(help="Homa Lawn's settings on the phone.", no_args_is_help=True)
app.add_typer(widget_app, name="widget")
app.add_typer(prefs_app, name="prefs")
look_app = typer.Typer(help="Wallpaper & style: the phone's wallpaper and colors from Omarchy (change the "
                            "settings in the Homa Lawn window).", no_args_is_help=True)
app.add_typer(look_app, name="look")


@layout_app.command("get")
def layout_get(as_json: bool = typer.Option(False, "--json", help="Print the full layout as JSON (the dock is "
                                                                 "`hotseat` there)")):
    """Show the current home screen layout."""
    r = _do(ops.layout_get)
    typer.echo(json.dumps(r.data, indent=2) if as_json else r.text)


@layout_app.command("apply")
def layout_apply(file: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False, metavar="FILE",
                                             help="A layout JSON file, in the shape `layout get --json` prints")):
    """Replace the home screen with a layout JSON file (snapshot taken first)."""
    _show(_do(ops.layout_apply, json.loads(file.read_text())))


@app.command()
def place(app_: str = typer.Argument(..., metavar="APP", help="Package or package/activity"),
          screen: int = typer.Option(..., "--screen", help="Home screen page, 0 is the first"),
          x: int = typer.Option(..., "--x", help="Column, 0 is the left"),
          y: int = typer.Option(..., "--y", help="Row, 0 is the top")):
    """Put an app at a cell (moves it if it is already on the home screen).

    Cells are counted from 0: page 0 is the first page, column 0 the left, row 0 the top."""
    _show(_do(ops.place, app_, screen, x, y))


@app.command()
def remove(target: str = typer.Argument(..., metavar="APP|ID",
                                       help="Package, package/activity, or an item id from `layout get --json`")):
    """Remove an app (package, package/activity, or item id) from the home screen."""
    _show(_do(ops.remove, target))


@folder_app.command("create")
def folder_create(name: str = typer.Argument(..., metavar="NAME", help="The folder's name"),
                  apps: list[str] = typer.Argument(..., metavar="APP...", help="Package or package/activity of each app"),
                  screen: int = typer.Option(..., "--screen", help="Home screen page, 0 is the first"),
                  x: int = typer.Option(..., "--x", help="Column, 0 is the left"),
                  y: int = typer.Option(..., "--y", help="Row, 0 is the top")):
    """Create a folder holding the given apps.

    Cells are counted from 0: page 0 is the first page, column 0 the left, row 0 the top."""
    _show(_do(ops.folder_create, name, apps, screen, x, y))


@app.command("apps")
def apps_cmd():
    """List launchable apps on the phone."""
    _show(_do(ops.apps))


@app.command("widgets")
def widgets_cmd():
    """List widgets that can be added (default size in cells, provider, label)."""
    _show(_do(ops.widgets))


@widget_app.command("add")
def widget_add(provider: str = typer.Argument(..., metavar="PROVIDER",
                                              help="The widget's provider, from `homalawn widgets`"),
               screen: int = typer.Option(..., "--screen", help="Home screen page, 0 is the first"),
               x: int = typer.Option(..., "--x", help="Column of its top-left cell, 0 is the left"),
               y: int = typer.Option(..., "--y", help="Row of its top-left cell, 0 is the top"),
               w: int = typer.Option(None, "--w", help="Width in cells (default: the widget's own)"),
               h: int = typer.Option(None, "--h", help="Height in cells (default: the widget's own)"),
               wait: int = typer.Option(120, "--wait", help="Seconds to wait for the widget to be ready")):
    """Add a widget at a cell and wait until it is live.

    Cells are counted from 0: page 0 is the first page, column 0 the left, row 0 the top."""
    _show(_do(ops.widget_add, provider, screen, x, y, w, h, wait=wait, progress=typer.echo))


@snapshot_app.command("save")
def snapshot_save(name: str = typer.Argument(None, metavar="[NAME]",
                                             help="Name for the snapshot (default: manual-<date>-<time>)")):
    """Save the phone's current home screen under a name, to restore later."""
    _show(_do(ops.snapshot_save, name))


@snapshot_app.command("list")
def snapshot_list():
    """List snapshots, newest first, with the phone each was taken on.

    They are the ones Homa Lawn saved before each change, and your own."""
    _show(_do(ops.snapshot_list))


@snapshot_app.command("restore")
def snapshot_restore(name: str = typer.Argument(..., metavar="NAME", help="A snapshot from `snapshot list`"),
                     force: bool = typer.Option(False, "--force", help="Restore a snapshot of another or an unknown phone"),
                     no_backup: bool = typer.Option(False, "--no-backup",
                                                    help="Replace the wallpaper without saving the current one")):
    """Restore a snapshot (layout, preferences and grid, or wallpaper; whatever it holds)."""
    _show(_do(ops.snapshot_restore, name, force=force, no_backup=no_backup))


@prefs_app.command("list")
def prefs_list(filter_: str = typer.Argument(None, metavar="[FILTER]", help="Part of the setting's key")):
    """List Homa Lawn's settings on the phone (known ones without a stored value show as default)."""
    _show(_do(ops.prefs_list, filter_))


@prefs_app.command("get")
def prefs_get(key: str = typer.Argument(..., metavar="KEY", help="The setting's key, from `prefs list`")):
    """Show one setting."""
    _show(_do(ops.prefs_get, key))


@prefs_app.command("set")
def prefs_set(key: str = typer.Argument(..., metavar="KEY", help="The setting's key, from `prefs list`"),
              value: str = typer.Argument(..., metavar="VALUE", help="The new value"),
              store: str = typer.Option(None, "--store", help="datastore or shared (unknown keys only)"),
              type_: str = typer.Option(None, "--type", help="bool, int, float or string (unknown keys only)")):
    """Set a Homa Lawn setting. The previous values are saved first; `homalawn undo` puts them back."""
    _show(_do(ops.pref_set, key, value, store, type_))


def _look_settings():
    """This phone's saved wallpaper & style settings (keyed by its fingerprint), defaults when it has none."""
    from . import lookconf
    from .link import identity
    try:
        t = _session().transport()
    except ops.OpError as e:
        raise _fail(str(e))
    peer = getattr(t, "peer", None)
    key = peer.fp if peer is not None else t.label      # adb: the serial, as before
    looks = lookconf.load(peers=[(p.fp, p.name) for p in identity.load_peers()])
    return t.label, looks.get(key) or lookconf.LookSettings()


@look_app.command("apply")
def look_apply():
    """Apply wallpaper & style to the phone now, from its settings.

    Pick the phone with `homalawn --phone NAME look apply`. The phone's current wallpapers and colors
    are saved first; `homalawn undo` puts them back."""
    _, settings = _look_settings()
    _show(_do(ops.look_apply, settings))


@look_app.command("show")
def look_show():
    """Print this phone's wallpaper & style settings (as JSON)."""
    name, settings = _look_settings()
    typer.echo(f"{name}:")
    typer.echo(json.dumps(settings.to_dict(), indent=2))


@app.command()
def grid(size: str = typer.Argument(..., metavar="COLSxROWS", help="Columns x rows, e.g. 4x5")):
    """Set the home screen grid (columns x rows), the same way Homa Lawn's settings do."""
    m = re.fullmatch(r"(\d+)[xX](\d+)", size)
    if not m:
        raise _fail("The grid size must look like 4x5 (columns x rows).")
    _show(_do(ops.grid, int(m[1]), int(m[2])))


@app.command("nova-import")
def nova_import(file: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False, metavar="FILE",
                                            help="The .novabackup file (make a backup in Nova's settings)"),
                dry_run: bool = typer.Option(False, "--dry-run", help="Only say what would be placed and left out"),
                glance_row: bool = typer.Option(None, "--glance-row/--no-glance-row",
                                                help="Homa Lawn's \"Add extra row to show At a Glance\" (default: as "
                                                     "the phone has At a Glance now)")):
    """Move a Nova Launcher home screen onto the phone.

    Everything it changes is saved first; `homalawn undo` puts the previous home screen back."""
    _show(_do(ops.nova_import, str(file), dry_run=dry_run, glance_row=glance_row))


@app.command()
def wallpaper(file: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False, metavar="FILE",
                                          help="An image file (PNG, JPEG, ...)"),
              lock: bool = typer.Option(False, "--lock", help="set the lock screen wallpaper instead of the home one"),
              both: bool = typer.Option(False, "--both", help="set the home and the lock screen wallpaper"),
              crop: str = typer.Option("none", "--crop", metavar="center",
                                       help="center: scale to cover the phone's screen and keep the middle"),
              no_backup: bool = typer.Option(False, "--no-backup", help="overwrite without saving the current wallpaper")):
    """Set the wallpaper from an image file (the current one is saved first)."""
    if lock and both:
        raise _fail("--lock and --both cannot be used together.")
    if crop not in ("none", "center"):
        raise _fail("--crop takes center (or none).")
    _show(_do(ops.wallpaper, str(file), no_backup=no_backup, target="both" if both else "lock" if lock else "home",
              crop=crop))


def _save_png(data: bytes, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    typer.echo(str(out))


@app.command()
def screenshot(out: Path = typer.Argument(None, metavar="[FILE]",
                                          help="Where to save it (default: ~/Pictures/homalawn-<time>.png)")):
    """Save a screenshot of the phone (the same as `homalawn screen shot`).

    Over the paired link both are taken through screen control; this one also works over adb
    (`--via adb`)."""
    out = out or Path.home() / "Pictures" / f"homalawn-{time.strftime('%Y%m%d-%H%M%S')}.png"
    _save_png(_do(ops.screenshot).data, out)


PAIR_BROWSE_S = 6.0  # longer than avahi's 5 s wait for an advert that does not resolve


def _find_phone_for_pairing() -> tuple[str, int]:
    """No HOST given: the one Homa Lawn phone mDNS finds on this network, or a clear error."""
    from .link import discovery
    how = ("Pass its address: `homalawn pair HOST` (the phone's Wi-Fi or Tailscale address; on the phone, "
           "Settings › About phone › Status shows the IP address).")
    if discovery.missing():
        raise _fail(discovery.MISSING + " " + how)
    found = discovery.phones(discovery.browse(timeout=PAIR_BROWSE_S))
    if len(found) == 1:
        h = found[0]
        typer.echo(f"Found the phone at {h.host}.")
        return h.address, h.port
    if not found:
        raise _fail("No phone with Homa Lawn answered on this network (it must be on the same Wi-Fi as this "
                    "computer, with Homa Lawn running). " + how)
    listing = ", ".join(f"{h.host} ({h.hostname or h.name})" for h in found)
    raise _fail(f"Several Homa Lawn phones found: {listing}. " + how)


@app.command()
def pair(host: str = typer.Argument(None, metavar="[HOST[:PORT]]",
                                    help="The phone's address, port 47100 by default; looked for on this network "
                                         "when left out"),
         name: str = typer.Option(None, "--name", help="How the phone shows this computer (default: its host name)")):
    """Pair this computer with the phone: both screens show four words; Accept on the phone if they match."""
    from .link import identity, pairing, wire

    # the phone's Accept is the only confirmation (owner decision 2026-10-12): the person compares the
    # words printed here with the phone's before tapping it; a second question here is a later gate
    h, port = wire.parse_host(host) if host else _find_phone_for_pairing()

    def show_words(words):
        typer.echo("")
        typer.echo("Words: " + typer.style("  ".join(words), bold=True))

    try:
        peer = pairing.pair(h, port, name=name, confirm=lambda words: True, show_words=show_words,
                            say=lambda m: typer.echo(m, err=False))
    except KeyboardInterrupt:
        raise _fail("\nStopped. Nothing was paired.")
    except (pairing.PairError, identity.IdentityError) as e:
        raise _fail(str(e))
    except (OSError, ConnectionError, wire.LinkError, RuntimeError) as e:
        raise _fail(f"Pairing failed: {e}")
    typer.echo(f"Paired with {peer.name}.")


@app.command("setup")
def setup_cmd(serial: str = typer.Option(None, "--serial", metavar="SERIAL",
                                         help="The phone's adb serial (default: the one phone connected over adb, "
                                              "else connect one over Wireless debugging by QR code)"),
              apk: Path = typer.Option(None, "--apk", dir_okay=False,
                                       help="The Homa Lawn app (APK file) to install (default: a build in this "
                                            "checkout, else the newest release)"),
              new: bool = typer.Option(False, "--new",
                                       help="A phone this computer has never seen: connect it over Wireless "
                                            "debugging by QR code even when other phones are connected over adb"),
              via: str = typer.Option(None, "--via", metavar="link",
                                      help="A phone already set up, over the Homa Lawn link only (no adb): update "
                                           "it, check each step, name any setting to switch on"),
              phone: str = typer.Option(None, "--phone", metavar="NAME",
                                        help="Which paired phone (its name or the start of its id; also "
                                             "HOMALAWN_PHONE), needed when several are paired"),
              no_screen_control: bool = typer.Option(False, "--no-screen-control",
                                                     help="Leave screen control as it is (without it an agent can "
                                                          "change the home screen but not operate other apps)"),
              debug_forward: bool = typer.Option(False, "--debug-forward", hidden=True)):
    """Set up a phone in one go over adb: install Homa Lawn, set it up, pair it, check it works.

    The steps: install Homa Lawn, make it the home app, let it run in the background, switch screen
    control on (running setup is your consent; skip it with --no-screen-control), pair it with this
    computer, and check it works. Safe to run again on a phone that is already set up; one that is
    not on adb but answers over the Homa Lawn link is updated and checked over the link."""
    from . import setup
    s = _session()
    via = via or s.via
    phone = phone or s.phone
    if via not in (None, "link", "adb"):
        raise typer.BadParameter("must be link (or adb)", param_hint="--via")
    if via == "link":
        if new or serial or s.serial:
            raise _fail("--via link sets up a phone already paired over the Homa Lawn link; leave out --new and "
                        "--serial with it.")
        try:
            setup.run(typer.echo, apk=apk, via="link", phone_name=phone or env.get("PHONE") or None,
                      screen_control=not no_screen_control)
        except KeyboardInterrupt:
            raise _fail("\nStopped.")
        except setup.SetupError as e:
            raise _fail(str(e))
        return
    if new:
        if serial or s.serial:
            raise _fail("--new connects a phone that is not on adb yet; leave out --serial with it.")
        serial = None  # HOMALAWN_SERIAL names a phone already on adb; --new is about another one
    else:
        serial = serial or s.serial or env.get("SERIAL") or None
    try:
        setup.run(typer.echo, serial=serial, apk=apk, debug_forward=debug_forward, new=new,
                  ask=lambda q: typer.prompt(q), via=via,
                  phone_name=phone or env.get("PHONE") or None, screen_control=not no_screen_control)
    except KeyboardInterrupt:
        raise _fail("\nStopped.")
    except setup.SetupError as e:
        raise _fail(str(e))


@app.command("update")
def update_cmd(apk: Path = typer.Option(None, "--apk", dir_okay=False,
                                        help="The Homa Lawn app (APK file) to send (default: a build in this "
                                             "checkout, else the newest release)"),
               download: bool = typer.Option(False, "--download",
                                             help="Give the phone the app to download (QR code + address), for a "
                                                  "phone whose Homa Lawn cannot update itself yet: the newest "
                                                  "release straight from GitHub; a build from this checkout or "
                                                  "--apk is served on this computer's network for 10 minutes"),
               port: int = typer.Option(None, "--port", metavar="N", min=1, max=65535,
                                        help="With --download: the port to serve on (default 47101)"),
               phone: str = typer.Option(None, "--phone", metavar="NAME",
                                         help="Which paired phone (its name or the start of its id; also "
                                              "HOMALAWN_PHONE), needed when several are paired")):
    """Send a new Homa Lawn build to the paired phone over the link.

    A phone allowed to "Install unknown apps" for Homa Lawn installs it by itself (its home screen
    restarts); otherwise it shows an update notification (tap it, then Update). With --download, the
    phone downloads it instead: a release from its GitHub address, a build made here from this computer."""
    if port is not None and not download:
        raise _fail("--port is only used with --download.")
    from . import update
    try:
        if download:
            app = update.new_phone_app(apk, say=typer.echo)
            if app["source"] == "release":
                update.from_github(typer.echo, app)
                return
            try:
                update.download(typer.echo, app["apk"], port=port or update.DOWNLOAD_PORT)
            except KeyboardInterrupt:
                typer.echo("Stopped serving the build.")
            return
        path = update.pick_apk(apk, say=typer.echo) if apk is not None else None
        s = _session()
        if s.via == "adb":
            raise _fail("Updates go over the Homa Lawn link; for adb use `homalawn setup`.")
        link = ops.Session(via="link", phone=phone or s.phone, notify=_say_err)
        try:
            update.send(typer.echo, link.transport(), path)
        finally:
            link.reset()
    except ops.OpError as e:
        raise _fail(f"{str(e).rstrip('.')}. (A phone that cannot be reached over the link can download the build: "
                    "`homalawn update --download`.)")
    except update.UpdateError as e:
        raise _fail(str(e))
    except KeyboardInterrupt:
        raise _fail("\nStopped; run `homalawn update` again to continue where it stopped.")


@app.command()
def unpair(name: str = typer.Argument(None, metavar="[NAME]",
                                     help="The phone's name or the start of its id, from `homalawn phones`")):
    """Forget a paired phone here, and ask the phone to forget this computer."""
    from .link import identity, pairing
    try:
        peer = ops._peer_named(name)
        told = pairing.unpair(peer)
    except ops.OpError as e:
        raise _fail(str(e))
    except identity.IdentityError as e:
        raise _fail(str(e))
    if told:
        typer.echo(f"Unpaired {peer.name}: the phone ({told}) and this computer have forgotten each other.")
    else:
        typer.echo(f"Forgot {peer.name} on this computer. The phone could not be reached, so it still lists this "
                   "computer until you forget it there: Homa Lawn's settings › Computers › this computer › Forget.")


addresses_app = typer.Typer(help="A paired phone's addresses; run bare to list them (pick the phone with "
                                 "`homalawn --phone NAME addresses`).", invoke_without_command=True)
app.add_typer(addresses_app, name="addresses")


@addresses_app.callback()
def addresses_cmd(ctx: typer.Context):
    """List the phone's addresses and which one answered last."""
    if ctx.invoked_subcommand is None:
        _show(_op_addresses(ops.addresses_list))


def _op_addresses(op, *args) -> ops.Result:
    try:
        return op(ops._named_phone(_session()), *args)
    except ops.OpError as e:
        raise _fail(str(e))


@addresses_app.command("add")
def addresses_add(host: str = typer.Argument(..., metavar="HOST", help="Host name or IP address, optional :port")):
    """Add an address to dial the phone on (at most 5)."""
    _show(_op_addresses(ops.addresses_add, host))


@addresses_app.command("remove")
def addresses_remove(host: str = typer.Argument(..., metavar="HOST", help="One of the addresses you added")):
    """Remove one of the phone's added addresses."""
    _show(_op_addresses(ops.addresses_remove, host))


@app.command()
def phones():
    """List the phones paired with this computer."""
    from .link import identity
    ps = identity.load_peers()
    if not ps:
        typer.echo("No phone is paired yet; run `homalawn pair`.")
        return
    for p in ps:
        typer.echo(phone_line(p))


@app.command("peers", hidden=True)
def peers():
    """List the phones paired with this computer (the old name of `homalawn phones`)."""
    phones()


def phone_line(p) -> str:
    """One paired phone in words: its name, when it was paired, where it answered last, and the
    start of its id (what --phone also takes)."""
    when = time.strftime("%Y-%m-%d", time.localtime(p.paired)) if p.paired else "on an unknown date"
    where = p.last_ok or p.last_lan or (p.hosts[0] if p.hosts else None)
    return (f"{p.name}: paired {when}" + (f", last reached at {where}" if where else "")
            + f" (id {p.fp[:16]})")


screen_app = typer.Typer(help="Screen control: read and operate the phone's screen in any app (over the paired "
                              "link).", no_args_is_help=True)
app.add_typer(screen_app, name="screen")


def _screen_args(fn, *args):
    """Parse command-line screen arguments; a bad one is the CLI's one line, before the phone is asked."""
    try:
        return ops._screen_args(fn, *args)
    except ops.OpError as e:
        raise _fail(str(e))


@screen_app.command("status")
def screen_status():
    """Is screen control (Homa Lawn's accessibility service) on?"""
    _show(_do(ops.screen_status))


@screen_app.command("tree")
def screen_tree(as_json: bool = typer.Option(False, "--json", help="Print the phone's raw node list")):
    """What is on the screen: an indented list of items with their ids (n12) and centre points."""
    r = _do(ops.screen_tree)
    if as_json:
        typer.echo(json.dumps(r.data, indent=2, ensure_ascii=False))
        return
    _show(r)


@screen_app.command("tap")
def screen_tap(target: list[str] = typer.Argument(..., metavar="NODE | X,Y | X Y",
                                               help="An item id from `screen tree` (n12), or a point in screen pixels")):
    """Tap an item from `screen tree` (n12) or a point."""
    from . import screen as screen_mod
    _show(_do(ops.screen_tap, _screen_args(screen_mod.parse_point, target)))


@screen_app.command("long-press")
def screen_long_press(target: list[str] = typer.Argument(..., metavar="NODE | X,Y | X Y",
                                                      help="An item id from `screen tree` (n12), or a point in "
                                                           "screen pixels"),
                      ms: int = typer.Option(None, "--ms", help="How long to hold, in milliseconds (default 800)")):
    """Touch and hold an item or a point."""
    from . import screen as screen_mod
    _show(_do(ops.screen_long_press, _screen_args(screen_mod.parse_point, target), ms))


@screen_app.command("swipe")
def screen_swipe(points: list[str] = typer.Argument(..., metavar="X1,Y1 X2,Y2",
                                                 help="Start and end point, in screen pixels"),
                 ms: int = typer.Option(None, "--ms", help="How long the swipe takes, in milliseconds (default 300)")):
    """Swipe from one point to another."""
    from . import screen as screen_mod
    p = _screen_args(screen_mod.parse_swipe, points)
    if ms is not None:
        p["ms"] = ms
    _show(_do(ops.screen_swipe, p))


@screen_app.command("type")
def screen_type(text: str = typer.Argument(..., metavar="TEXT", help="The text to put in"),
                node: str = typer.Option(None, "--node", help="The text field's id from `screen tree` (default: the "
                                                               "one with the focus)")):
    """Put text into a text field (replaces what it holds)."""
    _show(_do(ops.screen_type, text, node))


@screen_app.command("key")
def screen_key(name: str = typer.Argument(..., metavar="back|home|recents|notifications|quick_settings",
                                          help="The key to press")):
    """Press a system key."""
    _show(_do(ops.screen_key, name))


@screen_app.command("shot")
def screen_shot(out: Path = typer.Argument(None, metavar="[FILE]",
                                           help="Where to save it (default: ~/Pictures/homalawn-screen-<time>.png)")):
    """Save a screenshot taken through screen control (the same as `homalawn screenshot`)."""
    out = out or Path.home() / "Pictures" / f"homalawn-screen-{time.strftime('%Y%m%d-%H%M%S')}.png"
    _save_png(_do(ops.screen_shot).data, out)


@screen_app.command("launch")
def screen_launch(package: str = typer.Argument(..., metavar="PACKAGE",
                                              help="The app's package, e.g. com.android.settings")):
    """Open an app by its package name."""
    _show(_do(ops.screen_launch, package))


@app.command()
def undo(no_backup: bool = typer.Option(False, "--no-backup",
                                        help="Replace the wallpaper without saving the current one")):
    """Undo the last change Homa Lawn made on this phone; repeat to go further back.

    A change is one to the home screen, a setting, the grid, the wallpaper or wallpaper & style."""
    _show(_do(ops.undo, no_backup=no_backup))


@app.command("mcp")
def mcp_cmd():
    """Run Homa Lawn's tool server for coding agents (MCP over stdio).

    `homalawn desktop install` adds it to the coding agents it finds; an agent can list the paired
    phones and pick one."""
    from . import mcp_server
    s = _session()
    mcp_server.serve(s.via, s.serial, s.phone)


@app.command()
def serve():
    """Run the Homa Lawn service (normally started as homalawnd by `homalawn desktop install`).

    It keeps a link to each paired phone, applies wallpaper & style when the Omarchy theme or
    background changes, and answers the Homa Lawn window on a local socket."""
    from . import daemon
    try:
        daemon.run()
    except daemon.CannotStart as e:
        raise _fail(str(e))


desktop_app = typer.Typer(help="Install or remove Homa Lawn on the Omarchy desktop: the service, the bar icon "
                               "and window, and the tool server for coding agents.", no_args_is_help=True)
app.add_typer(desktop_app, name="desktop")


@desktop_app.command("install")
def desktop_install(no_agents: bool = typer.Option(False, "--no-agents",
                                                   help="Do not add Homa Lawn's tool server to the coding agents "
                                                        "installed here (nor to ones installed later)")):
    """Install Homa Lawn on this Omarchy desktop (safe to run again: it updates).

    It installs the Homa Lawn service (homalawnd), puts the Homa Lawn icon and window in Omarchy's
    bar, and adds Homa Lawn's tool server to the coding agents installed here (skip with
    --no-agents)."""
    from . import agents, desktop
    # recorded before the service starts: its agent loop runs at once and reads this choice
    agents.set_auto_add(not no_agents)
    try:
        desktop.install(Path.home(), exe=desktop.default_exe(), say=typer.echo)
    except desktop.DesktopError as e:
        raise _fail(str(e))
    if no_agents:
        typer.echo("Skipped the coding agents (--no-agents): Homa Lawn's tool server is not added to them, now or "
                   "later; Connect on the Agents page adds it to one.")
    else:
        found = agents.installed_names()
        if found:
            typer.echo(f"Adding Homa Lawn's tool server to the coding agents found here ({', '.join(found)}), so "
                       "they can work with your phone. Skip this with --no-agents; `homalawn desktop uninstall` "
                       "takes it out again.")
        for line in agents.sync():
            typer.echo(line)
    typer.echo("Next: click the phone icon in the bar to open Homa Lawn, then Add phone.")


@desktop_app.command("uninstall")
def desktop_uninstall():
    """Remove Homa Lawn from this desktop: the service, the bar icon and window, the agent entries.

    Pairings, keys and History stay; it says where they are and how to remove them."""
    from . import agents, desktop, release, snapshots
    try:
        desktop.uninstall(Path.home(), say=typer.echo)
    except desktop.DesktopError as e:
        raise _fail(str(e))
    for line in agents.unregister_all():
        typer.echo(line)
    for line in desktop.kept_lines(Path.home(), cfg=config.path().parent, data=snapshots.root().parent,
                                   state=env.state_dir(), cache=release.cache_dir().parent):
        typer.echo(line)


if __name__ == "__main__":
    app()
