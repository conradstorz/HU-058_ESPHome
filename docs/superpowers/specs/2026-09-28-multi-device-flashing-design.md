# Multi-device flashing design

Date: 2026-09-28
Scope: `firmware/esphome/`

## Goal

Run any number of HU-058 clocks side by side in Home Assistant from one
codebase. Every clock has a unique ESPHome name and unique API encryption
key and OTA password. Presenting a clock over USB is enough: a new clock
gets a fresh identity minted from the time it was first flashed, and a
clock seen before gets exactly the identity it had.

The first clock, `wifi-clock`, is already adopted in Home Assistant and
must keep its name and secrets. Its compiled configuration must not change.

## Device identity

The ESP32 factory MAC address, read over USB with esptool before flashing,
is the hardware key. It survives reflashing and erasing. The first clock's
MAC is `20:50:0d:17:f4:58`. MACs are stored lowercase, colon separated.

## File layout

All paths are relative to `firmware/esphome/`.

| File | Tracked | Purpose |
|------|---------|---------|
| `clock-base.yaml` | yes | Today's `clock.yaml` with `substitutions`, `api.encryption` and the `ota` block removed. Nothing else changes. `clock.yaml` is deleted. |
| `<name>.yaml` | yes | One per device. Substitutions, `packages: {clock: !include clock-base.yaml}`, `api: {encryption: {key: !secret api_key_<name_>}}`, `ota: [{platform: esphome, password: !secret ota_password_<name_>}]`. `<name_>` is the device name with hyphens replaced by underscores. |
| `wifi-clock.yaml` | yes | The first clock's device file, hand written in this change. |
| `devices.yaml` | yes | Registry. A list of `{mac, name, friendly_name, first_flashed}`. Seeded with the first clock. |
| `secrets.yaml` | no | Shared `wifi_ssid`, `wifi_password`, `ap_password`, `timezone`, plus `api_key_<name_>` and `ota_password_<name_>` per device. The existing `api_key` and `ota_password` entries are renamed to `api_key_wifi_clock` and `ota_password_wifi_clock` with their values unchanged. |
| `secrets.yaml.example` | yes | Updated to show the shared entries and one per-device pair. |
| `flash.py` | yes | The flashing tool. Run with `uv run flash.py`. |
| `pyproject.toml` | yes | Declares `esphome`, `esptool`, `pyserial`, `pyyaml` and `pytest`. |
| `tests/test_flash.py` | yes | Unit tests for the pure parts of `flash.py`. |
| `.gitignore` | yes | Adds `venv/` and `__pycache__/`. |

Device files stay flat in `firmware/esphome/` because ESPHome resolves
`!secret` and the `external_components` local path relative to the
configuration file's directory.

## Naming

New devices are named `clock-YYYYMMDD-HHMM` with friendly name
`Clock YYYY-MM-DD HH:MM`, using the workstation's local time at the moment
the device is first registered. If a name already exists in the registry
(two clocks registered within one minute), the tool exits with an error
and asks the user to retry a minute later.

Home Assistant exposes the actions of a new clock as
`esphome.clock_YYYYMMDD_HHMM_<action>`.

## `flash.py`

Usage: `uv run flash.py [--port COMx] [--register-only] [-- <esphome run args>]`

1. **Find the port.** Enumerate USB serial ports with pyserial. Exactly one
   present: use it. `--port` overrides. Zero or more than one without
   `--port`: exit with an error listing what was found.
2. **Read the MAC.** Run `esptool --port <port> read_mac` and parse the
   `MAC:` line. Failure to talk to the chip is an error.
3. **Look up the registry.** Load `devices.yaml`.
   - Known MAC: use the stored name. If `<name>.yaml` is missing, regenerate
     it from the registry entry. If either of its secrets is missing from
     `secrets.yaml`, exit with an error. Secrets are never regenerated for a
     known device, because that would break the Home Assistant pairing.
   - Unknown MAC: mint the name, generate a 32 byte random API key
     (base64) and a 32 hex character OTA password, append both to
     `secrets.yaml`, append the registry entry, write `<name>.yaml`. Print
     the new name and tell the user the API key is in `secrets.yaml` for
     adoption in Home Assistant.
4. **Flash.** Unless `--register-only`, run
   `esphome run <name>.yaml --device <port>` with any pass-through
   arguments appended. The tool's exit code is esphome's.

`secrets.yaml` is edited by appending lines, never rewritten, so comments
and formatting in the user's file survive. `devices.yaml` is rewritten by
pyyaml; it holds no comments.

## Verification

- `uv run pytest`: name minting from a fixed datetime, MAC normalisation,
  registry lookup, device file rendering, secrets append, duplicate-name
  error, missing-secret error for a known device.
- `esphome config wifi-clock.yaml` compared against `esphome config
  clock.yaml` at the commit before this change. The two outputs must be
  identical.
- `esphome config` on a freshly minted device file must succeed.
- End to end: flash the clock on COM4 with `uv run flash.py` and adopt it
  in Home Assistant alongside the first clock.

## Documentation

The README flashing section is rewritten around `uv run flash.py`,
explains the registry and the per-device secrets, and notes that the
first clock is registered by hand.
