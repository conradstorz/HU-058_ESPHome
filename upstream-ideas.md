# Upstream ideas

Notes on what to offer back to the original project,
[misterblack1/HU-058_ESPHome](https://github.com/misterblack1/HU-058_ESPHome).
This fork is `conradstorz/HU-058_ESPHome`; the local remotes are `origin`
(fork) and `upstream` (original).

As of 2026-09-29 upstream is still at `10b2d64`, the commit this fork was
cloned from, so the fork is strictly ahead and nothing needs merging down.

## Goal

Introduce ourselves with the smallest change that is obviously good at a
glance, adds functionality every user benefits from, and stands on its own.
Then build on it in steps small enough that each one is easy to accept.
The sequence is PR 0 (second clock, zero existing lines touched), PR 1
(tidy the shape into a base package and per-clock files), PR 2
(`flash.py`). Each PR is cut from the previous one's branch, so if the
maintainer merges them in order every diff stays small.

## PR 0: a second clock without touching `clock.yaml`

The smallest meaningful PR touches zero existing lines. Upstream's
`clock.yaml` already declares `substitutions` for `name` and
`friendly_name`, and ESPHome lets an including file override a package's
substitutions. Verified on 2026-09-29 with `esphome config`: a file that
pulls the untouched upstream `clock.yaml` in as a package and sets its own
substitutions resolves to the new name.

What changes:

- New file `clock-2.yaml`, about 8 lines:

  ```yaml
  # Second clock. Copy this file, change the two names, flash it.
  substitutions:
    name: kitchen-clock
    friendly_name: Kitchen Clock
  packages:
    base: !include clock.yaml
  ```

- One paragraph in `firmware/esphome/README.md` under "Build and flash":
  copy the file, change the two names, run `esphome run clock-2.yaml`.
- New `firmware/esphome/.gitignore` with `/secrets.yaml` and `/.esphome/`.
  Upstream has no `.gitignore` at all, so committing secrets by accident
  is the other trap a new user hits. Two lines, zero existing code.

About 14 new lines total, nothing modified.

Trade-offs to state in the PR text:

- Both clocks share one API key and OTA password, since the package
  carries `!secret api_key` and `!secret ota_password`. Home Assistant is
  fine with that. A user who wants separate keys adds four override lines
  (`api: encryption: key:` and `ota: password:`) to the second file.
- The comment in `clock.yaml` saying actions are called as
  `esphome.wifi_clock_<name>` becomes slightly stale for the second clock.
  One-line fix if the maintainer wants it; otherwise leave it.

Branch: cut `upstream-second-clock` from `upstream/main`, add the three
files by hand, verify with `uv run esphome config clock-2.yaml`, push to
`origin`, open with `gh pr create --repo misterblack1/HU-058_ESPHome`.

## PR 1: move the shared config into `clock-base.yaml`

Follows PR 0 and tidies what PR 0 leaves loose. After PR 0, `clock.yaml`
is both the first clock's file and the package every other clock
includes, so it still carries the first clock's name, API key and OTA
password, and every other clock inherits those unless it overrides them.
PR 1 makes the shared part a package with no identity in it and turns
every clock, the first included, into a small device file.

What changes, relative to PR 0:

- `clock-base.yaml`: `clock.yaml` moved here, minus the `substitutions:`
  block, the `api:` encryption key and the `ota:` password, plus a header
  comment saying it is a package and is not flashed on its own. Upstream's
  `esphome:` block already uses `${name}` and `${friendly_name}`, so the
  real change inside the file is about 10 lines.
- `clock.yaml`: becomes a 15-line device file for the first clock. It sets
  the substitutions, `api: encryption: key: !secret api_key_wifi_clock`,
  `ota: password: !secret ota_password_wifi_clock`, and includes
  `clock-base.yaml`. Keeping this filename means `esphome run clock.yaml`
  still works for existing users.
- `clock-2.yaml` from PR 0: same shape as `clock.yaml`, including
  `clock-base.yaml` and its own two secrets.
- `secrets.yaml.example`: the two per-clock entries for each device file.
- `firmware/esphome/README.md`: the PR 0 paragraph updated so the recipe
  is "copy `clock.yaml`, change the two names and the two secret names".
- Comments in `docs/wiring.md`, `components/aip33628/__init__.py` and
  `components/aip33628/aip33628.h` that name `clock.yaml` should point at
  `clock-base.yaml` where they describe the shared config. The
  `esphome.wifi_clock_<name>` comment PR 0 left stale gets fixed here.
- A troubleshooting bullet in `firmware/esphome/README.md`, next to the
  existing BOOT-button and OTA-rollback notes under "Build and flash",
  for the wrong-board case. esptool's message is
  `This chip is ESP8266, not ESP32. Wrong chip argument?`, which arrives
  only after the full compile and points at the config when the real
  cause is an ESP8266 on the cable, most likely the kit's own ESP-01S.
  Say that no config change fixes it, the firmware needs a separate
  ESP32, and give the pre-flight check so nobody waits through a compile
  to learn it: `uv run esptool --port COM4 chip-id`. About eight lines,
  docs only. Could ride with PR 0 instead if PR 1 stalls.

Why it earns its place after PR 0:

- Each clock gets its own API key and OTA password, so revoking or
  re-pairing one never touches another.
- The package has no identity in it, so nothing stale is inherited and no
  overrides are needed.
- It is the file shape `flash.py` writes, so PR 2 has nothing to reshape.
- Still no Python. Compiled config for the first clock was verified
  byte-identical to today's in the fork (plan Task 2), and should be
  re-verified on the PR branch.

Differences from the fork to watch when preparing the branch:

1. Upstream keeps `clock.yaml` as the flashable file. The fork uses
   `wifi-clock.yaml`. Do not rename in the PR.
2. Upstream's files use CRLF line endings. Preserve them or the diff shows
   819 changed lines instead of about 10.
3. Do not include `clock-20260929-0912.yaml`, `devices.yaml`, or any
   per-device secrets. `secrets.yaml.example` gains only the entries the
   two device files reference.

Branch: cut `upstream-config-split` from `upstream-second-clock` (or from
`upstream/main` once PR 0 merges), apply the changes by hand rather than
cherry-picking (the fork commits carry the rename and line-ending churn),
verify with `uv run esphome config clock.yaml` and `clock-2.yaml`, push to
`origin`, open the PR against upstream.

## PR 2, later: `flash.py`

Once PR 1 lands, `flash.py` is a natural follow-up because it only writes
files in the shape PR 1 defines. It brings port discovery, the MAC
registry in `devices.yaml`, per-device secrets, and the ESP32 chip check.
Offer it with its tests and `pyproject.toml`; leave `uv.lock` out unless
the maintainer wants it.

Pitch it as a simpler install, not as a multi-device feature: plug in a
clock, run one command, done. No hunting for the COM port, no hand-editing
a device file, no pasting API keys, and a clear refusal if the board is
not an ESP32. The registry is the mechanism that makes a repeat flash
safe, since a board the tool has seen before gets the same name and
secrets back and its Home Assistant pairing survives. Single-clock users
get the simpler install; multi-clock users get the rest for free.

Why it comes last:

- Port discovery mostly duplicates `esphome run`, which already picks the
  port when one board is attached and prompts when there are several.
- The chip check catches a real mistake (an ESP8266 on COM4 during Task 6)
  but esptool refuses the wrong image anyway, just with a worse message.
- The MAC lookup only pays off with the registry, and the registry only
  makes sense once each clock has its own small device file (PR 1).
- It is 300 lines of Python plus tests and uv landing in a YAML and C++
  repo. The maintainer has to decide whether to own a Python tool before
  judging whether it is a good one, which is the opposite of obvious at a
  glance.

## Not for upstream

- `AGENTS.md`, `docs/superpowers/`, `.superpowers/`: fork process notes.
- `clock-20260929-0912.yaml` and the `devices.yaml` entries: our hardware.
