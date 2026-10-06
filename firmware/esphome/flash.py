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
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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

RESULTS = ("registered", "flashed", "build-failed", "flash-failed")


@dataclass
class Device:
    mac: str
    name: str
    friendly_name: str
    first_flashed: str
    # How the most recent run against this clock ended, and when. None on an
    # entry written before these fields existed; they fill in on the next run.
    last_attempt: str | None = None
    last_result: str | None = None


def load_registry_text(text: str, label: str) -> list[Device]:
    """Parse registry text. label names the source in any error message.

    Split out of load_registry() for the copy inside the safety archive, which
    arrives as bytes out of a zip and has no file to read. It used to be
    written into a tempfile.TemporaryDirectory() just to get a path - the one
    thing in that feature that wrote outside both the repository and
    backup_dir() - and parsing text directly removes both the round trip and
    the second copy of the extraction code.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise FlashError(f"{label} is not valid YAML: {e}")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise FlashError(f"{label} should be a YAML mapping at the top level")
    devices = raw.get("devices")
    if devices is not None and not isinstance(devices, list):
        raise FlashError(f"{label}: 'devices' should be a list")
    out = []
    for d in devices or []:
        result = d.get("last_result")
        if result is not None and str(result) not in RESULTS:
            raise FlashError(
                f"{label}: last_result {result!r} for {d.get('name')} is not one of "
                f"{', '.join(RESULTS)}"
            )
        out.append(
            Device(
                mac=normalize_mac(str(d["mac"])),
                name=str(d["name"]),
                friendly_name=str(d["friendly_name"]),
                first_flashed=str(d["first_flashed"]),
                last_attempt=None if d.get("last_attempt") is None else str(d["last_attempt"]),
                last_result=None if result is None else str(result),
            )
        )
    return out


def load_registry(path: Path) -> list[Device]:
    if not path.exists():
        return []
    return load_registry_text(path.read_text(), path.name)


def save_registry(path: Path, devices: list[Device]) -> None:
    header = (
        "# Registry of every clock flashed from this directory, keyed by the ESP32\n"
        "# factory MAC address. flash.py appends to this and rewrites it, so keep\n"
        "# comments out of the entries themselves. MACs are quoted on purpose: an\n"
        "# all-digit MAC would otherwise parse as a YAML 1.1 sexagesimal integer.\n"
    )
    body = yaml.safe_dump(
        {"devices": [{k: v for k, v in asdict(d).items() if v is not None} for d in devices]},
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


def record_result(registry_path: Path, mac: str, now: datetime, result: str) -> Device:
    """Note how this run ended against the clock's registry entry.

    Overwrites the previous outcome: the registry answers "what happened last
    time", not "what has ever happened". The entry has to exist already;
    resolve_device() is the only thing that creates one.
    """
    if result not in RESULTS:
        raise ValueError(f"unknown result {result!r}; expected one of {RESULTS}")
    devices = load_registry(registry_path)
    device = find_device(devices, mac)
    if device is None:
        raise FlashError(f"{normalize_mac(mac)} is not in {registry_path.name}; nothing to record against.")
    device.last_attempt = now.isoformat(timespec="seconds")
    device.last_result = result
    save_registry(registry_path, devices)
    return device


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


# --- build cache ------------------------------------------------------------

def find_ccache_dir() -> Path | None:
    """The directory holding the ccache that ESPHome's ESP-IDF install shipped.

    ESPHome puts ccache on the build PATH (step 5 of
    esphome.espidf.framework.get_framework_env) but decides whether to enable it
    at step 6, with shutil.which() against this process's PATH. A ccache it
    installed itself is invisible to its own probe, so every build gets
    IDF_CCACHE_ENABLE=0 and recompiles the framework from scratch. Putting the
    directory on our PATH lets the probe find it, which is what switches on
    CCACHE_BASEDIR and lets two clocks with identical source share objects.

    ESPHome is asked where its tools live rather than that path being rebuilt
    here, so an ESPHOME_ESP_IDF_PREFIX override cannot send the build one way
    and this lookup another. It is the same call the build makes, which strips
    whitespace, expands ~, resolves symlinks, and reads a blank override as
    unset.
    """
    from esphome.build_helpers.tools_cache import IDF_TOOLS_CACHE, tools_cache_path

    tools = tools_cache_path(*IDF_TOOLS_CACHE) / "tools" / "ccache"
    found = list(tools.glob("*/*/ccache.exe")) + list(tools.glob("*/*/ccache"))
    # Newest by mtime, not by name: the directories are version numbers, and
    # sorting those as strings puts 4.9 after 4.12.1.
    return max(found, key=lambda f: f.stat().st_mtime).parent if found else None


def build_env() -> dict[str, str]:
    """os.environ with ccache reachable, for the esphome subprocess."""
    env = dict(os.environ)
    ccache_dir = find_ccache_dir()
    if ccache_dir is None:
        return env
    env["PATH"] = f"{ccache_dir}{os.pathsep}{env.get('PATH', '')}"
    # The 5 GiB default evicts by LRU, and one ESP-IDF object tree is big enough
    # that a quiet eviction would put the full rebuild back. This is a
    # per-invocation value, not stored config: a build started outside flash.py
    # still gets the default.
    env.setdefault("CCACHE_MAXSIZE", "20G")
    return env


# --- safety backup ----------------------------------------------------------

ARCHIVE_NAME = "HU-058_clock_safety_backup_of_local_data.zip"
BACKUP_MEMBERS = ("devices.yaml", "secrets.yaml")


def backup_dir() -> Path:
    """The per-workstation directory holding the safety archive.

    Outside the repository on purpose. Both files it protects are gitignored,
    and git removes an untracked ignored file without a word in three
    situations: a pull carrying the commit that untracked it, a checkout of any
    commit from before that (which overwrites it, then deletes it again on the
    way back), and `git clean -xdf`. A copy kept in the working tree would go
    with the original.
    """
    import platformdirs

    return Path(platformdirs.user_data_dir("HU-058_ESPHome", appauthor=False))


def backup_path() -> Path:
    return backup_dir() / ARCHIVE_NAME


def _archive_written(path: Path) -> str | None:
    """The archive's own last-write date, for an informational aside only.

    Taken from stat() rather than the README's own `Written:` line: simpler,
    always available even on an archive whose zip structure will not open at
    all (stat() asks the filesystem, not the zip), and it is what os.replace()
    actually preserves across a rewrite - the README's stamp lives inside the
    member data and would need the zip opened to read, which is one more way
    for this to fail right where it must not. The two can disagree (the
    README's is UTC, this is local, and a hand-copied archive carries its
    copy time instead of its write time); that is acceptable for a date meant
    to prompt "go look at this", not to settle anything on its own.

    None on any failure. The caller folds that into "no date to show" and
    never into a reason to refuse.
    """
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    except Exception:
        return None


def _local_data_paths() -> dict[str, Path]:
    """Archive member name -> where that file lives in this checkout.

    The member names are fixed so an archive written by one clone restores
    into another; the paths come from the module constants so the tests can
    redirect them.
    """
    return {"devices.yaml": REGISTRY_PATH, "secrets.yaml": SECRETS_PATH}


def backup_readme(files: list[str], clocks: int | None) -> str:
    """The README.md written into the archive, for whoever finds it later."""
    import platform

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    held = "\n".join(f"- `{f}`" for f in files)
    count = ""
    if clocks is not None:
        count = (
            f"The registry in this archive holds {clocks} "
            f"{'clock' if clocks == 1 else 'clocks'}.\n\n"
        )
    return (
        "# HU-058 clock safety backup\n"
        "\n"
        "`flash.py` wrote this archive the last time it put firmware on a clock.\n"
        "It is a copy of the only two files in the HU-058 project that cannot be\n"
        "recreated from the repository.\n"
        "\n"
        f"- Written: {stamp}\n"
        f"- Workstation: {platform.node()}\n"
        f"- Repository: {HERE}\n"
        f"- Archive: {backup_path()}\n"
        "\n"
        "## What is in here\n"
        "\n"
        f"{held}\n"
        "\n"
        f"{count}"
        "`devices.yaml` maps each clock's ESP32 factory MAC address to the name,\n"
        "friendly name and first-flashed date it was given. It is what stops\n"
        "`flash.py` minting a second identity for a clock Home Assistant has\n"
        "already paired.\n"
        "\n"
        "`secrets.yaml` holds the WiFi credentials and, for every clock, its API\n"
        "encryption key and OTA password. Home Assistant already has the old keys,\n"
        "so these cannot be regenerated: a clock whose keys are lost has to be\n"
        "removed from Home Assistant and added again by hand.\n"
        "\n"
        "## Getting them back\n"
        "\n"
        "The next `uv run flash.py` restores either file automatically if it has\n"
        "gone missing, before it reads the registry. Nothing to do.\n"
        "\n"
        "By hand: unzip these files into `firmware/esphome/` in the repository. A\n"
        "file that still exists is never overwritten automatically, so move the\n"
        "current one aside first if you mean to replace it.\n"
        "\n"
        f"Any `{ARCHIVE_NAME}.unreadable-*.zip` beside this one is an\n"
        "older archive `flash.py` could not open, set aside rather than deleted in\n"
        "case anything can still be unzipped out of it by hand. The live archive is\n"
        f"always `{ARCHIVE_NAME}` exactly; delete a set-aside copy once\n"
        "you are done with it.\n"
        "\n"
        "## Why this lives outside the repository\n"
        "\n"
        "Both files are gitignored, and git treats an untracked ignored file as\n"
        "expendable. It deletes one on a `git pull` carrying the commit that\n"
        "untracked it, silently overwrites one on a `git checkout` of any commit\n"
        "from before that and deletes it again on the way back, and removes one\n"
        "with `git clean -xdf`. A backup kept in the working tree would go in the\n"
        "same command as the original.\n"
        "\n"
        "## Keep it to yourself\n"
        "\n"
        "`secrets.yaml` holds live credentials and this archive is not encrypted.\n"
        "It is fine where it is, under your own user profile. Do not put it in a\n"
        "cloud-synced folder, attach it to an issue, or paste it into a chat.\n"
    )


def _archive_state(path: Path) -> tuple[set[str], list[str] | None]:
    """What an existing archive holds: its data members, and its clock names.

    One open for both answers, because they come from the same file and two
    opens could see two different ones.

    The names rather than the count. len() is the count, so nothing is lost,
    and the staleness guard needs the names: a rollback that substitutes one
    clock for another keeps the count identical, and a guard that can only see
    a count cannot see that at all.

    The names are None when the archive is there but the question could not be
    answered: a damaged member, a central directory that will not parse, a
    registry that will not load. That is a different fact from [], which means
    a readable archive whose registry genuinely holds no clocks, and the
    caller has to tell them apart - an unreadable archive is the one thing
    that must never be overwritten in place, and reporting it as empty disarms
    exactly the guards that would have saved it.

    The member set is kept even when the names fail, and it is also what tells
    the two unreadable states apart: a non-empty set means the central
    directory parsed and a member did not, which still gets the more specific
    "it still holds X" warning out of the loss guard and is never set aside.
    An empty set with None means the archive would not open at all.
    """
    if not path.exists():
        return set(), []
    members: set[str] = set()
    try:
        with zipfile.ZipFile(path) as z:
            members = {n for n in z.namelist() if n in BACKUP_MEMBERS}
            if "devices.yaml" not in members:
                return members, []
            raw = z.read("devices.yaml")
    except Exception:
        # Deliberately broad, like the probe in _backup_candidates below: a
        # bad CRC, a truncated member and a lie in the central directory all
        # mean the same thing here, and letting one escape would crash the
        # flash.
        return members, None
    try:
        # Through the same parser the working tree's copy goes through, so one
        # place decides what a registry is and what counts as a clock in it.
        return members, [d.name for d in load_registry_text(raw.decode(), "devices.yaml")]
    except Exception:
        return members, None


def _backup_candidates(paths: dict[str, Path]) -> list[tuple[str, Path]]:
    """The data files worth archiving: present, and readable.

    A file that will not parse is skipped rather than copied, so a truncated
    registry cannot overwrite the last good copy of itself.
    """
    out: list[tuple[str, Path]] = []
    for name, path in paths.items():
        if not path.exists():
            continue
        try:
            if name == "devices.yaml":
                load_registry(path)
            else:
                yaml.safe_load(path.read_text())
        except Exception as e:
            # Deliberately broad. The probe's only question is whether this
            # file is usable, and load_registry() raises KeyError on an entry
            # truncated mid-write and ValueError on a mangled MAC, neither of
            # which is a YAMLError. Letting one escape would crash the flash.
            print(f"warning: not backing up {name}: {e}", file=sys.stderr)
            continue
        out.append((name, path))
    return out


def _stale_dropped_clocks(archived: list[str], registry: Path, secrets: Path) -> list[str]:
    """Clocks the archive holds, this registry does not, and secrets still key.

    The discriminator between a hand edit and a substitution. Dropping a
    scrapped board's entry by hand takes its api_key_* with it. A `git
    checkout` of any commit from before devices.yaml was untracked rolls the
    registry back and leaves secrets.yaml - which git never tracked, so
    nothing touches it - holding the keys of every clock the rollback dropped.
    A dropped clock whose API key is still here is the signature of the
    substitution, and that is what this fires on: an api_key_* knowingly
    orphaned by hand looks exactly like a rollback from here and keeps the
    archive on every run until those lines go too. The warning says so.

    The archived names are passed in from _archive_state()'s single open, not
    read out of the zip again: a second open can see a different file, and the
    old second read defaulted to "no names" on failure, which failed in the
    unsafe direction by allowing the write.

    Asked on any dropped name, not only on a count that shrank. A rollback
    that drops one clock and brings back another leaves the count alone, and
    that is the quietest version of this loss, not the harmless one.

    secret_names() rather than a second spelling of the convention, so the
    name-to-secret-key mapping has one source of truth.

    Empty on any failure. A secrets.yaml that is missing or will not parse is
    a witness that cannot testify either way, and no reason on its own to
    refuse the backup; backup_local_data() must never raise.
    """
    try:
        dropped = set(archived) - {d.name for d in load_registry(registry)}
        if not dropped:
            return []
        present = _secret_keys(secrets)
    except Exception:
        return []
    stale = []
    for name in sorted(dropped):
        api_key_name, _ = secret_names(name)
        if api_key_name in present:
            stale.append(name)
    return stale


def _set_aside_dead_archive(target: Path) -> Path:
    """Move an archive that will not open at all out of the way, and say where.

    The owner's ruling, for the one unreadable state where it costs nothing:
    refusing forever left the safety backup disabled until somebody read
    stderr, and a zip whose central directory will not parse still holds every
    member's bytes for manual recovery. Those bytes are kept under a name that
    says what they are, and the fresh backup is written as planned.

    Renamed, never copied. os.replace() moves the bytes atomically, so at
    every instant exactly one file in backup_dir() holds them: a copy could
    exhaust the disk and leave a truncated duplicate for the write to then
    make the only copy, and it would double the on-disk footprint of a file
    full of credentials.

    A mkstemp-unique name, not a timestamp. A fixed name clobbers the previous
    set-aside, and second-resolution stamps collide between concurrent runs -
    this project already has a documented same-minute collision in
    resolve_device(). The cost is one stray empty file per hard kill, which is
    left alone on purpose: a sweep cannot tell a dead temporary from a
    concurrent run's live one.
    """
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=ARCHIVE_NAME + ".unreadable-", suffix=".zip")
    os.close(fd)
    aside = Path(name)
    try:
        os.replace(target, aside)
    except OSError:
        # mkstemp's empty file is dead weight the moment the replace fails,
        # and a stray *.unreadable-*.zip that is really empty invites exactly
        # the "unzip what you can from it" the README promises, and finds
        # nothing - evidence the file lost bytes it never held.
        try:
            aside.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # After the replace, not before: the rename gives this path the dead
    # file's inode and therefore its mode, not mkstemp's. Usually already
    # 0600, but an archive mangled by a third party can carry any mode, and
    # this one may hold credentials. Best effort, like the archive's own.
    try:
        os.chmod(aside, 0o600)
    except OSError:
        pass
    return aside


def backup_local_data() -> None:
    """Copy devices.yaml and secrets.yaml into the safety archive.

    Never raises. Firmware getting onto the board matters more than the copy,
    so every failure warns and returns.
    """
    tmp = None
    aside = None
    try:
        target = backup_path()
        existing, archived_names = _archive_state(target)
        # Only for the four refusals below where the zip itself opened, so the
        # date really is when this archive was last written rather than when
        # something unreadable happened to it. Computed once regardless of
        # which path is taken - it is a single stat() call, cheap even on the
        # paths that never use it.
        target_desc = (
            f"{target} (written {written})"
            if (written := _archive_written(target)) is not None
            else str(target)
        )
        paths = _local_data_paths()
        candidates = _backup_candidates(paths)
        names = {n for n, _ in candidates}
        # Any member the archive holds and this run does not is a loss. A
        # proper-subset test is not enough: {secrets.yaml} against an archived
        # {devices.yaml} is incomparable, passes, and destroys the only copy of
        # the registry.
        if lost := existing - names:
            print(
                f"warning: keeping the existing safety backup {target_desc}: it still "
                f"holds {', '.join(sorted(lost))}, which is missing or unreadable here.",
                file=sys.stderr,
            )
            return
        if not candidates:
            return
        # The registry candidate and its clock count bound together, because
        # every guard below reads them as a pair: clocks is not None exactly
        # when registry is not None. They used to be assigned inside the
        # candidates loop, which was correct only by implication.
        registry = next((p for n, p in candidates if n == "devices.yaml"), None)
        clocks = len(load_registry(registry)) if registry is not None else None
        # An archive that cannot be read is the one copy that might still hold
        # keys Home Assistant has and nothing else does, so nothing is ever
        # overwritten in place here. What happens instead depends on which of
        # the two unreadable states it is.
        if archived_names is None:
            if existing:
                # The central directory parsed and a member did not, so the
                # member set is known - and it is what closes the loss. An
                # archive holding {devices.yaml, secrets.yaml} with a damaged
                # registry, in a run whose secrets.yaml is missing, is refused
                # by the loss guard above. Setting it aside instead would turn
                # that closed loss into an open one: a one-member fresh archive
                # would be written and the only copy of the keys would live in
                # a quarantined file the tool cannot read.
                print(
                    f"warning: keeping the existing safety backup {target_desc}: it cannot "
                    "be read, so there is no telling what overwriting it would lose. Unzip "
                    "what you can from it, then delete it and the next run will write a "
                    "fresh one.",
                    file=sys.stderr,
                )
                return
            # It would not open at all, so its member set was already unknown
            # and setting it aside costs no guard anything it did not lack.
            # The empty-registry guard first, though: after the set-aside there
            # is nothing left to compare against, and a registry truncated to
            # nothing - or missing entirely - would become the fresh archive.
            if not clocks:
                print(
                    f"warning: keeping the existing safety backup {target}: it cannot be "
                    "read, and there are no clocks here to put in a fresh one. Unzip what "
                    "you can from it, then delete it and the next run will write a fresh "
                    "one.",
                    file=sys.stderr,
                )
                return
            try:
                aside = _set_aside_dead_archive(target)
            except Exception as e:
                # Refuse the write. Falling through to it after a failed
                # set-aside is the original data-loss bug with extra steps.
                print(
                    f"warning: keeping the existing safety backup {target}: it cannot be "
                    f"read, and moving it aside to make room for a fresh one failed: {e}. "
                    "Unzip what you can from it, then delete it and the next run will "
                    "write a fresh one.",
                    file=sys.stderr,
                )
                return
            print(
                f"warning: the safety backup {target} cannot be read, so its bytes have "
                f"been set aside as {aside} and a fresh backup written in their place. "
                "Unzip what you can from the set-aside file, then delete it.",
                file=sys.stderr,
            )
            # Nothing left to compare against, and nothing left to lose: every
            # guard below is a comparison with the archive that just moved.
            archived_names = []
        archived = len(archived_names)
        # A zero-byte devices.yaml is valid YAML and loads as no clocks at all,
        # so truncation to nothing clears every check above with its member set
        # intact.
        if clocks == 0 and archived:
            print(
                f"warning: keeping the existing safety backup {target_desc}: devices.yaml "
                f"has no clocks and the backup holds {archived}.",
                file=sys.stderr,
            )
            return
        # Top level, on any clock the archive holds and this registry does not.
        # It used to be nested inside the shrink warning below, which asked it
        # the wrong question: a rollback that drops one clock and brings
        # another back keeps the count identical, never reached the guard, and
        # wrote the archive with no warning at all - more quietly than the
        # shrink that was guarded. Found by the whole-branch review.
        if registry is not None:
            if stale := _stale_dropped_clocks(archived_names, registry, paths["secrets.yaml"]):
                print(
                    f"warning: keeping the existing safety backup {target_desc}: its "
                    f"registry holds {', '.join(stale)}, devices.yaml here does not, and "
                    "secrets.yaml still holds their API keys. That reads as a registry "
                    "rolled back rather than edited - a git checkout of a commit from "
                    "before devices.yaml was untracked overwrites it in place - so the "
                    "backup keeps what it has. If you did mean to drop those clocks, take "
                    "their api_key_* and ota_password_* lines out of secrets.yaml too and "
                    "run again.",
                    file=sys.stderr,
                )
                return
        if clocks is not None and archived > clocks:
            # Dropping a scrapped board's entry by hand is legitimate and has to
            # reach the backup. It just says so on the way past.
            print(
                f"warning: devices.yaml is down to {clocks} from {archived} clocks "
                "in the safety backup; backing up the shorter registry.",
                file=sys.stderr,
            )
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir()'s mode applies only to a directory it creates itself, and
        # only to the last one: parents get the default, and a directory that
        # was already there with a looser mode keeps it. Tighten it by hand,
        # best effort - this is where credentials land. Only this directory,
        # never its parents: AppData/Local and ~/.local/share are shared.
        try:
            os.chmod(target.parent, 0o700)
        except OSError:
            pass
        # A unique temporary name, not target.name + ".tmp". The archive path
        # is per-workstation by design, so two runs in two terminals - a
        # --port COM4 beside a --port COM7, or a --register-only beside a
        # flash - would open one fixed name twice, interleave into it, and
        # whichever replace landed last would install a garbled zip.
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
        os.close(fd)
        tmp = Path(tmp_name)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.md", backup_readme([n for n, _ in candidates], clocks))
            for name, path in candidates:
                z.write(path, name)
        os.replace(tmp, target)
        tmp = None
        # Credentials: owner only. Close to a no-op on Windows, where the ACL on
        # the user data directory is what applies. Its own message: the archive
        # is written and correct by now, and saying "could not write the safety
        # backup" about a file that is sitting there would send the user
        # looking for the wrong problem.
        try:
            os.chmod(target, 0o600)
        except OSError as e:
            print(
                f"warning: wrote the safety backup {target} but could not restrict it "
                f"to your account: {e}",
                file=sys.stderr,
            )
    except Exception as e:
        if aside is None:
            print(f"warning: could not write the safety backup: {e}", file=sys.stderr)
        else:
            # backup_path() is simply absent now. A user told only that the
            # write failed would go looking there and find nothing at all, so
            # this message has to name the set-aside too: that file is where
            # their last backup actually is.
            print(
                f"warning: could not write the safety backup: {e}. The unreadable archive "
                f"it was replacing is set aside at {aside}.",
                file=sys.stderr,
            )
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _restore_member(z: zipfile.ZipFile, name: str, path: Path) -> None:
    """Write one archive member to path, or leave nothing behind trying."""
    # Read before there is any temporary file to clean up: a member with a bad
    # CRC then fails without having touched the working tree at all.
    data = z.read(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Through a temporary file, because of restore_local_data's rule that a
    # file which still exists is never overwritten: writing the destination
    # directly leaves a partial file if this is interrupted, and the next run
    # would see a file that exists and refuse to restore over it. A
    # half-written registry would block automatic recovery for good.
    #
    # A unique name, not path.name + ".restoring", for the same reason the
    # archive's temporary file has one: two runs in two terminals share this
    # directory and would otherwise interleave into one file.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".restoring")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        # Only on the way out, never in a finally: a cleanup that failed after
        # a move that landed would report a restore that did happen as one
        # that did not.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def restore_local_data() -> None:
    """Put back any local data file that has gone missing.

    Runs before the registry is read, because a missing registry makes
    flash.py mint a fresh identity for a clock Home Assistant has already
    paired and nothing downstream can tell that happened. Never raises, and
    never overwrites a file that is still there.
    """
    archive = None
    paths = _local_data_paths()
    # Before the guarded block, not inside it: a failure part-way through has
    # to leave the files that did land reported as restored.
    restored: list[str] = []
    try:
        archive = backup_path()
        if not archive.exists():
            return
        with zipfile.ZipFile(archive) as z:
            held = set(z.namelist())
            for name, path in paths.items():
                if path.exists() or name not in held:
                    continue
                try:
                    _restore_member(z, name, path)
                except Exception as e:
                    # Per member, because the members are independent copies of
                    # independent files. One damaged member used to reach the
                    # outer handler and end the restore, and devices.yaml is
                    # attempted first: a bad CRC on the registry hid an intact
                    # secrets.yaml behind it, the flash then minted a new
                    # identity and wrote a one-clock secrets.yaml, and the next
                    # backup replaced the archive that still held every old key.
                    print(
                        f"warning: could not restore {name} from the safety backup "
                        f"{archive}: {e}",
                        file=sys.stderr,
                    )
                    continue
                restored.append(name)
    except Exception as e:
        if archive is None:
            print(f"warning: could not read the safety backup: {e}", file=sys.stderr)
        else:
            print(f"warning: could not read the safety backup {archive}: {e}", file=sys.stderr)
    if not restored:
        return
    # Named here, not folded silently into the per-file messages below: the
    # rollback guard in backup_local_data() can leave the archive unwritten
    # for weeks while new clocks keep getting registered, and a restore that
    # only says what it put back - never how old it is - hides exactly that.
    # A user who sees "written 2026-08-14" today has a reason to go check
    # devices.yaml; one who sees only "restored" does not.
    written = _archive_written(archive)
    written_part = f"written {written}" if written is not None else ""

    def _detail(*parts: str) -> str:
        bits = [p for p in parts if p]
        return f" ({', '.join(bits)})" if bits else ""

    for name in restored:
        if name == "devices.yaml":
            try:
                n = len(load_registry(paths[name]))
                count_part = f"{n} {'clock' if n == 1 else 'clocks'}"
            except Exception:
                count_part = ""
            print(f"Restored devices.yaml from the safety backup{_detail(count_part, written_part)}.")
        else:
            print(f"Restored {name} from the safety backup{_detail(written_part)}.")
    print(f"  {archive}")


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
        # Before the registry is read: a missing one mints a second identity
        # for a clock Home Assistant has already paired.
        restore_local_data()
        port = find_port(args.port)
        mac = read_mac(port)
        device, is_new = resolve_device(mac, _now(), REGISTRY_PATH, SECRETS_PATH, HERE)
        # The registry is final now, so even an upload that fails leaves a copy.
        backup_local_data()
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
    return subprocess.call(cmd, cwd=HERE, env=build_env())


if __name__ == "__main__":
    sys.exit(main())
