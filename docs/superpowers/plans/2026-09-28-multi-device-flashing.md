# Multi-Device Flashing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any number of HU-058 clocks run side by side in Home Assistant, each with a unique ESPHome name and secrets keyed to its ESP32 factory MAC, flashed with one command.

**Architecture:** The shared ESPHome config becomes a package (`clock-base.yaml`). Each clock has a ~12 line device file that sets its name and points at its own `!secret` entries. A registry (`devices.yaml`) maps MAC → identity. `flash.py` reads the MAC over USB with esptool, mints or reuses the identity, and runs `esphome run`.

**Tech Stack:** ESPHome 2026.9.0 (packages, substitutions, `!secret`), esptool 5.x (`read-mac` subcommand), pyserial, PyYAML, pytest, uv.

Spec: `docs/superpowers/specs/2026-09-28-multi-device-flashing-design.md`

## Global Constraints

- All work happens in `firmware/esphome/`. Paths below are relative to it unless they start with `docs/`.
- The first clock is already adopted in Home Assistant. Its name `wifi-clock`, friendly name `WiFi Clock`, API key and OTA password must not change. Its MAC is `20:50:0d:17:f4:58`.
- Device files stay flat in `firmware/esphome/` (ESPHome resolves `!secret` and the `components` path relative to the config file's directory).
- Names: `clock-YYYYMMDD-HHMM`, friendly `Clock YYYY-MM-DD HH:MM`, local workstation time.
- Per-device secret names: `api_key_<name_>` and `ota_password_<name_>` where `<name_>` is the name with `-` replaced by `_`.
- MACs stored lowercase, colon separated, and quoted in YAML (an all-digit MAC would otherwise parse as a YAML 1.1 sexagesimal integer).
- `secrets.yaml` is gitignored and only ever appended to, never rewritten.
- Secrets are never regenerated for a known device.
- Python via `uv` only: `uv sync`, `uv run pytest`, `uv run flash.py`. Never `pip install` or activate a venv.
- Do not chain shell commands with `&&`. One command per tool call.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Windows host. Shell is PowerShell or Git Bash; the commands below are written for Git Bash (`cd`, `diff`, `cat`).

---

## File Structure

| Path | Action | Responsibility |
|------|--------|----------------|
| `pyproject.toml` | create | uv project: runtime deps and pytest config |
| `.gitignore` | modify | add `venv/`, `__pycache__/`, `.pytest_cache/`, `uv.lock` stays tracked |
| `clock-base.yaml` | create (from `clock.yaml`) | everything shared between clocks |
| `clock.yaml` | delete | replaced by base + device files |
| `wifi-clock.yaml` | create | first clock's identity |
| `devices.yaml` | create | MAC → identity registry |
| `secrets.yaml` | modify (local only) | rename `api_key`/`ota_password` to per-device names |
| `secrets.yaml.example` | modify | document the new layout |
| `flash.py` | create | the tool: pure helpers + I/O + CLI, one module |
| `tests/test_flash.py` | create | unit tests for `flash.py` |
| `README.md` | modify | Start here, Setup, Build and flash, Adopt sections |

`flash.py` stays one module (~200 lines). Pure functions at the top take paths and datetimes as arguments so tests use `tmp_path` and never touch the real `secrets.yaml`.

---

### Task 1: uv project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `tests/__init__.py` (empty)
- Modify: `.gitignore`

**Interfaces:**
- Produces: a working `uv run pytest` and `uv run esphome` in `firmware/esphome/`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "hu058-esphome"
version = "0.1.0"
description = "Flashing tool for HU-058 clocks running ESPHome"
requires-python = ">=3.12"
dependencies = [
    "esphome>=2026.9.0",
    "esptool>=5.0",
    "pyserial>=3.5",
    "pyyaml>=6.0",
]

[dependency-groups]
dev = ["pytest>=8"]

[tool.uv]
package = false

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `tests/__init__.py`** as an empty file.

- [ ] **Step 3: Replace `.gitignore`** with:

```
# ESPHome build output and local secrets
/.esphome/
/secrets.yaml

# Python
/.venv/
/venv/
__pycache__/
.pytest_cache/
```

- [ ] **Step 4: Install**

Run: `uv sync`
Expected: resolves and installs esphome, esptool, pyserial, pyyaml, pytest into `.venv/`. Produces `uv.lock`. Takes a few minutes the first time.

- [ ] **Step 5: Smoke check**

Run: `uv run esphome version`
Expected: `Version: 2026.9.0` (or newer).

Run: `uv run pytest`
Expected: `no tests ran` with exit code 5. That is fine at this point.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock tests/__init__.py .gitignore
git commit -m "Add uv project for the flashing tool

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Split clock.yaml into a package plus the first device file

**Files:**
- Create: `clock-base.yaml`
- Create: `wifi-clock.yaml`
- Create: `devices.yaml`
- Modify: `secrets.yaml` (local, gitignored)
- Modify: `secrets.yaml.example`
- Delete: `clock.yaml`

**Interfaces:**
- Produces: the device file shape that `flash.py` renders in Task 3 (`render_device_yaml` must produce exactly the layout of `wifi-clock.yaml` below with the name substituted), and the `devices.yaml` shape that Task 3 loads.

- [ ] **Step 1: Add the per-device secret names alongside the old ones**

Keep the old `api_key:` and `ota_password:` lines for now so the old and new configs can be compared. Run:

```bash
uv run python -c "
import re, pathlib
p = pathlib.Path('secrets.yaml'); t = p.read_text()
api = re.search(r'^api_key:\s*(.+)$', t, re.M).group(1)
ota = re.search(r'^ota_password:\s*(.+)$', t, re.M).group(1)
p.write_text(t.rstrip('\n') + f'\n\n# wifi-clock\napi_key_wifi_clock: {api}\nota_password_wifi_clock: {ota}\n')
"
```

Expected: `secrets.yaml` ends with the two new lines carrying the same values as the old ones. Check with `tail -4 secrets.yaml`.

- [ ] **Step 2: Create `clock-base.yaml`**

Copy `clock.yaml` to `clock-base.yaml`, then make exactly three edits:

1. Delete the `substitutions:` block (the first three lines) and the blank line after it. Add this comment block in its place at the top of the file:

```yaml
# Shared configuration for every HU-058 clock. This file is not flashed on
# its own. Each clock has a small <name>.yaml next to it that sets the name,
# the API key and the OTA password and pulls this in as a package. Run
# `uv run flash.py` with a clock on USB and it writes that file for you.
```

2. In the `api:` block, delete these two lines (the `actions:` list stays):

```yaml
  encryption:
    key: !secret api_key
```

3. Delete the whole `ota:` block (three lines):

```yaml
ota:
  - platform: esphome
    password: !secret ota_password
```

Everything else is byte for byte the same as `clock.yaml`. `${name}` and `${friendly_name}` stay where they are; the device file supplies them.

- [ ] **Step 3: Create `wifi-clock.yaml`**

```yaml
# Identity for one clock. Everything else is in clock-base.yaml.
substitutions:
  name: wifi-clock
  friendly_name: WiFi Clock

packages:
  clock: !include clock-base.yaml

api:
  encryption:
    key: !secret api_key_wifi_clock

ota:
  - platform: esphome
    password: !secret ota_password_wifi_clock
```

- [ ] **Step 4: Prove the first clock's config is unchanged**

```bash
uv run esphome config clock.yaml > "$TEMP/old.txt"
```
```bash
uv run esphome config wifi-clock.yaml > "$TEMP/new.txt"
```
```bash
diff "$TEMP/old.txt" "$TEMP/new.txt"
```

Expected: no output from `diff`. If lines differ, the split is wrong. Fix `clock-base.yaml` or `wifi-clock.yaml` until the diff is empty. Do not proceed with a non-empty diff.

- [ ] **Step 5: Remove the old secret names and the old config**

```bash
uv run python -c "
import re, pathlib
p = pathlib.Path('secrets.yaml'); t = p.read_text()
t = re.sub(r'^api_key:.*\n', '', t, flags=re.M)
t = re.sub(r'^ota_password:.*\n', '', t, flags=re.M)
p.write_text(t)
"
```
```bash
git rm clock.yaml
```

Run: `uv run esphome config wifi-clock.yaml > /dev/null`
Expected: exit code 0, no `Secret 'api_key' not defined` style error.

- [ ] **Step 6: Create `devices.yaml`**

```yaml
# Registry of every clock flashed from this directory, keyed by the ESP32
# factory MAC address. flash.py appends to this and rewrites it, so keep
# comments out of the entries themselves. MACs are quoted on purpose: an
# all-digit MAC would otherwise parse as a YAML 1.1 sexagesimal integer.
devices:
  - mac: '20:50:0d:17:f4:58'
    name: wifi-clock
    friendly_name: WiFi Clock
    # Registered by hand when the registry was introduced. The clock was
    # first flashed before that, around 2026-09-07.
    first_flashed: '2026-09-28'
```

- [ ] **Step 7: Rewrite `secrets.yaml.example`**

```yaml
# Copy to secrets.yaml and fill in. secrets.yaml is gitignored, this file is not.
#
# The shared entries are yours to fill in once. The per-device entries are
# written by `uv run flash.py` the first time it sees a new clock, so you
# normally never touch them. The pair below shows the shape it writes:
# api_key_<name> and ota_password_<name>, with hyphens in the clock's name
# turned into underscores.
#
# If you ever need an api key by hand:
#   python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"

wifi_ssid: "your network"
wifi_password: "your password"
ap_password: "fallback ap password"
timezone: "Etc/UTC"

# wifi-clock
api_key_wifi_clock: "base64 key, 32 bytes"
ota_password_wifi_clock: "ota password"
```

- [ ] **Step 8: Commit**

```bash
git add clock-base.yaml wifi-clock.yaml devices.yaml secrets.yaml.example
git commit -m "Split clock.yaml into a shared package and a per-device file

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

(`clock.yaml` deletion is already staged by `git rm`.)

---

### Task 3: flash.py pure helpers, test first

**Files:**
- Create: `flash.py`
- Create: `tests/test_flash.py`

**Interfaces:**
- Produces (all in `flash.py`, used by Task 4):
  - `normalize_mac(mac: str) -> str`
  - `parse_mac_output(text: str) -> str`
  - `mint_name(now: datetime) -> tuple[str, str]` returning `(name, friendly_name)`
  - `secret_names(name: str) -> tuple[str, str]` returning `(api_key_name, ota_password_name)`
  - `render_device_yaml(name: str, friendly_name: str) -> str`
  - `@dataclass Device(mac: str, name: str, friendly_name: str, first_flashed: str)`
  - `load_registry(path: Path) -> list[Device]`
  - `save_registry(path: Path, devices: list[Device]) -> None`
  - `find_device(devices: list[Device], mac: str) -> Device | None`
  - `generate_api_key() -> str`, `generate_ota_password() -> str`
  - `append_secrets(path: Path, entries: dict[str, str], comment: str) -> None`
  - `missing_secrets(path: Path, names: list[str]) -> list[str]`
  - `class FlashError(Exception)`

- [ ] **Step 1: Write the failing tests**

`tests/test_flash.py`:

```python
from datetime import datetime
from pathlib import Path

import pytest
import yaml

import flash


# --- MAC handling -----------------------------------------------------------

def test_normalize_mac_lowercases_and_uses_colons():
    assert flash.normalize_mac("20:50:0D:17:F4:58") == "20:50:0d:17:f4:58"
    assert flash.normalize_mac("20-50-0D-17-F4-58") == "20:50:0d:17:f4:58"


@pytest.mark.parametrize("bad", ["20:50:0d", "20:50:0d:17:f4:5g", "", "hello"])
def test_normalize_mac_rejects_garbage(bad):
    with pytest.raises(ValueError):
        flash.normalize_mac(bad)


def test_parse_mac_output_finds_mac_line():
    text = (
        "esptool v5.3.1\n"
        "Connected to ESP32 on COM4:\n"
        "Chip type:          ESP32-D0WD-V3 (revision v3.1)\n"
        "MAC:                20:50:0D:17:F4:58\n"
        "\nHard resetting via RTS pin...\n"
    )
    assert flash.parse_mac_output(text) == "20:50:0d:17:f4:58"


def test_parse_mac_output_without_mac_raises():
    with pytest.raises(flash.FlashError):
        flash.parse_mac_output("A fatal error occurred: Could not open COM4")


# --- naming -----------------------------------------------------------------

def test_mint_name_uses_minute_resolution():
    name, friendly = flash.mint_name(datetime(2026, 9, 28, 14, 7, 59))
    assert name == "clock-20260928-1407"
    assert friendly == "Clock 2026-09-28 14:07"


def test_secret_names_replace_hyphens():
    assert flash.secret_names("clock-20260928-1407") == (
        "api_key_clock_20260928_1407",
        "ota_password_clock_20260928_1407",
    )
    assert flash.secret_names("wifi-clock") == ("api_key_wifi_clock", "ota_password_wifi_clock")


# --- device file ------------------------------------------------------------

def test_render_device_yaml_matches_hand_written_first_clock():
    here = Path(__file__).resolve().parent.parent
    expected = (here / "wifi-clock.yaml").read_text()
    assert flash.render_device_yaml("wifi-clock", "WiFi Clock") == expected


# --- registry ---------------------------------------------------------------

def test_registry_round_trip_quotes_all_digit_macs(tmp_path):
    path = tmp_path / "devices.yaml"
    devices = [
        flash.Device("20:50:01:17:14:58", "clock-20260928-1407", "Clock 2026-09-28 14:07", "2026-09-28"),
    ]
    flash.save_registry(path, devices)
    assert flash.load_registry(path) == devices
    # Loaded by plain yaml the MAC must still be a string, not an int.
    raw = yaml.safe_load(path.read_text())
    assert raw["devices"][0]["mac"] == "20:50:01:17:14:58"


def test_load_registry_missing_file_is_empty(tmp_path):
    assert flash.load_registry(tmp_path / "devices.yaml") == []


def test_load_registry_normalizes_macs_and_stringifies_dates(tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text(
        "devices:\n"
        "  - mac: 20:50:0D:17:F4:58\n"
        "    name: wifi-clock\n"
        "    friendly_name: WiFi Clock\n"
        "    first_flashed: 2026-09-28\n"
    )
    [d] = flash.load_registry(path)
    assert d.mac == "20:50:0d:17:f4:58"
    assert d.first_flashed == "2026-09-28"


def test_find_device_by_mac_in_any_case():
    d = flash.Device("20:50:0d:17:f4:58", "wifi-clock", "WiFi Clock", "2026-09-28")
    assert flash.find_device([d], "20:50:0D:17:F4:58") is d
    assert flash.find_device([d], "aa:bb:cc:dd:ee:ff") is None


# --- secrets ----------------------------------------------------------------

def test_generate_api_key_is_32_bytes_base64():
    import base64
    key = flash.generate_api_key()
    assert len(base64.b64decode(key)) == 32
    assert flash.generate_api_key() != key


def test_generate_ota_password_is_32_hex_chars():
    pw = flash.generate_ota_password()
    assert len(pw) == 32
    int(pw, 16)


def test_append_secrets_preserves_existing_text(tmp_path):
    path = tmp_path / "secrets.yaml"
    path.write_text('# my comment\nwifi_ssid: "CandM"')  # no trailing newline on purpose
    flash.append_secrets(path, {"api_key_x": "k+/=", "ota_password_x": "abc"}, comment="x")
    text = path.read_text()
    assert text.startswith('# my comment\nwifi_ssid: "CandM"\n')
    assert text.endswith('\n# x\napi_key_x: "k+/="\nota_password_x: "abc"\n')
    assert yaml.safe_load(text) == {"wifi_ssid": "CandM", "api_key_x": "k+/=", "ota_password_x": "abc"}


def test_append_secrets_refuses_to_overwrite(tmp_path):
    path = tmp_path / "secrets.yaml"
    path.write_text('api_key_x: "old"\n')
    with pytest.raises(flash.FlashError):
        flash.append_secrets(path, {"api_key_x": "new"}, comment="x")
    assert path.read_text() == 'api_key_x: "old"\n'


def test_missing_secrets_lists_absent_names(tmp_path):
    path = tmp_path / "secrets.yaml"
    path.write_text('api_key_x: "k"\n')
    assert flash.missing_secrets(path, ["api_key_x", "ota_password_x"]) == ["ota_password_x"]


def test_missing_secrets_when_file_absent(tmp_path):
    assert flash.missing_secrets(tmp_path / "secrets.yaml", ["a"]) == ["a"]
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest -q`
Expected: collection error, `ModuleNotFoundError: No module named 'flash'`.

- [ ] **Step 3: Write `flash.py` with the pure helpers**

```python
"""Flash an HU-058 clock over USB, minting or reusing its identity.

Every clock is keyed by its ESP32 factory MAC address. A MAC seen for the
first time gets a name from the current time, a fresh API key and OTA
password appended to secrets.yaml, a registry entry in devices.yaml and a
<name>.yaml device file. A MAC seen before gets exactly what it had.

Usage:
    uv run flash.py [--port COMx] [--register-only] [esphome run args...]
"""
from __future__ import annotations

import argparse
import base64
import re
import secrets as pysecrets
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE / "devices.yaml"
SECRETS_PATH = HERE / "secrets.yaml"

_MAC_RE = re.compile(r"MAC:\s*([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5})")
_MAC_PART_RE = re.compile(r"^[0-9a-f]{2}$")


class FlashError(Exception):
    """Something the user has to fix. main() prints it and exits 1."""


# --- MAC handling -----------------------------------------------------------

def normalize_mac(mac: str) -> str:
    parts = mac.strip().lower().replace("-", ":").split(":")
    if len(parts) != 6 or not all(_MAC_PART_RE.match(p) for p in parts):
        raise ValueError(f"not a MAC address: {mac!r}")
    return ":".join(parts)


def parse_mac_output(text: str) -> str:
    m = _MAC_RE.search(text)
    if not m:
        raise FlashError("esptool did not report a MAC address. Output was:\n" + text)
    return normalize_mac(m.group(1))


# --- naming -----------------------------------------------------------------

def mint_name(now: datetime) -> tuple[str, str]:
    return f"clock-{now:%Y%m%d-%H%M}", f"Clock {now:%Y-%m-%d %H:%M}"


def secret_names(name: str) -> tuple[str, str]:
    n = name.replace("-", "_")
    return f"api_key_{n}", f"ota_password_{n}"


# --- device file ------------------------------------------------------------

def render_device_yaml(name: str, friendly_name: str) -> str:
    api_key_name, ota_password_name = secret_names(name)
    return (
        "# Identity for one clock. Everything else is in clock-base.yaml.\n"
        "substitutions:\n"
        f"  name: {name}\n"
        f"  friendly_name: {friendly_name}\n"
        "\n"
        "packages:\n"
        "  clock: !include clock-base.yaml\n"
        "\n"
        "api:\n"
        "  encryption:\n"
        f"    key: !secret {api_key_name}\n"
        "\n"
        "ota:\n"
        "  - platform: esphome\n"
        f"    password: !secret {ota_password_name}\n"
    )


# --- registry ---------------------------------------------------------------

@dataclass
class Device:
    mac: str
    name: str
    friendly_name: str
    first_flashed: str


def load_registry(path: Path) -> list[Device]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or {}
    return [
        Device(
            mac=normalize_mac(str(d["mac"])),
            name=str(d["name"]),
            friendly_name=str(d["friendly_name"]),
            first_flashed=str(d["first_flashed"]),
        )
        for d in raw.get("devices") or []
    ]


def save_registry(path: Path, devices: list[Device]) -> None:
    header = (
        "# Registry of every clock flashed from this directory, keyed by the ESP32\n"
        "# factory MAC address. Maintained by flash.py; it rewrites this file, so\n"
        "# comments inside the entries do not survive.\n"
    )
    body = yaml.safe_dump(
        {"devices": [asdict(d) for d in devices]},
        sort_keys=False,
        default_flow_style=False,
    )
    path.write_text(header + body)


def find_device(devices: list[Device], mac: str) -> Device | None:
    mac = normalize_mac(mac)
    return next((d for d in devices if d.mac == mac), None)


# --- secrets ----------------------------------------------------------------

def generate_api_key() -> str:
    return base64.b64encode(pysecrets.token_bytes(32)).decode()


def generate_ota_password() -> str:
    return pysecrets.token_hex(16)


def _secret_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    raw = yaml.safe_load(path.read_text()) or {}
    return set(raw)


def append_secrets(path: Path, entries: dict[str, str], comment: str) -> None:
    clash = sorted(set(entries) & _secret_keys(path))
    if clash:
        raise FlashError(f"{path.name} already has {', '.join(clash)}; refusing to overwrite")
    text = path.read_text() if path.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    lines = "".join(f'{k}: "{v}"\n' for k, v in entries.items())
    path.write_text(f"{text}\n# {comment}\n{lines}")


def missing_secrets(path: Path, names: list[str]) -> list[str]:
    present = _secret_keys(path)
    return [n for n in names if n not in present]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: all tests in `tests/test_flash.py` pass. If `test_render_device_yaml_matches_hand_written_first_clock` fails, the hand-written `wifi-clock.yaml` from Task 2 and `render_device_yaml` disagree; make `render_device_yaml` match the file (the file is the one already proven equivalent to the old config).

- [ ] **Step 5: Commit**

```bash
git add flash.py tests/test_flash.py
git commit -m "Add flash.py identity helpers with tests

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: flash.py I/O, resolution logic and CLI

**Files:**
- Modify: `flash.py` (append below the helpers from Task 3)
- Modify: `tests/test_flash.py` (append)

**Interfaces:**
- Consumes: everything from Task 3.
- Produces:
  - `find_port(explicit: str | None) -> str`
  - `read_mac(port: str) -> str`
  - `resolve_device(mac: str, now: datetime, registry_path: Path, secrets_path: Path, device_dir: Path) -> tuple[Device, bool]` returning the device and whether it was newly registered
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_flash.py`:

```python
# --- port discovery ---------------------------------------------------------

class _Port:
    def __init__(self, device, vid=None, description=""):
        self.device, self.vid, self.description = device, vid, description


def test_find_port_explicit_wins(monkeypatch):
    monkeypatch.setattr(flash, "_comports", lambda: [_Port("COM3", 0x0403), _Port("COM9", 0x10C4)])
    assert flash.find_port("COM9") == "COM9"


def test_find_port_single_usb_serial(monkeypatch):
    monkeypatch.setattr(flash, "_comports", lambda: [_Port("COM1"), _Port("COM4", 0x0403, "USB Serial Port")])
    assert flash.find_port(None) == "COM4"


def test_find_port_none_found(monkeypatch):
    monkeypatch.setattr(flash, "_comports", lambda: [_Port("COM1")])
    with pytest.raises(flash.FlashError, match="No USB serial"):
        flash.find_port(None)


def test_find_port_ambiguous(monkeypatch):
    monkeypatch.setattr(flash, "_comports", lambda: [_Port("COM3", 0x0403), _Port("COM4", 0x0403)])
    with pytest.raises(flash.FlashError, match="COM3"):
        flash.find_port(None)


# --- read_mac ---------------------------------------------------------------

def test_read_mac_runs_esptool(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
            stdout = "MAC: 20:50:0D:17:F4:58\n"
            stderr = ""
        return R()

    monkeypatch.setattr(flash.subprocess, "run", fake_run)
    assert flash.read_mac("COM4") == "20:50:0d:17:f4:58"
    assert calls[0][:2] == [flash.sys.executable, "-m"]
    assert "esptool" in calls[0]
    assert "COM4" in calls[0]
    assert "read-mac" in calls[0]


def test_read_mac_failure_is_flash_error(monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            returncode = 2
            stdout = ""
            stderr = "A fatal error occurred: Could not open COM4"
        return R()

    monkeypatch.setattr(flash.subprocess, "run", fake_run)
    with pytest.raises(flash.FlashError, match="Could not open COM4"):
        flash.read_mac("COM4")


# --- resolve_device ---------------------------------------------------------

NOW = datetime(2026, 9, 28, 14, 7, 0)


def _layout(tmp_path):
    reg = tmp_path / "devices.yaml"
    sec = tmp_path / "secrets.yaml"
    sec.write_text('wifi_ssid: "x"\n')
    return reg, sec


def test_resolve_new_device_creates_everything(tmp_path):
    reg, sec = _layout(tmp_path)
    device, is_new = flash.resolve_device("AA:BB:CC:DD:EE:FF", NOW, reg, sec, tmp_path)

    assert is_new is True
    assert device == flash.Device("aa:bb:cc:dd:ee:ff", "clock-20260928-1407", "Clock 2026-09-28 14:07", "2026-09-28T14:07:00")
    assert flash.load_registry(reg) == [device]
    assert (tmp_path / "clock-20260928-1407.yaml").read_text() == flash.render_device_yaml(device.name, device.friendly_name)
    secrets_now = yaml.safe_load(sec.read_text())
    assert secrets_now["wifi_ssid"] == "x"
    assert len(secrets_now["api_key_clock_20260928_1407"]) == 44
    assert len(secrets_now["ota_password_clock_20260928_1407"]) == 32


def test_resolve_known_device_reuses_and_touches_nothing(tmp_path):
    reg, sec = _layout(tmp_path)
    flash.resolve_device("aa:bb:cc:dd:ee:ff", NOW, reg, sec, tmp_path)
    before = (sec.read_text(), reg.read_text(), (tmp_path / "clock-20260928-1407.yaml").read_text())

    device, is_new = flash.resolve_device("aa:bb:cc:dd:ee:ff", datetime(2030, 1, 1), reg, sec, tmp_path)

    assert is_new is False
    assert device.name == "clock-20260928-1407"
    assert (sec.read_text(), reg.read_text(), (tmp_path / "clock-20260928-1407.yaml").read_text()) == before


def test_resolve_known_device_regenerates_missing_yaml(tmp_path):
    reg, sec = _layout(tmp_path)
    flash.resolve_device("aa:bb:cc:dd:ee:ff", NOW, reg, sec, tmp_path)
    (tmp_path / "clock-20260928-1407.yaml").unlink()

    device, _ = flash.resolve_device("aa:bb:cc:dd:ee:ff", NOW, reg, sec, tmp_path)

    assert (tmp_path / "clock-20260928-1407.yaml").read_text() == flash.render_device_yaml(device.name, device.friendly_name)


def test_resolve_known_device_with_missing_secret_errors(tmp_path):
    reg, sec = _layout(tmp_path)
    flash.resolve_device("aa:bb:cc:dd:ee:ff", NOW, reg, sec, tmp_path)
    sec.write_text('wifi_ssid: "x"\n')  # secrets lost

    with pytest.raises(flash.FlashError, match="api_key_clock_20260928_1407"):
        flash.resolve_device("aa:bb:cc:dd:ee:ff", NOW, reg, sec, tmp_path)
    # Nothing was regenerated.
    assert yaml.safe_load(sec.read_text()) == {"wifi_ssid": "x"}


def test_resolve_two_devices_same_minute_errors(tmp_path):
    reg, sec = _layout(tmp_path)
    flash.resolve_device("aa:bb:cc:dd:ee:01", NOW, reg, sec, tmp_path)
    with pytest.raises(flash.FlashError, match="clock-20260928-1407"):
        flash.resolve_device("aa:bb:cc:dd:ee:02", NOW, reg, sec, tmp_path)
    assert len(flash.load_registry(reg)) == 1


# --- main -------------------------------------------------------------------

def test_main_register_only_does_not_flash(tmp_path, monkeypatch, capsys):
    reg, sec = _layout(tmp_path)
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    monkeypatch.setattr(flash, "HERE", tmp_path)
    monkeypatch.setattr(flash, "find_port", lambda explicit: "COM4")
    monkeypatch.setattr(flash, "read_mac", lambda port: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(flash, "_now", lambda: NOW)
    monkeypatch.setattr(flash.subprocess, "call", lambda *a, **k: pytest.fail("esphome must not run"))

    rc = flash.main(["--register-only"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "New clock" in out
    assert "clock-20260928-1407" in out
    assert "api_key_clock_20260928_1407" in out


def test_main_flashes_known_device_and_passes_args(tmp_path, monkeypatch):
    reg, sec = _layout(tmp_path)
    flash.resolve_device("aa:bb:cc:dd:ee:ff", NOW, reg, sec, tmp_path)
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    monkeypatch.setattr(flash, "HERE", tmp_path)
    monkeypatch.setattr(flash, "find_port", lambda explicit: "COM4")
    monkeypatch.setattr(flash, "read_mac", lambda port: "aa:bb:cc:dd:ee:ff")
    calls = []
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: calls.append((cmd, kw)) or 7)

    rc = flash.main(["--no-logs"])

    assert rc == 7
    cmd, kw = calls[0]
    assert cmd[:2] == [flash.sys.executable, "-m"]
    assert cmd[2:] == ["esphome", "run", "clock-20260928-1407.yaml", "--device", "COM4", "--no-logs"]
    assert kw["cwd"] == tmp_path


def test_main_reports_flash_error(monkeypatch, capsys):
    monkeypatch.setattr(flash, "find_port", lambda explicit: (_ for _ in ()).throw(flash.FlashError("No USB serial port found")))
    assert flash.main([]) == 1
    assert "No USB serial port found" in capsys.readouterr().err
```

- [ ] **Step 2: Run the new tests to see them fail**

Run: `uv run pytest -q`
Expected: the Task 3 tests pass; the new ones fail with `AttributeError: module 'flash' has no attribute 'find_port'` and similar.

- [ ] **Step 3: Append the I/O, resolution and CLI code to `flash.py`**

```python
# --- I/O --------------------------------------------------------------------

def _comports():
    from serial.tools import list_ports
    return list_ports.comports()


def find_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    usb = [p for p in _comports() if p.vid is not None]
    if not usb:
        raise FlashError("No USB serial port found. Plug the clock in, or pass --port.")
    if len(usb) > 1:
        listing = ", ".join(f"{p.device} ({p.description})" for p in usb)
        raise FlashError(f"More than one USB serial port: {listing}. Pass --port.")
    return usb[0].device


def read_mac(port: str) -> str:
    cmd = [sys.executable, "-m", "esptool", "--port", port, "read-mac"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise FlashError(f"esptool failed on {port}:\n{r.stdout}{r.stderr}")
    return parse_mac_output(r.stdout)


def _now() -> datetime:
    return datetime.now()


# --- resolution -------------------------------------------------------------

def resolve_device(
    mac: str,
    now: datetime,
    registry_path: Path,
    secrets_path: Path,
    device_dir: Path,
) -> tuple[Device, bool]:
    """Return the Device for this MAC, registering it first if it is new."""
    mac = normalize_mac(mac)
    devices = load_registry(registry_path)
    device = find_device(devices, mac)
    is_new = device is None

    if device is None:
        name, friendly_name = mint_name(now)
        if any(d.name == name for d in devices):
            raise FlashError(
                f"A clock named {name} was registered less than a minute ago. "
                "Wait for the next minute and run again."
            )
        api_key_name, ota_password_name = secret_names(name)
        append_secrets(
            secrets_path,
            {api_key_name: generate_api_key(), ota_password_name: generate_ota_password()},
            comment=name,
        )
        device = Device(mac, name, friendly_name, now.isoformat(timespec="seconds"))
        devices.append(device)
        save_registry(registry_path, devices)
    else:
        missing = missing_secrets(secrets_path, list(secret_names(device.name)))
        if missing:
            raise FlashError(
                f"{device.name} is registered but {secrets_path.name} is missing "
                f"{', '.join(missing)}. Secrets are never regenerated for a known clock, "
                "because Home Assistant already has the old ones. Restore them by hand."
            )

    device_file = device_dir / f"{device.name}.yaml"
    if not device_file.exists():
        device_file.write_text(render_device_yaml(device.name, device.friendly_name))
    return device, is_new


# --- CLI --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Flash an HU-058 clock, minting or reusing its identity by MAC address.",
        epilog="Any other arguments are passed through to `esphome run`, e.g. --no-logs.",
    )
    parser.add_argument("--port", help="serial port, e.g. COM4. Default: the only USB serial port present.")
    parser.add_argument("--register-only", action="store_true", help="register the clock and write its files, but do not flash.")
    args, extra = parser.parse_known_args(argv)

    try:
        port = find_port(args.port)
        mac = read_mac(port)
        device, is_new = resolve_device(mac, _now(), REGISTRY_PATH, SECRETS_PATH, HERE)
    except FlashError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    api_key_name, _ = secret_names(device.name)
    if is_new:
        print(f"New clock on {port} with MAC {mac}: registered as {device.name} ({device.friendly_name}).")
        print(f"When Home Assistant asks for an encryption key, use {api_key_name} from secrets.yaml.")
    else:
        print(f"Known clock on {port} with MAC {mac}: {device.name} ({device.friendly_name}).")

    if args.register_only:
        return 0

    cmd = [sys.executable, "-m", "esphome", "run", f"{device.name}.yaml", "--device", port, *extra]
    return subprocess.call(cmd, cwd=HERE)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest -q`
Expected: all pass, 0 failures.

- [ ] **Step 5: Check the generated device file validates in ESPHome**

```bash
uv run python -c "
import flash, pathlib
pathlib.Path('_probe.yaml').write_text(flash.render_device_yaml('clock-probe', 'Clock Probe'))
flash.append_secrets(pathlib.Path('secrets.yaml'), {'api_key_clock_probe': flash.generate_api_key(), 'ota_password_clock_probe': flash.generate_ota_password()}, comment='probe, delete me')
"
```
```bash
uv run esphome config _probe.yaml > /dev/null
```
Expected: exit 0.

Then clean up: delete `_probe.yaml` and remove the last four lines (`# probe, delete me` block) from `secrets.yaml`:
```bash
rm _probe.yaml
```
```bash
uv run python -c "
import re, pathlib
p = pathlib.Path('secrets.yaml'); t = p.read_text()
t = re.sub(r'\n# probe, delete me\napi_key_clock_probe: .*\nota_password_clock_probe: .*\n', '\n', t)
p.write_text(t)
"
```
Run: `tail -3 secrets.yaml` and confirm the probe lines are gone.

- [ ] **Step 6: Commit**

```bash
git add flash.py tests/test_flash.py
git commit -m "Add flash.py port discovery, MAC lookup and esphome run

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: README

**Files:**
- Modify: `README.md` (sections "Start here" steps 1-2, "Setup", "Build and flash", "Adopt in Home Assistant")

**Interfaces:** none.

- [ ] **Step 1: Update "Start here"**

Replace steps 1 and 2 of the numbered list:

```markdown
1. `uv sync`, then copy `secrets.yaml.example` to `secrets.yaml` and fill in the WiFi entries.
2. `uv run flash.py` with the ESP32 on USB, from this directory.
```

- [ ] **Step 2: Replace the "Setup" section body**

Everything between `## Setup` and `## Build and flash` becomes:

~~~markdown
```
uv sync
cp secrets.yaml.example secrets.yaml
```

On Windows that is `copy` instead of `cp`. `uv sync` installs ESPHome,
esptool and the flashing tool's dependencies into `.venv/`; every command
below is run through `uv run` so nothing needs activating.

Fill in the WiFi credentials and the timezone. Leave the per-device entries
alone, `flash.py` writes those.

The timezone in `secrets.yaml` is a fallback for a boot with no Home Assistant.
Change it to yours.

Home Assistant pushes its own timezone on every time sync and that path wins,
but only while the `homeassistant` time platform has no timezone of its own.
Do not add one there.
~~~

- [ ] **Step 3: Replace the "Build and flash" section body**

Everything between `## Build and flash` and `## Adopt in Home Assistant` becomes:

~~~markdown
```
uv run flash.py
```

Plug in one clock and run that. It reads the ESP32's factory MAC address over
USB and looks it up in `devices.yaml`:

- **A clock it has never seen** gets a name from the current time,
  `clock-YYYYMMDD-HHMM`, a fresh API key and OTA password appended to
  `secrets.yaml`, an entry in `devices.yaml`, and a `<name>.yaml` device file
  next to this README. Then it flashes.
- **A clock it has seen** gets exactly the identity it had, so reflashing
  never disturbs its pairing with Home Assistant.

That is how several clocks live side by side: each one is a separate ESPHome
node with its own secrets, all built from `clock-base.yaml`. The first clock
ever built, `wifi-clock`, predates the registry and was entered by hand.

`--port COM7` picks the serial port when more than one USB adapter is
plugged in. `--register-only` writes the files without flashing. Anything
else on the command line goes straight to `esphome run`, so
`uv run flash.py --no-logs` skips the log tail after upload.

USB flashing on a WROOM-32 devkit may need to hold down BOOT while trying to
program. Auto-reset into the bootloader does not work on every board.

Once it is on the network, updates go over the air, and the API carries the
log stream. Use the clock's own device file:

```
uv run esphome run clock-20260928-1407.yaml --device clock-20260928-1407.local
uv run esphome logs clock-20260928-1407.yaml --device clock-20260928-1407.local
```

Two things to know before pushing an OTA build:

- **A bad build is not recoverable remotely.** If it fails to bring up WiFi
  the only way back is a USB cable, which means opening the case. Compile
  before uploading. The fallback access point in `clock-base.yaml` is the
  safety net, so look for its setup SSID before assuming a flash is dead.
- **Do not open the serial port for about a minute after an OTA.** Opening it
  asserts DTR, which resets the board before ESPHome marks the new partition
  valid, and the device rolls back to the previous image with no error
  anywhere.
~~~

- [ ] **Step 4: Update the "Adopt in Home Assistant" key sentence**

Replace:

```markdown
It asks for an encryption key. That is `api_key` out of your `secrets.yaml`.
```

with:

```markdown
It asks for an encryption key. That is the `api_key_<name>` entry for this
clock in your `secrets.yaml`; `flash.py` prints the exact entry name when it
registers a new clock. Every clock has its own, so pick the one that matches
the node Home Assistant discovered.

The clock's actions appear in Home Assistant as `esphome.<name_>_<action>`,
with the hyphens in the name turned into underscores, so a clock called
`clock-20260928-1407` exposes `esphome.clock_20260928_1407_show_number`.
```

- [ ] **Step 5: Check no stale references remain**

Run: `grep -n "clock.yaml" README.md`
Expected: only matches on `clock-base.yaml` or `<name>.yaml` style text. Fix any bare `clock.yaml` reference by pointing it at `clock-base.yaml` (the line near "board: in clock.yaml" in the porting section refers to the ESP32 board setting, which now lives in `clock-base.yaml`).

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "Document multi-device flashing with flash.py

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: End to end on the real clock

**Files:** none new. `flash.py` will create `clock-<stamp>.yaml`, append to `devices.yaml` and `secrets.yaml`.

**Interfaces:** none.

The second clock is on COM4 (FTDI adapter). The user is present for this task.

- [ ] **Step 1: Register without flashing**

Run: `uv run flash.py --register-only`
Expected output shape:
```
New clock on COM4 with MAC xx:xx:xx:xx:xx:xx: registered as clock-2026MMDD-HHMM (Clock 2026-MM-DD HH:MM).
When Home Assistant asks for an encryption key, use api_key_clock_2026MMDD_HHMM from secrets.yaml.
```
If it reports `Known clock ... wifi-clock`, stop: the board on USB is the first clock, not the second.

- [ ] **Step 2: Check what it wrote**

Run: `git status --short`
Expected: `devices.yaml` modified, `clock-<stamp>.yaml` untracked. `secrets.yaml` does not appear (gitignored).

Run: `uv run esphome config clock-<stamp>.yaml > /dev/null`
Expected: exit 0.

- [ ] **Step 3: Flash**

Run: `uv run flash.py --no-logs`
Expected: `Known clock on COM4 ...` line, then ESPHome compiles and uploads. Compile takes several minutes the first time. Ends with `INFO Successfully uploaded program.` If upload fails to enter the bootloader, hold BOOT on the board and rerun.

- [ ] **Step 4: Confirm the first clock is untouched**

Run: `uv run esphome config wifi-clock.yaml > /dev/null`
Expected: exit 0. Nothing was flashed to `wifi-clock`; this only confirms its config still validates after the registry changes.

- [ ] **Step 5: Commit the new device**

```bash
git add devices.yaml clock-<stamp>.yaml
git commit -m "Register second clock

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 6: Hand off to the user**

Tell the user: the new node `clock-<stamp>` should appear under Settings > Devices and Services in Home Assistant within a minute of joining WiFi; the encryption key is `api_key_clock_<stamp_>` in `secrets.yaml`.
