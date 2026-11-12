# Security policy

## Reporting a vulnerability

Please report security problems privately, through GitHub's private vulnerability reporting: the
repository's **Security** tab › **Report a vulnerability**
(https://github.com/g00rek/homalawn/security/advisories/new). Do not open a public issue for them.

Say what is affected, how to reproduce it, and what an attacker gains. A fix is released as a new
build.

## Supported versions

Only the latest release is supported: the newest `build-<N>` release of the phone app and the
`homalawn` tool from the same commit. Fixes are not backported; update both (`homalawn update`
sends the new phone app to a paired phone).

## In scope

- **The link** between the computer and the phone: the TLS connection on port 47100, certificate
  pinning, who gets answers, the rate limits.
- **Pairing**: the four words, the Accept on the phone, anything that pairs without that tap or
  with another device in the middle.
- **Updates**: anything that gets a phone, or `homalawn setup` / `homalawn update`, to install an
  app not signed with Homa Lawn's key, an older build, or a file that does not match the release's
  checksum.
- **Screen control**: anything that lets a computer see or operate the screen without the person
  switching screen control on, or reach screens Android keeps from accessibility services.
- **The computer side**: the service's local socket, the files under `~/.config/homalawn/`
  (keys, pairings), and the tool server for coding agents doing more than its tools say.

## Out of scope

- Someone who already controls the phone over adb (USB or Wireless debugging), or who has the
  unlocked phone in hand.
- A paired computer doing what pairing allows (see [Security and privacy](docs/guide/security.md)
  for the full list), including through a coding agent the person connected.
- Bugs in upstream Lawnchair that Homa Lawn does not change: report those to
  [Lawnchair](https://github.com/LawnchairLauncher/lawnchair).
