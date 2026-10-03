# Local Data Safety Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every flash copies `devices.yaml` and `secrets.yaml` into one compressed archive outside the repository, and any run that finds either file missing restores it before reading the registry.

**Architecture:** Five new functions in `flash.py`, wired into `main()` either side of `resolve_device()`: restore runs before the registry is read, backup runs once the registry's content is final. The archive is a single rolling zip in the per-user data directory, holding `devices.yaml`, `secrets.yaml` and a generated `README.md`. Neither function ever raises — a backup problem warns and leaves the flash alone.

**Tech Stack:** Python 3.13 stdlib `zipfile` / `os.replace`, `platformdirs` (already a transitive dependency via ESPHome), PyYAML, pytest, uv.

Spec: `docs/superpowers/specs/2026-10-03-local-data-safety-backup-design.md`

## Global Constraints

- All work happens in `firmware/esphome/`. Paths below are relative to it unless they start with `docs/`.
- `ARCHIVE_NAME = "HU-058_clock_safety_backup_of_local_data.zip"` exactly, extension included.
- `BACKUP_MEMBERS = ("devices.yaml", "secrets.yaml")` — archive member names are these literals, whatever `REGISTRY_PATH` and `SECRETS_PATH` are pointed at.
- The archive directory is `platformdirs.user_data_dir("HU-058_ESPHome", appauthor=False)`.
- One rolling copy. No history, no rotation, no encryption.
- `backup_local_data()` and `restore_local_data()` must never raise. Every failure path prints `warning: ...` to stderr and returns.
- An existing local file is never overwritten by a restore.
- Python via `uv` only: `uv sync`, `uv run pytest`, `uv run flash.py`. Never `pip install` or activate a venv.
- Do not chain shell commands with `&&`. One command per tool call.
- Run `uv run pytest` from `firmware/esphome/`. All existing tests must keep passing; the suite is at 82 before this plan starts.
- Commit messages end with `Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)`.
- Windows host. Anything that compiles firmware must run from PowerShell — ESP-IDF's `idf_tools.py` aborts with "MSys/Mingw is not supported" under Git Bash. `uv run pytest` is fine in either shell.

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `flash.py` | Modify | Add a `# --- safety backup ---` section after the secrets section and before `# --- resolution ---`. Add two calls in `main()`. |
| `tests/test_flash.py` | Modify | Add a `# --- the safety backup ---` section after the Git Bash guard tests. |
| `README.md` | Modify | Document the archive under "Build and flash", next to the existing `devices.yaml` backup advice. |

No new modules. `flash.py` is where the registry and secrets helpers already live and is the only place that launches a flash.

---

### Task 1: Archive location

**Files:**
- Modify: `firmware/esphome/flash.py` (imports, and a new section before `# --- resolution ---`)
- Test: `firmware/esphome/tests/test_flash.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ARCHIVE_NAME: str`, `BACKUP_MEMBERS: tuple[str, str]`, `backup_dir() -> Path`, `backup_path() -> Path`, `_local_data_paths() -> dict[str, Path]`. Later tasks monkeypatch `backup_dir` and read `backup_path()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_flash.py`, after `test_check_shell_ignores_msystem_off_windows`:

```python
# --- the safety backup ------------------------------------------------------

# devices.yaml and secrets.yaml are the only two files here that cannot be
# recreated from the repository, and git removes untracked ignored files
# without a word: on a pull carrying the commit that untracked them, on a
# checkout of any commit from before that, and on git clean -xdf. The archive
# lives outside the working tree because that is the only place git cannot
# reach.

def test_backup_path_is_the_named_archive_in_the_backup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path)

    assert flash.backup_path() == tmp_path / flash.ARCHIVE_NAME
    assert flash.ARCHIVE_NAME == "HU-058_clock_safety_backup_of_local_data.zip"


def test_backup_dir_is_outside_the_repository():
    assert flash.backup_dir().name == "HU-058_ESPHome"
    assert flash.HERE not in flash.backup_dir().parents
    assert flash.backup_dir() != flash.HERE


def test_local_data_paths_follow_the_module_constants(tmp_path, monkeypatch):
    monkeypatch.setattr(flash, "REGISTRY_PATH", tmp_path / "devices.yaml")
    monkeypatch.setattr(flash, "SECRETS_PATH", tmp_path / "secrets.yaml")

    assert flash._local_data_paths() == {
        "devices.yaml": tmp_path / "devices.yaml",
        "secrets.yaml": tmp_path / "secrets.yaml",
    }
    assert tuple(flash._local_data_paths()) == flash.BACKUP_MEMBERS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_flash.py -k "backup_path or backup_dir or local_data_paths" -v`
Expected: FAIL with `AttributeError: module 'flash' has no attribute 'ARCHIVE_NAME'`

- [ ] **Step 3: Add the imports**

In `flash.py`, change the import block so it reads:

```python
import argparse
import base64
import json
import os
import re
import secrets as pysecrets
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
```

(`zipfile` is new; `datetime` gains `timezone`.)

- [ ] **Step 4: Write the minimal implementation**

In `flash.py`, immediately before the `# --- resolution ---` banner, add:

```python
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


def _local_data_paths() -> dict[str, Path]:
    """Archive member name -> where that file lives in this checkout.

    The member names are fixed so an archive written by one clone restores
    into another; the paths come from the module constants so the tests can
    redirect them.
    """
    return {"devices.yaml": REGISTRY_PATH, "secrets.yaml": SECRETS_PATH}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_flash.py -v`
Expected: PASS, 85 passed

- [ ] **Step 6: Commit**

```bash
git add firmware/esphome/flash.py firmware/esphome/tests/test_flash.py
git commit -F - <<'MSGEOF'
Name where the safety archive lives

devices.yaml and secrets.yaml are the only files in this directory that
cannot be recreated from the repository, and both are gitignored, which git
reads as expendable: a pull carrying the commit that untracked them deletes
them, a checkout of any earlier commit overwrites them and then deletes them
on the way back, and git clean -xdf removes them outright.

backup_dir() is therefore outside the working tree, in the per-user data
directory, which is the only place git cannot reach. The member names are
fixed literals so an archive written by one clone restores into another.

Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)
MSGEOF
```

---

### Task 2: The generated README

**Files:**
- Modify: `firmware/esphome/flash.py` (the safety backup section)
- Test: `firmware/esphome/tests/test_flash.py`

**Interfaces:**
- Consumes: `backup_path()`, `HERE` from Task 1.
- Produces: `backup_readme(files: list[str], clocks: int | None) -> str`. Task 3 writes its return value into the archive as `README.md`.

- [ ] **Step 1: Write the failing tests**

Add to the safety backup section of `tests/test_flash.py`:

```python
def test_backup_readme_names_the_archive_and_the_clock_count(tmp_path, monkeypatch):
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path)

    body = flash.backup_readme(["devices.yaml", "secrets.yaml"], clocks=3)

    assert "# HU-058 clock safety backup" in body
    assert str(flash.backup_path()) in body
    assert str(flash.HERE) in body
    assert "holds 3 clocks" in body
    assert "`devices.yaml`" in body
    assert "`secrets.yaml`" in body
    assert "uv run flash.py" in body
    assert "git clean -xdf" in body
    assert "not encrypted" in body


def test_backup_readme_says_one_clock_in_the_singular(tmp_path, monkeypatch):
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path)

    assert "holds 1 clock." in flash.backup_readme(["devices.yaml"], clocks=1)


def test_backup_readme_omits_the_count_when_there_is_no_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path)

    body = flash.backup_readme(["secrets.yaml"], clocks=None)

    assert "clocks." not in body
    assert "`secrets.yaml`" in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_flash.py -k backup_readme -v`
Expected: FAIL with `AttributeError: module 'flash' has no attribute 'backup_readme'`

- [ ] **Step 3: Write the minimal implementation**

Append to the safety backup section in `flash.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_flash.py -v`
Expected: PASS, 88 passed

- [ ] **Step 5: Commit**

```bash
git add firmware/esphome/flash.py firmware/esphome/tests/test_flash.py
git commit -F - <<'MSGEOF'
Explain the archive to whoever finds it

A zip in the user data directory with two YAML files in it tells a reader
nothing. The generated README.md names the workstation and repository it came
from, says what each file does and why it cannot be regenerated, gives both
the automatic and the by-hand way to restore it, and says plainly that the
archive is unencrypted and holds live credentials.

It is generated per backup rather than static so the provenance lines are
true of the archive actually on disk.

Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)
MSGEOF
```

---

### Task 3: Writing the archive

**Files:**
- Modify: `firmware/esphome/flash.py` (the safety backup section)
- Test: `firmware/esphome/tests/test_flash.py`

**Interfaces:**
- Consumes: `backup_path()`, `_local_data_paths()`, `BACKUP_MEMBERS`, `backup_readme()`, `load_registry()`, `FlashError`.
- Produces: `backup_local_data() -> None`, plus the helpers `_archive_members(path: Path) -> set[str]` and `_backup_candidates(paths: dict[str, Path]) -> list[tuple[str, Path]]`. Task 5 calls `backup_local_data()` from `main()`.

- [ ] **Step 1: Write the failing tests**

Add to the safety backup section of `tests/test_flash.py`:

```python
def _local_data(tmp_path, monkeypatch, registry=True, secrets=True):
    """Point the module constants at tmp_path and write valid files there."""
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "backup")
    reg = tmp_path / "devices.yaml"
    sec = tmp_path / "secrets.yaml"
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    if registry:
        flash.save_registry(reg, [flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03")])
    if secrets:
        sec.write_text("wifi_ssid: net\napi_key_clock_x: key\n")
    return reg, sec


def test_backup_writes_both_files_and_a_readme(tmp_path, monkeypatch):
    _local_data(tmp_path, monkeypatch)

    flash.backup_local_data()

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "devices.yaml", "secrets.yaml"]
        assert "clock-x" in z.read("devices.yaml").decode()
        assert "api_key_clock_x" in z.read("secrets.yaml").decode()
        assert "holds 1 clock." in z.read("README.md").decode()


def test_backup_skips_a_registry_that_does_not_parse(tmp_path, monkeypatch, capsys):
    reg, _ = _local_data(tmp_path, monkeypatch)
    reg.write_text("devices: [oh: no\n")

    flash.backup_local_data()

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "secrets.yaml"]
    assert "devices.yaml" in capsys.readouterr().err


def test_backup_skips_secrets_that_do_not_parse(tmp_path, monkeypatch, capsys):
    _, sec = _local_data(tmp_path, monkeypatch)
    sec.write_text("key: [unclosed\n")

    flash.backup_local_data()

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "devices.yaml"]
    assert "secrets.yaml" in capsys.readouterr().err


def test_backup_keeps_the_old_archive_rather_than_shrink_it(tmp_path, monkeypatch, capsys):
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    before = flash.backup_path().read_bytes()

    reg.unlink()  # what a git pull did on 2026-10-03
    flash.backup_local_data()

    assert flash.backup_path().read_bytes() == before
    assert "devices.yaml" in capsys.readouterr().err
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert "devices.yaml" in z.namelist()


def test_backup_rewrites_the_archive_when_nothing_is_missing(tmp_path, monkeypatch):
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()

    flash.save_registry(reg, [
        flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03"),
        flash.Device("11:22:33:44:55:66", "clock-y", "Clock Y", "2026-10-03"),
    ])
    flash.backup_local_data()

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert "clock-y" in z.read("devices.yaml").decode()
        assert "holds 2 clocks." in z.read("README.md").decode()


def test_backup_rewrites_the_archive_when_a_file_is_added(tmp_path, monkeypatch):
    # The guard is a proper-subset test, so gaining a file must not trip it.
    reg, _ = _local_data(tmp_path, monkeypatch, registry=False)
    flash.backup_local_data()
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "secrets.yaml"]

    flash.save_registry(reg, [flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03")])
    flash.backup_local_data()

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "devices.yaml", "secrets.yaml"]


def test_backup_leaves_no_temporary_file_behind(tmp_path, monkeypatch):
    _local_data(tmp_path, monkeypatch)

    flash.backup_local_data()

    assert [p.name for p in flash.backup_path().parent.iterdir()] == [flash.ARCHIVE_NAME]


def test_backup_warns_and_does_not_raise_when_it_cannot_write(tmp_path, monkeypatch, capsys):
    _local_data(tmp_path, monkeypatch)
    blocker = tmp_path / "backup"
    blocker.write_text("")  # a file where the directory needs to be

    flash.backup_local_data()

    assert "warning" in capsys.readouterr().err
```

Add `import zipfile` to the test file's imports, after `import os`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_flash.py -k "backup_writes or backup_skips or backup_keeps or backup_rewrites or backup_leaves or backup_warns" -v`
Expected: FAIL with `AttributeError: module 'flash' has no attribute 'backup_local_data'`

- [ ] **Step 3: Write the minimal implementation**

Append to the safety backup section in `flash.py`:

```python
def _archive_members(path: Path) -> set[str]:
    """The data files an existing archive holds; empty when it cannot be read."""
    if not path.exists():
        return set()
    try:
        with zipfile.ZipFile(path) as z:
            return {n for n in z.namelist() if n in BACKUP_MEMBERS}
    except (OSError, zipfile.BadZipFile):
        return set()


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
        except (FlashError, OSError, yaml.YAMLError) as e:
            print(f"warning: not backing up {name}: {e}", file=sys.stderr)
            continue
        out.append((name, path))
    return out


def backup_local_data() -> None:
    """Copy devices.yaml and secrets.yaml into the safety archive.

    Never raises. Firmware getting onto the board matters more than the copy,
    so every failure warns and returns.
    """
    target = backup_path()
    tmp = target.with_name(target.name + ".tmp")
    try:
        candidates = _backup_candidates(_local_data_paths())
        names = {n for n, _ in candidates}
        existing = _archive_members(target)
        if names < existing:
            lost = ", ".join(sorted(existing - names))
            print(
                f"warning: keeping the existing safety backup: it still holds {lost}, "
                "which is missing or unreadable here.",
                file=sys.stderr,
            )
            return
        if not candidates:
            return
        clocks = None
        for name, path in candidates:
            if name == "devices.yaml":
                clocks = len(load_registry(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.md", backup_readme([n for n, _ in candidates], clocks))
            for name, path in candidates:
                z.write(path, name)
        os.replace(tmp, target)
    except (OSError, FlashError, zipfile.BadZipFile) as e:
        print(f"warning: could not write the safety backup: {e}", file=sys.stderr)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_flash.py -v`
Expected: PASS, 96 passed

- [ ] **Step 5: Commit**

```bash
git add firmware/esphome/flash.py firmware/esphome/tests/test_flash.py
git commit -F - <<'MSGEOF'
Write the clock registry and secrets to a safety archive

One rolling zip, written to a temporary file and os.replace()d into position
so an interrupted run cannot leave half an archive.

Two guards earn their keep. A file that will not parse is skipped rather than
copied, so a truncated registry cannot overwrite the last good copy of
itself. And an archive is never allowed to shrink: if the files to hand are a
proper subset of what the archive already holds, the old archive stays and
the run warns. That second guard is the accident of 2026-10-03 exactly - a
pull ate devices.yaml, and the next flash would otherwise have replaced a
two-file backup with a one-file one and destroyed the only remaining copy of
the file that had just been lost.

The comparison is over data files only. README.md is always present and would
mask the loss.

Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)
MSGEOF
```

---

### Task 4: Restoring from the archive

**Files:**
- Modify: `firmware/esphome/flash.py` (the safety backup section)
- Test: `firmware/esphome/tests/test_flash.py`

**Interfaces:**
- Consumes: `backup_path()`, `_local_data_paths()`, `load_registry()`, `FlashError`.
- Produces: `restore_local_data() -> None`. Task 5 calls it from `main()` before `find_port()`.

- [ ] **Step 1: Write the failing tests**

Add to the safety backup section of `tests/test_flash.py`:

```python
def test_restore_puts_back_a_missing_registry(tmp_path, monkeypatch, capsys):
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    reg.unlink()

    flash.restore_local_data()

    assert len(flash.load_registry(reg)) == 1
    out = capsys.readouterr().out
    assert "Restored devices.yaml" in out
    assert "(1 clock)" in out
    assert str(flash.backup_path()) in out


def test_restore_puts_back_missing_secrets(tmp_path, monkeypatch, capsys):
    _, sec = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    sec.unlink()

    flash.restore_local_data()

    assert "api_key_clock_x" in sec.read_text()
    assert "Restored secrets.yaml" in capsys.readouterr().out


def test_restore_never_overwrites_a_file_that_exists(tmp_path, monkeypatch, capsys):
    reg, sec = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    sec.write_text("wifi_ssid: edited-since\n")

    flash.restore_local_data()

    assert sec.read_text() == "wifi_ssid: edited-since\n"
    assert "Restored" not in capsys.readouterr().out


def test_restore_without_an_archive_says_nothing(tmp_path, monkeypatch, capsys):
    reg, _ = _local_data(tmp_path, monkeypatch)
    reg.unlink()

    flash.restore_local_data()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert not reg.exists()


def test_restore_warns_on_an_archive_it_cannot_open(tmp_path, monkeypatch, capsys):
    reg, _ = _local_data(tmp_path, monkeypatch)
    reg.unlink()
    flash.backup_path().parent.mkdir(parents=True, exist_ok=True)
    flash.backup_path().write_text("not a zip file")

    flash.restore_local_data()

    assert "warning" in capsys.readouterr().err
    assert not reg.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_flash.py -k restore -v`
Expected: FAIL with `AttributeError: module 'flash' has no attribute 'restore_local_data'`

- [ ] **Step 3: Write the minimal implementation**

Append to the safety backup section in `flash.py`:

```python
def restore_local_data() -> None:
    """Put back any local data file that has gone missing.

    Runs before the registry is read, because a missing registry makes
    flash.py mint a fresh identity for a clock Home Assistant has already
    paired and nothing downstream can tell that happened. Never raises, and
    never overwrites a file that is still there.
    """
    archive = backup_path()
    if not archive.exists():
        return
    paths = _local_data_paths()
    restored: list[str] = []
    try:
        with zipfile.ZipFile(archive) as z:
            held = set(z.namelist())
            for name, path in paths.items():
                if path.exists() or name not in held:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(z.read(name))
                restored.append(name)
    except (OSError, zipfile.BadZipFile) as e:
        print(f"warning: could not read the safety backup {archive}: {e}", file=sys.stderr)
        return
    if not restored:
        return
    for name in restored:
        if name == "devices.yaml":
            try:
                n = len(load_registry(paths[name]))
            except FlashError:
                print("Restored devices.yaml from the safety backup.")
                continue
            print(
                f"Restored devices.yaml from the safety backup "
                f"({n} {'clock' if n == 1 else 'clocks'})."
            )
        else:
            print(f"Restored {name} from the safety backup.")
    print(f"  {archive}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_flash.py -v`
Expected: PASS, 101 passed

- [ ] **Step 5: Commit**

```bash
git add firmware/esphome/flash.py firmware/esphome/tests/test_flash.py
git commit -F - <<'MSGEOF'
Put back a registry or secrets file that has gone missing

A missing devices.yaml is the expensive case: flash.py mints a fresh identity
for a clock Home Assistant has already paired, and nothing downstream can
tell that it happened. So the restore runs before the registry is read, and
it reports which files came back and how many clocks the registry holds.

A file that still exists is never overwritten - the archive is a floor, not
an authority. A missing archive is a first run, not an error, and says
nothing. An archive that will not open warns rather than claiming a restore
it did not perform.

Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)
MSGEOF
```

---

### Task 5: Wire it into a flash, and document it

**Files:**
- Modify: `firmware/esphome/flash.py:345-375` (the `try` block in `main()`)
- Modify: `firmware/esphome/README.md` (the "Build and flash" section)
- Test: `firmware/esphome/tests/test_flash.py`

**Interfaces:**
- Consumes: `backup_local_data()`, `restore_local_data()`.
- Produces: nothing new. This is the last task.

- [ ] **Step 1: Write the failing tests**

Add to the safety backup section of `tests/test_flash.py`:

```python
def test_main_backs_up_after_registering(tmp_path, monkeypatch):
    monkeypatch.delenv("MSYSTEM", raising=False)  # not Git Bash
    reg, sec = _layout(tmp_path)
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "backup")
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    monkeypatch.setattr(flash, "HERE", tmp_path)
    monkeypatch.setattr(flash, "find_port", lambda explicit: "COM4")
    monkeypatch.setattr(flash, "read_mac", lambda port: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(flash, "_now", lambda: NOW)
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: 0)

    assert flash.main([]) == 0

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "devices.yaml", "secrets.yaml"]
        assert "aa:bb:cc:dd:ee:ff" in z.read("devices.yaml").decode()


def test_main_backs_up_under_register_only(tmp_path, monkeypatch):
    reg, sec = _layout(tmp_path)
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "backup")
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    monkeypatch.setattr(flash, "HERE", tmp_path)
    monkeypatch.setattr(flash, "find_port", lambda explicit: "COM4")
    monkeypatch.setattr(flash, "read_mac", lambda port: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(flash, "_now", lambda: NOW)
    monkeypatch.setattr(flash.subprocess, "call", lambda *a, **k: pytest.fail("esphome must not run"))

    assert flash.main(["--register-only"]) == 0
    assert flash.backup_path().exists()


def test_main_restores_before_reading_the_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MSYSTEM", raising=False)  # not Git Bash
    reg, sec = _layout(tmp_path)
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "backup")
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    monkeypatch.setattr(flash, "HERE", tmp_path)
    monkeypatch.setattr(flash, "find_port", lambda explicit: "COM4")
    monkeypatch.setattr(flash, "read_mac", lambda port: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(flash, "_now", lambda: NOW)
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: 0)
    flash.resolve_device("aa:bb:cc:dd:ee:ff", NOW, reg, sec, tmp_path)
    flash.backup_local_data()
    name_before = flash.load_registry(reg)[0].name
    reg.unlink()  # the git pull

    assert flash.main([]) == 0

    out = capsys.readouterr().out
    assert "Restored devices.yaml" in out
    # The clock keeps the identity Home Assistant already paired with.
    assert "Known clock on COM4" in out
    assert flash.load_registry(reg)[0].name == name_before


def test_main_returns_the_flash_exit_code_even_if_the_backup_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MSYSTEM", raising=False)  # not Git Bash
    reg, sec = _layout(tmp_path)
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "backup")
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    monkeypatch.setattr(flash, "HERE", tmp_path)
    monkeypatch.setattr(flash, "find_port", lambda explicit: "COM4")
    monkeypatch.setattr(flash, "read_mac", lambda port: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(flash, "_now", lambda: NOW)
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: 7)
    (tmp_path / "backup").write_text("")  # a file where the directory needs to be

    assert flash.main([]) == 7
    assert "warning" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_flash.py -k "main_backs_up or main_restores or main_returns_the_flash" -v`
Expected: FAIL — the archive is never written, so `zipfile.ZipFile` raises `FileNotFoundError`

- [ ] **Step 3: Write the minimal implementation**

In `flash.py`, change the `try` block in `main()` from:

```python
    try:
        # Only the compile breaks in that shell; registering is fine there.
        if not args.register_only:
            check_shell()
        port = find_port(args.port)
        mac = read_mac(port)
        device, is_new = resolve_device(mac, _now(), REGISTRY_PATH, SECRETS_PATH, HERE)
    except FlashError as e:
```

to:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests -v`
Expected: PASS, 105 passed

- [ ] **Step 5: Document it in the firmware README**

In `firmware/esphome/README.md`, find this paragraph in the "Build and flash" section:

```markdown
Back the two of them up with `secrets.yaml`, though, and keep all three
together. The registry is what stops `flash.py` minting a second identity for
a clock Home Assistant has already paired, and the secrets are the keys that
pairing uses. Lose the pair and every clock has to be re-added by hand.
```

Insert immediately after it:

```markdown
`flash.py` keeps its own copy of both, so losing them takes more than one
mistake. Every flash writes `devices.yaml` and `secrets.yaml` into
`HU-058_clock_safety_backup_of_local_data.zip` in your user data directory
(`%LOCALAPPDATA%\HU-058_ESPHome\` on Windows,
`~/.local/share/HU-058_ESPHome/` elsewhere), along with a `README.md`
explaining what the archive is. If either file is missing when you next run
`flash.py`, it is restored from there before the registry is read, and the run
says so.

The archive is outside the repository on purpose. Both files are gitignored,
and git treats an untracked ignored file as expendable: it deletes one on a
`git pull` carrying the commit that untracked it, silently overwrites one on a
`git checkout` of an earlier commit and deletes it again on the way back, and
removes one with `git clean -xdf`. A copy kept in the working tree would go in
the same command as the original. It is one rolling copy, not a history, and it
is not a substitute for backing the pair up somewhere off this machine — and
because it contains `secrets.yaml`, it is not encrypted and should not be
synced or shared.
```

- [ ] **Step 6: Run the whole suite once more**

Run: `uv run pytest tests -v`
Expected: PASS, 105 passed

- [ ] **Step 7: Commit**

```bash
git add firmware/esphome/flash.py firmware/esphome/tests/test_flash.py firmware/esphome/README.md
git commit -F - <<'MSGEOF'
Back up the local data on every flash

restore_local_data() goes before find_port, so a missing registry is back in
place before anything reads it. backup_local_data() goes after
resolve_device(), where the registry's content is final, which means a flash
that dies at the upload still leaves a good copy - the run that started all
this failed exactly there.

--register-only backs up too: it writes the registry just the same.

Neither call can change the outcome of a flash. Both swallow their own
failures into a warning, and a test pins the exit code of a flash at 7 while
the backup directory is unwritable.

Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)
MSGEOF
```

---

## Verification

After Task 5, from `firmware/esphome/` in PowerShell:

```powershell
uv run pytest tests -q
uv run flash.py --register-only --port COM6
```

Expected: 105 tests pass; the register-only run prints the known clock and leaves
`%LOCALAPPDATA%\HU-058_ESPHome\HU-058_clock_safety_backup_of_local_data.zip`
holding `README.md`, `devices.yaml` and `secrets.yaml`.

Then prove the restore against the real archive, with the live registry moved
aside rather than deleted:

```powershell
Move-Item firmware\esphome\devices.yaml firmware\esphome\devices.yaml.keep
uv run flash.py --register-only --port COM6
```

Expected: `Restored devices.yaml from the safety backup (3 clocks).`, and the
clock is reported as known rather than registered under a new name. Compare the
restored file with `devices.yaml.keep`, then delete the `.keep` copy.
