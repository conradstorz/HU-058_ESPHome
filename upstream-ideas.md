# Upstream ideas

Notes on what to offer back to the original project,
[misterblack1/HU-058_ESPHome](https://github.com/misterblack1/HU-058_ESPHome).
This fork is `conradstorz/HU-058_ESPHome`; the local remotes are `origin`
(fork) and `upstream` (original).

As of 2026-09-30 upstream is still at `10b2d64`, the commit this fork was
cloned from, so the fork is strictly ahead and nothing needs merging down.

## Status

| PR | Upstream | Branch on `origin` | Opened | State |
| --- | --- | --- | --- | --- |
| 1, timezone docs | [#7](https://github.com/misterblack1/HU-058_ESPHome/pull/7) | `upstream-timezone-docs` | 2026-09-30 | open, waiting |
| 2, heartbeat LED | [#8](https://github.com/misterblack1/HU-058_ESPHome/pull/8) | `upstream-heartbeat` | 2026-09-30 | open, waiting |
| 3, second clock | not opened | `upstream-second-clock`, pushed, one commit `5ea4927` | | on hold |
| 4, config split | not opened | | | on hold |
| 5, `flash.py` | not opened | | | on hold |

Both open PRs were cut from `upstream/main` at `10b2d64`, validated with
`esphome config clock.yaml` on ESPHome 2026.9.0 against a `secrets.yaml`
built from upstream's example, and end with the line "Created for Conrad
Storz with the help of Claude Code (conradstorz@gmail.com)". Nothing else
goes upstream until the maintainer accepts, rejects, or clearly ignores
these two. Check with:

```
gh pr list --repo misterblack1/HU-058_ESPHome --author conradstorz --state all
```

Three possible outcomes and what each means for the rest of this file:

- **Accepted.** Continue with PR 3 as written, then 4 and 5, each cut from
  the merged `upstream/main`.
- **Rejected or changes requested.** Read the reasons before touching
  PR 3; the objection may apply to the whole sequence.
- **Ignored for a month or more.** Stop offering small steps. The fork's
  `flash.py` and per-clock device files are the way this project flashes
  regardless; if anything goes upstream later, offer PRs 3 to 5 together as
  one self-contained PR.

Lessons from opening the first two, for whoever prepares the next one:

- Upstream's line endings are not uniformly CRLF. `README.md` and
  `docs/home-assistant.md` are LF. `clock.yaml` and
  `secrets.yaml.example` are mixed, mostly CRLF with a few LF lines. Match
  the ending of the neighbouring lines and check with `git ls-files --eol`.
  The fork's `core.autocrlf=input` does not normalise a file whose index
  blob already contains CRLF, so a careful edit commits without churn.
- The fork's `secrets.yaml` uses per-clock secret names, so validating an
  upstream-shaped `clock.yaml` needs a throwaway `secrets.yaml` made from
  `secrets.yaml.example` with a real base64 key pasted in. Delete it and
  the `.esphome/` build directory before committing.
- Work in a `git worktree` under the session scratchpad cut from
  `upstream/main`, never on the fork's `main`. Remove it after the push.
- Open with `gh pr create --repo misterblack1/HU-058_ESPHome --base main
  --head conradstorz:<branch>`.

## Goal

Introduce ourselves with the smallest change that is obviously good at a
glance, adds functionality every user benefits from, and stands on its own.
Then build on it in steps small enough that each one is easy to accept.
The sequence is PR 1 (timezone docs, a typo fix and clearer wording), PR 2
(heartbeat LED, 17 added lines in one file), PR 3 (second clock, zero
existing lines touched), PR 4 (tidy the shape into a base package and
per-clock files), PR 5 (`flash.py`). PR 1, PR 2 and PR 3 are all cut from
`upstream/main` and are independent of each other. PR 4 and PR 5 are each
cut from the previous one's branch, since they edit files the earlier PR
creates, so if the maintainer merges them in order every diff stays small.

## PR 1: make the timezone rules unmissable

Opened 2026-09-30 as upstream #7. Docs only, independent of every other
PR, cut from `upstream/main`. Went first because a typo fix plus clearer
wording is the softest possible introduction and touches no behavior.

Prompted by a real trip: a freshly flashed clock showed UTC because the
`secrets.yaml.example` ships `Etc/UTC` and the clock had not been adopted
yet. The upstream docs do say Home Assistant's timezone wins, but the
sentence "that path wins, but only while the `homeassistant` time platform
has no timezone of its own. Do not add one there." reads as a
contradiction unless you already know that `timezone:` is an optional key
on that platform and that leaving it out is what enables the push.

What changes:

- `docs/home-assistant.md`: "Home Assitant" typo, and a pointer to the
  README section.
- `firmware/esphome/README.md`: replace the two Setup paragraphs with a
  "Where the timezone comes from" subsection that shows the `time:` block,
  says plainly that Home Assistant wins once adopted, that `secrets.yaml`
  only feeds the SNTP fallback, that a clock reading hours off is on the
  fallback and not yet adopted, and that adding `timezone:` under
  `platform: homeassistant` disables the push. Notes the Home Assistant
  2026.3.0 minimum for the push, verified in `api_connection.cpp` under
  `USE_HOMEASSISTANT_TIMEZONE`.
- `clock.yaml` (`clock-base.yaml` in the fork): rewrite the two comments on
  the `time:` platforms to say primary and fallback, and that the missing
  `timezone:` key is deliberate and is the switch.
- `secrets.yaml.example`: two comment lines above `timezone` saying it is
  fallback only.

All four edits are on the fork's `main` and, as of 2026-09-30, on the
`upstream-timezone-docs` branch as one commit. The `clock.yaml` comment
lands in a different region from PR 2's heartbeat block, so the two do
not conflict. The PR text also flags the one claim worth a reviewer's eye:
ESPHome's single global timezone means a Home Assistant disconnect does
not bring the `secrets.yaml` value back, which is why the docs call it a
startup value rather than a standby.

## PR 2: heartbeat on the devkit LED

Opened 2026-09-30 as upstream #8. The only change is fork commit
`fa1e768`: a `gpio` output on GPIO2 and a 4s `interval` that turns it on
for two seconds and off for two. Seventeen added lines in `clock.yaml`
(upstream's name for the shared file), inserted just above `switch:`,
nothing modified, nothing exposed to Home Assistant.

Why it is the first code change:

- Zero risk to the display. GPIO2 is unused by the panel and the buttons,
  and the interval runs in the main loop, nowhere near the scan ISR.
- Every user benefits. The blue LED on the back of the case says the
  firmware is alive without opening an app, and it is the first thing a
  new user sees after flashing a bare devkit.
- Trivially reviewable. Two YAML blocks and a comment; the maintainer can
  read the whole diff in the PR summary.

Caveats stated in the PR text: GPIO2 is the classic ESP32 devkit LED.
Other boards put their LED elsewhere or use a WS2812, so the block is a
no-op there rather than a fault; this fork supports only the classic
ESP32, and `possible-future-directions.md` has the notes on the rest.
GPIO2 is also a strapping pin, so
`esphome config` prints ESPHome's standard strapping-pin warning; the PR
deliberately leaves it visible and mentions `ignore_strapping_warning:
true` as the maintainer's option. That was a conscious choice: the block
stays at 17 lines and the maintainer decides how noisy config output
should be.

## PR 3: a second clock without touching `clock.yaml`

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

Branch: cut `upstream-second-clock` from `upstream/main`, not from the
PR 2 branch. The two PRs touch disjoint files, so they merge in either
order and neither should carry the other's commit. Add the three files by
hand, verify with `uv run esphome config clock-2.yaml`, push to `origin`,
open the PR against upstream.

## PR 4: move the shared config into `clock-base.yaml`

Follows PR 3 and tidies what PR 3 leaves loose. After PR 3, `clock.yaml`
is both the first clock's file and the package every other clock
includes, so it still carries the first clock's name, API key and OTA
password, and every other clock inherits those unless it overrides them.
PR 4 makes the shared part a package with no identity in it and turns
every clock, the first included, into a small device file.

What changes, relative to PR 3:

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
- `clock-2.yaml` from PR 3: same shape as `clock.yaml`, including
  `clock-base.yaml` and its own two secrets.
- `secrets.yaml.example`: the two per-clock entries for each device file.
- `firmware/esphome/README.md`: the PR 3 paragraph updated so the recipe
  is "copy `clock.yaml`, change the two names and the two secret names".
- Comments in `docs/wiring.md`, `components/aip33628/__init__.py` and
  `components/aip33628/aip33628.h` that name `clock.yaml` should point at
  `clock-base.yaml` where they describe the shared config. The
  `esphome.wifi_clock_<name>` comment PR 3 left stale gets fixed here.
- A troubleshooting bullet in `firmware/esphome/README.md`, next to the
  existing BOOT-button and OTA-rollback notes under "Build and flash",
  for the wrong-board case. esptool's message is
  `This chip is ESP8266, not ESP32. Wrong chip argument?`, which arrives
  only after the full compile and points at the config when the real
  cause is an ESP8266 on the cable, most likely the kit's own ESP-01S.
  Say that no config change fixes it, the firmware needs a separate
  ESP32, and give the pre-flight check so nobody waits through a compile
  to learn it: `uv run esptool --port COM4 chip-id`. About eight lines,
  docs only. Could ride with PR 3 instead if PR 4 stalls.

Why it earns its place after PR 3:

- Each clock gets its own API key and OTA password, so revoking or
  re-pairing one never touches another.
- The package has no identity in it, so nothing stale is inherited and no
  overrides are needed.
- It is the file shape `flash.py` writes, so PR 5 has nothing to reshape.
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
`upstream/main` once PR 3 merges), apply the changes by hand rather than
cherry-picking (the fork commits carry the rename and line-ending churn),
verify with `uv run esphome config clock.yaml` and `clock-2.yaml`, push to
`origin`, open the PR against upstream.

## PR 5, later: `flash.py`

Once PR 4 lands, `flash.py` is a natural follow-up because it only writes
files in the shape PR 4 defines. It brings port discovery, the MAC
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
  makes sense once each clock has its own small device file (PR 4).
- It is 300 lines of Python plus tests and uv landing in a YAML and C++
  repo. The maintainer has to decide whether to own a Python tool before
  judging whether it is a good one, which is the opposite of obvious at a
  glance.

## Not for upstream

- `AGENTS.md`, `docs/superpowers/`, `.superpowers/`: fork process notes.
- `clock-20260929-0912.yaml` and the `devices.yaml` entries: our hardware.
