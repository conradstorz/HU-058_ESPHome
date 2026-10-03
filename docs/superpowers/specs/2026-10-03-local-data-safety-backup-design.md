# Local data safety backup design

Date: 2026-10-03
Scope: `firmware/esphome/flash.py`

## Goal

`devices.yaml` and `secrets.yaml` are the two files that cannot be recreated
from the repository, and losing either costs real work: the registry is what
stops `flash.py` minting a second identity for a clock Home Assistant has
already paired, and the secrets are the keys that pairing uses. Both are
untracked, and git removes untracked ignored files without a word in three
separate situations.

Every flash writes both files into one compressed archive outside the
repository, where nothing git does can reach it, and any run that finds either
file missing restores it from that archive before doing anything else.

## What git actually does to these files

Measured on 2026-10-03 against a fresh clone of `67b3f05`, and the reason the
archive lives outside the working tree:

| Situation | Effect on an untracked, gitignored file |
|-----------|------------------------------------------|
| `git pull` on a clone made after the untracking commit | nothing; there is no tracked copy to delete |
| `git pull` on a clone made before it | deleted once, silently, as the untracking commit arrives |
| `git checkout <pre-untracking commit>` | **overwritten** by the old tracked content, exit 0, no output |
| returning to `main` from that commit | **deleted**, exit 0, no output |
| `git clean -xdf` | deleted, by design |
| deleting or re-cloning the repository | gone with the clone |

The checkout cases are the ones that persist for every future clone:
`checkout.overwriteIgnore` defaults to true, so git treats an ignored file as
expendable. `git bisect` and any look at an older commit hit this.

This also rules out a `post-merge` / `post-checkout` git hook, which would
otherwise fire at exactly the right moment: `.git/hooks` is not cloned, so a
hook needs a tracked hooks directory and a `core.hooksPath` setup step on every
workstation, and so is absent on precisely the workstation that has not been
set up yet.

## The archive

One file, overwritten in place on every flash:

    <user data dir>/HU-058_clock_safety_backup_of_local_data.zip

`<user data dir>` is `platformdirs.user_data_dir("HU-058_ESPHome",
appauthor=False)`: `%LOCALAPPDATA%\HU-058_ESPHome` on Windows,
`~/.local/share/HU-058_ESPHome` elsewhere. The same convention ESPHome uses for
its own caches, and not a cloud-synced location by default, which matters
because the archive contains credentials.

It is per workstation rather than per clone. The clocks are physical objects
belonging to the machine, so two clones of the repository sharing one archive is
correct. A registry restored into a clone whose `secrets.yaml` lacks that
clock's keys already fails loudly through the existing `missing_secrets` check.

Contents, flat at the archive root:

| Member | Source |
|--------|--------|
| `README.md` | generated on every backup |
| `devices.yaml` | `firmware/esphome/devices.yaml` |
| `secrets.yaml` | `firmware/esphome/secrets.yaml` |

Deflate compression, stdlib `zipfile`. The `.zip` extension is deliberate so
Windows Explorer opens it on a double-click.

The archive is not encrypted. It holds every clock's API encryption key and OTA
password and the WiFi password, so it carries the same exposure as
`secrets.yaml` already does: acceptable under the user's own profile, not
something to sync, attach or share. The generated `README.md` says so.

## Backup

Called from `main()` immediately after `resolve_device()` returns, before the
compile, and on the `--register-only` path as well. The registry's content is
final at that point, so a flash that fails at the upload stage still leaves a
backup.

1. Collect the data files. Each of `devices.yaml` and `secrets.yaml` is included
   only if it exists and parses — `load_registry()` for the registry, a
   `yaml.safe_load` for the secrets. A file that will not parse is skipped, so a
   truncated registry cannot overwrite the last good copy of itself.
2. **Shrink guard.** If an archive already exists and the set of data files
   about to be written is a proper subset of the set it already holds, keep the
   existing archive and warn. This is the accident of 2026-10-03 exactly: a pull
   eats `devices.yaml`, and the next flash would otherwise replace a two-file
   archive with a one-file one and destroy the only remaining copy of the file
   that was just lost. The comparison is over data files only; `README.md` is
   always present and would mask the loss.
3. Generate `README.md` (below).
4. Write to a temporary file in the same directory, then `os.replace()` it over
   the archive, so an interrupted run cannot leave a half-written archive.

A backup failure never fails a flash. It prints a warning and the run continues:
firmware getting onto the board matters more than the copy. A missing user data
directory is created.

## Restore

Called from `main()` before the registry is read, ahead of the backup.

1. No archive, or an archive that will not open: warn if it exists and is
   unopenable, otherwise say nothing. A missing archive is a first run, not an
   error.
2. For each of `devices.yaml` and `secrets.yaml`: if the file is missing locally
   and the member is present in the archive, extract it into
   `firmware/esphome/`. A file that exists locally is never overwritten.
3. Print which files were restored and, when the registry was among them, how
   many clocks it holds:

        Restored devices.yaml from the safety backup (3 clocks).
          C:\Users\Conrad\AppData\Local\HU-058_ESPHome\HU-058_clock_safety_backup_of_local_data.zip

Restoring before the registry is read is the whole point: a missing registry
makes `flash.py` mint a fresh identity for a clock that is already paired, and
nothing downstream can tell that happened.

## The generated README.md

Written fresh on each backup so it records where the archive came from rather
than being a static blob. It states:

- what the archive is, and that `flash.py` wrote it automatically
- the workstation name, the repository path and the UTC timestamp of the write
- what `devices.yaml` and `secrets.yaml` each do, and how many clocks the
  registry held
- that the next `uv run flash.py` restores either file automatically if it goes
  missing, and how to unzip them into `firmware/esphome/` by hand instead
- why the archive lives outside the repository, naming the pull, checkout and
  clean cases
- that `secrets.yaml` holds live credentials, so the archive should not be
  synced, shared or attached anywhere

## Code shape

All of it in `flash.py`, which is where the registry and secrets helpers already
live and the only place that launches a flash.

| Function | Purpose |
|----------|---------|
| `backup_dir() -> Path` | The user data directory. The seam the tests redirect. |
| `backup_path() -> Path` | `backup_dir() / ARCHIVE_NAME`. |
| `backup_readme(files, clocks) -> str` | The generated `README.md` body. |
| `backup_local_data() -> None` | Steps 1-4 above. Warns, never raises. |
| `restore_local_data() -> None` | Steps 1-3 above. Warns, never raises. |

`ARCHIVE_NAME = "HU-058_clock_safety_backup_of_local_data.zip"` and
`BACKUP_MEMBERS = ("devices.yaml", "secrets.yaml")` as module constants.

## Tests

In `firmware/esphome/tests/test_flash.py`, with `backup_dir` monkeypatched to a
`tmp_path`:

1. A normal flash writes an archive holding `README.md`, `devices.yaml` and
   `secrets.yaml`.
2. `--register-only` writes the archive too.
3. A registry that does not parse is skipped, and the rest of the archive is
   still written.
4. Secrets that do not parse are skipped the same way.
5. The shrink guard keeps the existing archive and warns when a data file has
   gone missing since the last backup.
6. The shrink guard does not fire when the data files are unchanged, nor when a
   file is added.
7. `restore_local_data()` restores a missing `devices.yaml`.
8. It restores a missing `secrets.yaml`.
9. It leaves a file that exists on disk untouched.
10. A missing archive restores nothing and does not warn.
11. An unopenable archive warns and restores nothing.
12. A backup failure (unwritable directory) leaves the flash's exit code
    untouched.
13. The generated `README.md` names the archive's own path and the clock count.
14. The archive is written atomically: no temporary file is left behind.

## Out of scope

- Backup history. One rolling copy, overwritten each flash.
- Encryption of the archive.
- Backing up anything else under `firmware/esphome/`. The minted
  `clock-*.yaml` device files are rewritten from the registry whenever they are
  missing (`flash.py:333`), so restoring the registry restores them too.
