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
import json
import os
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


# --- preflight --------------------------------------------------------------

def check_shell() -> None:
    """Refuse to build under Git Bash / MSYS on Windows.

    ESP-IDF's cmake and ninja steps do not run in that shell. ESPHome still
    prints "Successfully compiled program", produces no build directory and no
    firmware.factory.bin, and the upload afterwards fails with nothing to
    explain why. flash-clock.py stops for the same reason; this is the check
    for anyone running flash.py directly, agents included.

    Only the build is affected. Registration reads the MAC with esptool and
    writes files, all of which work here, so --register-only does not call
    this.
    """
    if sys.platform == "win32" and os.environ.get("MSYSTEM"):
        raise FlashError(
            "This is a Git Bash / MSYS shell, which builds ESPHome firmware with no\n"
            "error and no output, so the upload then fails for no visible reason.\n"
            "Run this from PowerShell or cmd instead:\n"
            "    uv run flash.py"
        )


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
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise FlashError(f"{path.name} is not valid YAML: {e}")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise FlashError(f"{path.name} should be a YAML mapping at the top level")
    devices = raw.get("devices")
    if devices is not None and not isinstance(devices, list):
        raise FlashError(f"{path.name}: 'devices' should be a list")
    return [
        Device(
            mac=normalize_mac(str(d["mac"])),
            name=str(d["name"]),
            friendly_name=str(d["friendly_name"]),
            first_flashed=str(d["first_flashed"]),
        )
        for d in devices or []
    ]


def save_registry(path: Path, devices: list[Device]) -> None:
    header = (
        "# Registry of every clock flashed from this directory, keyed by the ESP32\n"
        "# factory MAC address. flash.py appends to this and rewrites it, so keep\n"
        "# comments out of the entries themselves. MACs are quoted on purpose: an\n"
        "# all-digit MAC would otherwise parse as a YAML 1.1 sexagesimal integer.\n"
    )
    body = yaml.safe_dump(
        {"devices": [asdict(d) for d in devices]},
        sort_keys=False,
        default_flow_style=False,
    )
    try:
        path.write_text(header + body)
    except OSError as e:
        raise FlashError(f"Could not write {path}: {e}")


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
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise FlashError(f"{path.name} is not valid YAML: {e}")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise FlashError(f"{path.name} should be a YAML mapping at the top level")
    return set(raw)


def append_secrets(path: Path, entries: dict[str, str], comment: str) -> None:
    clash = sorted(set(entries) & _secret_keys(path))
    if clash:
        raise FlashError(
            f"{path.name} already has {', '.join(clash)}; refusing to overwrite. "
            "If a previous run was interrupted, wait for the next minute and run "
            "again, or delete those lines by hand."
        )
    text = path.read_text() if path.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    sep = "\n" if text else ""
    lines = "".join(f"{k}: {json.dumps(v)}\n" for k, v in entries.items())
    try:
        path.write_text(f"{text}{sep}# {comment}\n{lines}")
    except OSError as e:
        raise FlashError(f"Could not write {path}: {e}")


def missing_secrets(path: Path, names: list[str]) -> list[str]:
    present = _secret_keys(path)
    return [n for n in names if n not in present]


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
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32", "--port", port, "read-mac"]
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
        try:
            device_file.write_text(render_device_yaml(device.name, device.friendly_name))
        except OSError as e:
            raise FlashError(f"Could not write {device_file}: {e}")
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
        # Only the compile breaks in that shell; registering is fine there.
        if not args.register_only:
            check_shell()
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
