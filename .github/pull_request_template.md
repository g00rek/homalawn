## What changes for the user

<!-- One or two sentences. Link the issue: Fixes #123 -->

## How it was tested

- [ ] `uv run pytest -q -m "not device and not slow"` and `uv run ruff check`
- [ ] Device tests on the emulator (`HOMALAWN_SERIAL=emulator-5560 uv run pytest -m device`), if the phone side or the link changed
- [ ] `scripts/build-lawnchair.sh` applies every patch and builds, if `patches/` changed
- [ ] By hand: <!-- phone / Android version, or "n/a" -->

## Checklist

- [ ] Follows the patch policy in `docs/development/patching-lawnchair.md` (setting first, own folder and thin hooks, generic launcher bugs reported to Lawnchair)
- [ ] Docs and CHANGELOG updated where a user would notice
- [ ] No personal data (addresses, serials, names, paths) in code, tests or docs
