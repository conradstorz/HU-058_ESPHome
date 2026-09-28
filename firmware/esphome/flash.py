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
