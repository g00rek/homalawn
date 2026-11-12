# Contributing to Homa Lawn

Thanks for your interest. Homa Lawn is a young, alpha project; bug reports, ideas and pull requests
are all welcome. For anything larger than a small fix, please open an issue first so we can agree on
the approach.

## Development setup

```sh
git clone https://github.com/g00rek/homalawn.git && cd homalawn
uv sync                                   # Python 3.12+, dev dependencies (pytest, ruff) included
uv run homalawn --help                    # run from the checkout
uv tool install --editable .              # optional: `homalawn` on PATH, following this checkout
```

An editable install follows code changes but not new dependencies: after a pull that changes
`pyproject.toml`, run `uv tool install --editable --reinstall .`.

Building the phone app is optional (the tool downloads releases). It needs a full clone, JDK 21
(`mise install` uses the pinned `mise.toml`), the Android SDK and a Lawnchair clone in `lawnchair/`:

```sh
git clone https://github.com/LawnchairLauncher/lawnchair.git lawnchair
scripts/build-lawnchair.sh debug          # applies patches/, builds the test build, prints its path
```

Without a signing key you get the test build, **Homa Lawn Test** (`app.homalawn.debug`);
`scripts/make-signing-key.sh` makes your own key for release builds. Details in
[docs/development/patching-lawnchair.md](docs/development/patching-lawnchair.md).

Repository layout:

| Path | What |
|---|---|
| `src/homalawn/` | The desktop package: CLI, service, MCP server, link client |
| `src/homalawn/shell/` | The omarchy-shell plugin (QML) |
| `src/homalawn/recipes/` | Procedures agents follow inside other apps (shipped in the package) |
| `patches/` | The Lawnchair patch series, `BASE` (the Lawnchair commit) and `BUILD_OFFSET` |
| `scripts/` | Building the phone app, signing key, releases, the test emulator |
| `tests/` | Unit tests; `test_device_*.py` and `tests/device/` need the emulator |
| `assets/` | The icon and the script that makes every icon size |
| `docs/` | [User guide](docs/guide/) and [development docs](docs/development/) |

## Tests and lint

```sh
uv run pytest -q -m "not device and not slow"     # unit tests, no phone needed (what CI runs)
uv run ruff check                                 # lint (what CI runs)
```

Every change needs unit tests. Changes to the phone side or the link should also pass the device
tests on an emulator: see [docs/development/testing.md](docs/development/testing.md). **Never run
device tests against a phone you use**: they rewrite its home screen.

## Changing Lawnchair

Read [docs/development/patching-lawnchair.md](docs/development/patching-lawnchair.md) before
touching `patches/`. The patch policy, in short:

1. **A Lawnchair setting first**: if a setting can do it, use the setting.
2. **Our own folder, thin hooks**: new code goes in `lawnchair/src/app/lawnchair/omalawn/`;
   upstream files get only short hooks that call into it.
3. **Generic launcher bugs go to Lawnchair**: a bug every Lawnchair user has is reported to
   [Lawnchair](https://github.com/LawnchairLauncher/lawnchair/issues), not only patched here.

Each patch is one commit inside `lawnchair/`, exported with `git format-patch`. Keep upstream
copyright notices intact. After changing patches, `scripts/build-lawnchair.sh` must apply them all
cleanly and build. Export your patch before building: the script resets `lawnchair/`.

## Style

- Python: follow the code around you; type hints, small functions, no printing outside `cli.py`
  (operations return results, see `ops.py`).
- Words a person reads follow one vocabulary: **phone**, **computer**, **pair** / **Unpair** /
  **Forget**, **Wallpaper & style**, **Apply**, **History**, **Screen control**, **Home app**,
  **Run in background**; American spelling; menu paths with `›`.
- Messages are one plain sentence that says what happened and what to do.
- Docs: plain English, short sentences, examples with neutral data (`Pixel 8`, `192.168.1.50`,
  `100.64.0.10`). Never commit real addresses, serials, names or paths from your machine.

## Commits and pull requests

- Commit messages in English: a short subject line that states the change, optionally with a scope
  (`shell:`, `lawnchair:`, `fix(update):`, `docs:`), and a body that explains why when it is not
  obvious.
- One logical change per commit; keep refactors separate from behavior changes.
- A pull request says what changed for the user, how you tested it (unit, device, by hand on which
  phone and Android version), and links the issue.
- Add a line to [CHANGELOG.md](CHANGELOG.md) for anything a user would notice.

## Reporting bugs and security issues

Use the issue templates for bugs and feature requests, and leave out personal data: replace
addresses, phone names, serials and paths with placeholders. Report security problems privately
through GitHub's private vulnerability reporting (the **Security** tab), not in a public issue; see
[SECURITY.md](SECURITY.md).

By contributing you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
