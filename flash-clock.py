#!/usr/bin/env python3
"""Launcher: flash an HU-058 clock from the repository root.

Does what the firmware README tells you to do by hand, so you do not have to
remember the subdirectory. Checks the shell and the toolchain, makes sure
there is a secrets.yaml, then hands off to firmware/esphome/flash.py, which
owns every decision about the clock's identity.

Stdlib only, so it runs on any machine with Python 3.12 or newer. Everything
after the launcher's own flags goes straight through:

    python flash-clock.py
    python flash-clock.py --port COM7
    python flash-clock.py --no-logs
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESPHOME_DIR = HERE / "firmware" / "esphome"
SECRETS_PATH = ESPHOME_DIR / "secrets.yaml"
EXAMPLE_PATH = ESPHOME_DIR / "secrets.yaml.example"

UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"

# The four shared secrets, in the order secrets.yaml.example lists them:
# key, prompt, default. The per-device api_key_* and ota_password_* entries
# are deliberately absent; flash.py mints those per clock, and writing the
# example's placeholders would hand a registered clock an invalid API key.
SHARED_SECRETS = (
    ("wifi_ssid", "WiFi SSID", ""),
    ("wifi_password", "WiFi password", ""),
    ("ap_password", "Fallback AP password", ""),
    ("timezone", "Timezone", "Etc/UTC"),
)

SECRETS_HEADER = """\
# Written by flash-clock.py. The per-device entries below this block are
# appended by `uv run flash.py` the first time it sees a new clock.
"""

TIMEZONE_NOTE = """\
# Startup value only. It is in charge from every power-on until Home Assistant
# first answers, then the pushed timezone replaces it for good. See
# firmware/esphome/README.md under Setup.
"""


class LauncherError(Exception):
    """Something the user has to fix. main() prints it and exits 1."""


# --- preflight --------------------------------------------------------------

def check_shell() -> None:
    """Refuse to run under Git Bash / MSYS on Windows.

    The firmware README records the failure: that shell compiles with no error
    and produces no build output, so the upload fails afterwards with nothing
    to explain why. Better to stop here than to burn a flash on it.
    """
    if sys.platform == "win32" and os.environ.get("MSYSTEM"):
        raise LauncherError(
            "This is a Git Bash / MSYS shell, which builds ESPHome firmware with no\n"
            "error and no output, so the upload then fails for no visible reason.\n"
            "Run this from PowerShell or cmd instead:\n"
            "    python flash-clock.py"
        )


def find_uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise LauncherError(
            "uv is not on PATH, and it is what builds and flashes the firmware.\n"
            f"Install it from {UV_INSTALL_URL}, then run this again."
        )
    return uv


def check_layout() -> None:
    if not ESPHOME_DIR.is_dir():
        raise LauncherError(
            f"{ESPHOME_DIR} is missing. Run this script from inside a checkout of\n"
            "the repository, not from a copy of the script on its own."
        )


# --- secrets ----------------------------------------------------------------

def render_secrets(values: dict[str, str]) -> str:
    """Render the shared block of a secrets.yaml.

    Values go through json.dumps, the same way flash.py writes the per-device
    entries: it emits a double-quoted YAML scalar, so a password containing a
    quote, a backslash or a '#' survives intact.
    """
    lines = [SECRETS_HEADER, "\n"]
    for key, _prompt, _default in SHARED_SECRETS:
        if key == "timezone":
            lines.append(TIMEZONE_NOTE)
        lines.append(f"{key}: {json.dumps(values[key])}\n")
    return "".join(lines)


def _ask_one(ask, prompt: str, default: str, out=print) -> str:
    label = f"  {prompt} [{default}]: " if default else f"  {prompt}: "
    while True:
        value = ask(label).strip()
        if value:
            return value
        if default:
            return default
        out(f"  {prompt} cannot be empty.")


def prompt_values(ask=input, out=print) -> dict[str, str]:
    """Collect the four shared secrets, then let the user amend any of them."""
    values: dict[str, str] = {}
    out("")
    for key, prompt, default in SHARED_SECRETS:
        values[key] = _ask_one(ask, prompt, default, out)

    while True:
        out("")
        for i, (key, prompt, _default) in enumerate(SHARED_SECRETS, start=1):
            out(f"  {i}. {prompt}: {values[key]}")
        out("")
        # Plain input(), not getpass: this review pass exists so a mistyped
        # WiFi password is caught here rather than after a flash, and the file
        # it writes is plaintext and gitignored either way.
        reply = ask("Write these? [Y] to accept, or a number to change: ").strip()
        if reply == "" or reply.lower() in {"y", "yes"}:
            return values
        if reply.isdigit() and 1 <= int(reply) <= len(SHARED_SECRETS):
            key, prompt, default = SHARED_SECRETS[int(reply) - 1]
            values[key] = _ask_one(ask, prompt, values[key] or default, out)
            continue
        out(f"Not an entry number between 1 and {len(SHARED_SECRETS)}.")


def write_secrets(path: Path, values: dict[str, str]) -> None:
    """Create secrets.yaml, never overwrite it.

    Exclusive create on purpose. An existing secrets.yaml holds the API keys
    and OTA passwords of every clock already paired with Home Assistant, and
    flash.py will not regenerate them, so losing the file means opening every
    case again.
    """
    text = render_secrets(values)
    try:
        f = open(path, "x", encoding="utf-8", newline="\n")
    except FileExistsError:
        raise LauncherError(
            f"{path} appeared after the check and was left alone. Run this again."
        )
    except OSError as e:
        raise LauncherError(f"Could not write {path}: {e}")
    # Exclusive create has already made the file, so a Ctrl-C or a full disk
    # from here on would leave half a secrets.yaml behind and the next run
    # would refuse to touch it. Clear it up so "nothing written" stays true.
    try:
        with f:
            f.write(text)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def ensure_secrets(ask=input, out=print) -> None:
    if SECRETS_PATH.exists():
        return
    if not sys.stdin.isatty():
        raise LauncherError(
            f"{SECRETS_PATH} does not exist, and there is no terminal to ask on.\n"
            f"Copy {EXAMPLE_PATH.name} to secrets.yaml, fill in the WiFi entries, "
            "and run this again."
        )
    out(f"No secrets.yaml in {ESPHOME_DIR}. The firmware needs one to build.")
    reply = ask("Create a provisional one now? [y/N] ").strip().lower()
    if reply not in {"y", "yes"}:
        raise LauncherError(
            f"Nothing written. Copy {EXAMPLE_PATH.name} to secrets.yaml, fill in the "
            "WiFi entries, and run this again."
        )
    write_secrets(SECRETS_PATH, prompt_values(ask=ask, out=out))
    out(f"\nWrote {SECRETS_PATH}. It is gitignored; edit it by hand any time.")


# --- handoff ----------------------------------------------------------------

def run(uv: str, args: list[str], sync: bool = True) -> int:
    # Flush before every handoff. Our stdout is block buffered whenever it is
    # not a terminal, so without this the launcher's own lines arrive after
    # the child's and read as if they came from it.
    if sync:
        sys.stdout.flush()
        rc = subprocess.call([uv, "sync"], cwd=ESPHOME_DIR)
        if rc != 0:
            print("error: `uv sync` failed, so the firmware was not flashed.", file=sys.stderr)
            return rc
    sys.stdout.flush()
    return subprocess.call([uv, "run", "flash.py", *args], cwd=ESPHOME_DIR)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    wants_help = bool(args) and args[0] in {"-h", "--help"}

    try:
        check_shell()
        check_layout()
        uv = find_uv()
        if wants_help:
            print(__doc__.strip())
            print("\nAnd flash.py's own options:\n")
            # No sync and no secrets check: asking what the flags are should
            # not build a virtualenv or write a file.
            return run(uv, ["--help"], sync=False)
        ensure_secrets()
    except LauncherError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nerror: cancelled, nothing written.", file=sys.stderr)
        return 1

    return run(uv, args)


if __name__ == "__main__":
    sys.exit(main())
