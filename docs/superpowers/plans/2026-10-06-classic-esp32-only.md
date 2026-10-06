# Classic ESP32 Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The clock project says, everywhere, that the classic ESP32 (WROOM-32 family) is the board it runs on; `flash.py` records how every attempt ended in the registry; and everything learned about other chips and a generic board-fitting tool moves into one new file, `possible-future-directions.md`, replacing the two roadmaps.

**Architecture:** Two new optional fields on the `Device` dataclass (`last_attempt`, `last_result`) written by a new `record_result()` after `main()` runs `esphome compile` and then `esphome run`, so a failed build and a failed upload are told apart. Docs change in place: the firmware README's "Which ESP32" section becomes a plain list of supported boards, `AGENTS.md` stops talking about variants, `upstream-ideas.md` loses its easy-flash section, and the two roadmap files are deleted after their per-chip findings are copied into the new file's appendix.

**Tech Stack:** Python 3.12+ via `uv`, PyYAML, pytest. No new dependencies.

Decisions were made in conversation on 2026-10-06, not in a spec file; they are recorded under "Decisions" below.

## Global Constraints

- Code lives in `firmware/esphome/`; paths in Tasks 1–3 are relative to it. Tasks 4–5 use repository-root paths.
- Run tests with `uv run pytest` from `firmware/esphome/`. Full suite must pass after every task (151 collected today: 150 passed, 1 skipped on Windows; the skip is expected).
- Do not chain shell commands with `&&`. One command per tool call.
- `RESULTS = ("registered", "flashed", "build-failed", "flash-failed")` exactly. These are the only legal `last_result` values.
- `last_attempt` and `last_result` are optional on `Device`, default `None`, and are **omitted** from `devices.yaml` when `None`. An existing registry without them must load unchanged; the registry copy inside the safety archive goes through the same parser.
- `main()` runs `esphome compile <name>.yaml` first, then the existing `esphome run <name>.yaml --device <port> *extra`. The run command's shape does not change.
- Recording a result must never block the flash's exit code: a `FlashError` from `record_result()` prints `warning: ...` to stderr and the original return code is still returned.
- Every commit message ends with the line `Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)` and nothing after it. Subject line: sentence case, imperative, no prefix.
- Never add a MAC address to a tracked file. `devices.yaml.example` uses the fake `24:0a:c4:00:00:0x` MACs already there.
- Never claim any chip other than the classic ESP32 is supported, anywhere in the tracked tree. The one place other chips may be named is `possible-future-directions.md`.

## Decisions (2026-10-06)

1. The clock targets the classic ESP32 only. `flash.py`'s `--chip esp32` refusal is correct and stays.
2. `upstream-ideas.md` stays; only its "What comes next: the easy-flash system" section is cut, plus the three sentences elsewhere in it that lean on that section.
3. Outcome tracking is two fields, `last_attempt` + `last_result`, overwritten each run. No history list, no notes field.
4. The per-chip findings go into `possible-future-directions.md` as an appendix so deleting the roadmaps loses nothing.
5. `flash-clock.py`, `docs/`, `clock-base.yaml`, the component and the panel-test firmware are untouched: none of them claim other chips.

---

### Task 1: Outcome fields on the registry

**Files:**
- Modify: `flash.py:117-122` (the `Device` dataclass), `flash.py:125-154` (`load_registry_text`), `flash.py:163-178` (`save_registry`), and add `record_result()` after `find_device()` at `flash.py:181`.
- Test: `tests/test_flash.py` — add a new section after `test_load_registry` tests (search for `def test_find_device`; insert the new tests immediately before it).

**Interfaces:**
- Produces: `Device(mac, name, friendly_name, first_flashed, last_attempt=None, last_result=None)`; `RESULTS: tuple[str, ...]`; `record_result(registry_path: Path, mac: str, now: datetime, result: str) -> Device`.
- Task 2 calls `record_result(REGISTRY_PATH, mac, _now(), result)`.

- [ ] **Step 1: Write the failing tests**

Insert before `def test_find_device` in `tests/test_flash.py`:

```python
# --- outcome of the last attempt --------------------------------------------

# The registry records how each clock's most recent run ended. The two fields
# are optional on purpose: every devices.yaml written before they existed, and
# the copy of one inside the safety archive, must still load.

def test_registry_without_outcome_fields_loads_with_none():
    text = (
        "devices:\n"
        "- mac: 'aa:bb:cc:dd:ee:ff'\n"
        "  name: clock-x\n"
        "  friendly_name: Clock X\n"
        "  first_flashed: '2026-10-03'\n"
    )
    [d] = flash.load_registry_text(text, "devices.yaml")
    assert d.last_attempt is None
    assert d.last_result is None
    assert d == flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03")


def test_save_registry_omits_unset_outcome_fields(tmp_path):
    reg = tmp_path / "devices.yaml"
    flash.save_registry(reg, [flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03")])
    text = reg.read_text()
    assert "last_attempt" not in text
    assert "last_result" not in text


def test_registry_round_trips_outcome_fields(tmp_path):
    reg = tmp_path / "devices.yaml"
    d = flash.Device(
        "aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03",
        last_attempt="2026-10-06T09:00:00", last_result="flash-failed",
    )
    flash.save_registry(reg, [d])
    assert flash.load_registry(reg) == [d]
    assert "last_result: flash-failed" in reg.read_text()


def test_load_registry_rejects_an_unknown_result():
    text = (
        "devices:\n"
        "- mac: 'aa:bb:cc:dd:ee:ff'\n"
        "  name: clock-x\n"
        "  friendly_name: Clock X\n"
        "  first_flashed: '2026-10-03'\n"
        "  last_result: exploded\n"
    )
    with pytest.raises(flash.FlashError, match="last_result"):
        flash.load_registry_text(text, "devices.yaml")


def test_record_result_updates_the_matching_entry(tmp_path):
    reg = tmp_path / "devices.yaml"
    flash.save_registry(reg, [
        flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03"),
        flash.Device("11:22:33:44:55:66", "clock-y", "Clock Y", "2026-10-03"),
    ])

    updated = flash.record_result(reg, "AA:BB:CC:DD:EE:FF", NOW, "build-failed")

    assert updated.last_attempt == "2026-09-28T14:07:00"
    assert updated.last_result == "build-failed"
    x, y = flash.load_registry(reg)
    assert x == updated
    assert y.last_result is None  # the other clock is untouched


def test_record_result_overwrites_the_previous_outcome(tmp_path):
    reg = tmp_path / "devices.yaml"
    flash.save_registry(reg, [flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03")])
    flash.record_result(reg, "aa:bb:cc:dd:ee:ff", NOW, "flash-failed")
    later = NOW.replace(hour=15)

    flash.record_result(reg, "aa:bb:cc:dd:ee:ff", later, "flashed")

    [d] = flash.load_registry(reg)
    assert (d.last_attempt, d.last_result) == ("2026-09-28T15:07:00", "flashed")


def test_record_result_refuses_an_unknown_mac(tmp_path):
    reg = tmp_path / "devices.yaml"
    flash.save_registry(reg, [flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03")])
    with pytest.raises(flash.FlashError, match="not in devices.yaml"):
        flash.record_result(reg, "11:22:33:44:55:66", NOW, "flashed")


def test_record_result_refuses_an_unknown_result(tmp_path):
    reg = tmp_path / "devices.yaml"
    flash.save_registry(reg, [flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03")])
    with pytest.raises(ValueError):
        flash.record_result(reg, "aa:bb:cc:dd:ee:ff", NOW, "exploded")
```

`NOW` is already defined at `tests/test_flash.py:285` as `datetime(2026, 9, 28, 14, 7, 0)`. If the new section lands above that line, move the `NOW = ...` definition up to just under the imports instead of duplicating it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_flash.py -k "outcome or record_result or unknown_result" -v`
Expected: the eight new tests FAIL — `TypeError: Device.__init__() got an unexpected keyword argument 'last_attempt'` and `AttributeError: module 'flash' has no attribute 'record_result'`.

- [ ] **Step 3: Implement**

Replace the dataclass at `flash.py:117-122`:

```python
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
```

Replace the list comprehension at the end of `load_registry_text()` (`flash.py:146-154`):

```python
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
```

In `save_registry()` (`flash.py:170-174`) change the dump input so unset fields stay out of the file:

```python
    body = yaml.safe_dump(
        {"devices": [{k: v for k, v in asdict(d).items() if v is not None} for d in devices]},
        sort_keys=False,
        default_flow_style=False,
    )
```

Add after `find_device()` (`flash.py:181-183`):

```python
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
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: 158 passed, 1 skipped (150 + 8). `test_resolve_new_device_creates_everything` still passes because the four-argument `Device(...)` equals one with default `None`s.

- [ ] **Step 5: Commit**

```bash
git add firmware/esphome/flash.py firmware/esphome/tests/test_flash.py
```

```bash
git commit -F - <<'EOF'
Record how the last run against each clock ended

Two optional fields on the registry entry, last_attempt and last_result,
written by a new record_result(). The legal results are registered,
flashed, build-failed and flash-failed. Both fields are left out of
devices.yaml while unset, so a registry written before this change, and
the copy of one inside the safety archive, load exactly as before.

Nothing calls record_result() yet; main() is wired up next.

Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)
EOF
```

---

### Task 2: `main()` compiles, then flashes, then records

**Files:**
- Modify: `flash.py:954-991` (`main()`).
- Test: `tests/test_flash.py:376-395` (`test_main_flashes_known_device_and_passes_args`), `tests/test_flash.py:1690-1706` (`test_main_hands_the_build_env_to_esphome`), plus new tests appended after `test_main_returns_the_flash_exit_code_even_if_the_backup_fails`.

**Interfaces:**
- Consumes: `record_result()`, `RESULTS` from Task 1; `backup_local_data()`, `build_env()`, `secret_names()` already in `flash.py`.
- Produces: `main()` makes two `subprocess.call`s on a flash run — `[sys.executable, "-m", "esphome", "compile", "<name>.yaml"]` then `[sys.executable, "-m", "esphome", "run", "<name>.yaml", "--device", port, *extra]` — both with `cwd=HERE, env=build_env()`.

- [ ] **Step 1: Update the two existing tests that index `calls[0]`**

`test_main_flashes_known_device_and_passes_args` — replace the assertions after `rc = flash.main(["--no-logs"])`:

```python
    assert rc == 7
    compile_cmd, compile_kw = calls[0]
    run_cmd, run_kw = calls[1]
    assert compile_cmd[:2] == [flash.sys.executable, "-m"]
    assert compile_cmd[2:] == ["esphome", "compile", "clock-20260928-1407.yaml"]
    assert run_cmd[:2] == [flash.sys.executable, "-m"]
    assert run_cmd[2:] == ["esphome", "run", "clock-20260928-1407.yaml", "--device", "COM4", "--no-logs"]
    assert compile_kw["cwd"] == tmp_path
    assert run_kw["cwd"] == tmp_path
```

`test_main_hands_the_build_env_to_esphome` — replace the final assertion:

```python
    assert [c["env"] for c in calls] == [{"PATH": "sentinel"}, {"PATH": "sentinel"}]
```

- [ ] **Step 2: Write the failing tests for recording**

Append after `test_main_returns_the_flash_exit_code_even_if_the_backup_fails`:

```python
# --- recording the outcome ----------------------------------------------------

# esphome run compiles and uploads in one go and its exit code does not say
# which half failed, so main() compiles first on its own. A failed compile is
# recorded as build-failed and the upload never starts; a failed upload after
# a clean compile is flash-failed.

def _main_layout(tmp_path, monkeypatch):
    monkeypatch.delenv("MSYSTEM", raising=False)  # not Git Bash
    reg, sec = _layout(tmp_path)
    flash.resolve_device("aa:bb:cc:dd:ee:ff", NOW, reg, sec, tmp_path)
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "backup")
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    monkeypatch.setattr(flash, "HERE", tmp_path)
    monkeypatch.setattr(flash, "find_port", lambda explicit: "COM4")
    monkeypatch.setattr(flash, "read_mac", lambda port: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(flash, "_now", lambda: NOW.replace(hour=16))
    return reg


def _outcome(reg):
    [d] = flash.load_registry(reg)
    return d.last_attempt, d.last_result


def test_main_records_flashed_after_a_clean_run(tmp_path, monkeypatch):
    reg = _main_layout(tmp_path, monkeypatch)
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: 0)

    assert flash.main([]) == 0
    assert _outcome(reg) == ("2026-09-28T16:07:00", "flashed")


def test_main_records_build_failed_and_does_not_upload(tmp_path, monkeypatch):
    reg = _main_layout(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 3)

    assert flash.main([]) == 3
    assert _outcome(reg) == ("2026-09-28T16:07:00", "build-failed")
    assert len(calls) == 1
    assert calls[0][2:4] == ["esphome", "compile"]


def test_main_records_flash_failed_when_only_the_upload_fails(tmp_path, monkeypatch):
    reg = _main_layout(tmp_path, monkeypatch)
    codes = iter([0, 7])
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: next(codes))

    assert flash.main([]) == 7
    assert _outcome(reg) == ("2026-09-28T16:07:00", "flash-failed")


def test_main_records_registered_under_register_only(tmp_path, monkeypatch):
    reg = _main_layout(tmp_path, monkeypatch)
    monkeypatch.setattr(flash.subprocess, "call", lambda *a, **k: pytest.fail("esphome must not run"))

    assert flash.main(["--register-only"]) == 0
    assert _outcome(reg) == ("2026-09-28T16:07:00", "registered")


def test_main_backup_holds_the_outcome(tmp_path, monkeypatch):
    _main_layout(tmp_path, monkeypatch)
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: 0)

    flash.main([])

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert "last_result: flashed" in z.read("devices.yaml").decode()


def test_main_keeps_the_flash_exit_code_if_recording_fails(tmp_path, monkeypatch, capsys):
    reg = _main_layout(tmp_path, monkeypatch)
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: 0)

    def boom(*a, **k):
        raise flash.FlashError("registry is read-only")

    monkeypatch.setattr(flash, "record_result", boom)

    assert flash.main([]) == 0
    assert "warning: registry is read-only" in capsys.readouterr().err
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_flash.py -k "main" -v`
Expected: the two edited tests and the six new ones FAIL (`calls[1]` IndexError, outcome `(None, None)`, `len(calls) == 2`); everything else in `-k main` still passes.

- [ ] **Step 4: Implement**

Replace the tail of `main()` from `api_key_name, _ = secret_names(device.name)` (`flash.py:979`) to the end of the function:

```python
    api_key_name, _ = secret_names(device.name)
    if is_new:
        print(f"New clock on {port} with MAC {mac}: registered as {device.name} ({device.friendly_name}).")
        print(f"When Home Assistant asks for an encryption key, use {api_key_name} from secrets.yaml.")
    else:
        print(f"Known clock on {port} with MAC {mac}: {device.name} ({device.friendly_name}).")

    def record(result: str) -> None:
        # The outcome is bookkeeping. A registry that will not take it is worth
        # a warning, never worth hiding how the flash itself went.
        try:
            record_result(REGISTRY_PATH, mac, _now(), result)
        except FlashError as e:
            print(f"warning: {e}", file=sys.stderr)
        backup_local_data()

    if args.register_only:
        record("registered")
        return 0

    # esphome run compiles and uploads in one command, and its exit code does
    # not say which half failed. Compiling first on its own tells them apart;
    # the run's own compile pass is then a no-op.
    device_yaml = f"{device.name}.yaml"
    env = build_env()
    rc = subprocess.call([sys.executable, "-m", "esphome", "compile", device_yaml], cwd=HERE, env=env)
    if rc != 0:
        record("build-failed")
        return rc
    rc = subprocess.call(
        [sys.executable, "-m", "esphome", "run", device_yaml, "--device", port, *extra],
        cwd=HERE,
        env=env,
    )
    record("flashed" if rc == 0 else "flash-failed")
    return rc
```

Leave the existing `backup_local_data()` call after `resolve_device()` where it is: it protects a freshly minted identity even if the process dies mid-flash. The second call inside `record()` picks up the outcome.

Update the module docstring's `Usage:` line at `flash.py:9` — no change to flags, so leave it; but add one sentence to the first paragraph after "A MAC seen before gets exactly what it had.":

```
Every run also notes in the registry how it ended: registered, flashed,
build-failed or flash-failed, with the time.
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: 164 passed, 1 skipped (158 + 6).

- [ ] **Step 6: Commit**

```bash
git add firmware/esphome/flash.py firmware/esphome/tests/test_flash.py
```

```bash
git commit -F - <<'EOF'
Compile before flashing, and write the outcome to the registry

esphome run compiles and uploads in one command and its exit code does not
say which half failed, so main() now runs esphome compile on its own first.
A failed compile is recorded as build-failed and no upload is attempted; a
failed upload after a clean compile is flash-failed; a clean run is
flashed; --register-only is registered. The safety backup is refreshed
after the outcome lands so the archive carries it too.

A registry that refuses the write gets a warning on stderr and the flash's
own exit code is still returned.

Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)
EOF
```

---

### Task 3: Document the outcome fields

**Files:**
- Modify: `README.md` (the firmware one, `firmware/esphome/README.md`) "Build and flash" section, lines 121-135.
- Modify: `devices.yaml.example`.
- Modify: `../../AGENTS.md` (repository root), the `devices.yaml` bullet under "Working conventions".
- Test: `tests/test_flash.py` — the existing "the example registry" test near the end of the file loads `devices.yaml.example` through `load_registry_text`; it must still pass.

- [ ] **Step 1: Firmware README**

In `firmware/esphome/README.md`, replace the two bullets under "Plug in one clock and run that..." (lines 128-134) with:

```markdown
- **A clock it has never seen** gets a name from the current time,
  `clock-YYYYMMDD-HHMM`, a fresh API key and OTA password appended to
  `secrets.yaml`, an entry in `devices.yaml`, and a `<name>.yaml` device file
  next to this README. Then it compiles and flashes.
- **A clock it has seen** gets exactly the identity it had, so reflashing
  never disturbs its pairing with Home Assistant.
- **Either way the entry records how the run ended**: `last_attempt` is the
  time and `last_result` is one of `registered`, `flashed`, `build-failed` or
  `flash-failed`. The build runs on its own before the upload so the two
  failures are told apart. One look at `devices.yaml` says which boards on
  the bench have a working clock on them and which were tried and did not.
```

- [ ] **Step 2: Example registry**

In `devices.yaml.example`, extend the header comment and the second entry. After the sentence ending "...the one field that must never change for a clock already paired." add:

```
# `last_attempt` and `last_result` say how the most recent run against that
# clock ended: registered, flashed, build-failed or flash-failed. They are
# absent on an entry that predates them and fill in on its next run.
```

Change the second entry to:

```yaml
- mac: '24:0a:c4:00:00:02'
  name: clock-20260929-0912
  friendly_name: Clock 2026-09-29 09:12
  first_flashed: '2026-09-29T09:12:50'
  last_attempt: '2026-10-06T10:41:03'
  last_result: flashed
```

Leave the first entry without the fields: it demonstrates the pre-change shape still loading.

- [ ] **Step 3: AGENTS.md**

In the root `AGENTS.md`, the bullet beginning "`devices.yaml` and the minted `clock-YYYYMMDD-HHMM.yaml` device files are gitignored" — append one sentence before "Never commit either":

```
Each entry also carries `last_attempt` and `last_result` (registered, flashed, build-failed, flash-failed) for the most recent run against that board.
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest`
Expected: 164 passed, 1 skipped. The example-registry test proves the new example still parses and its `last_result` is a legal value.

- [ ] **Step 5: Commit**

```bash
git add firmware/esphome/README.md firmware/esphome/devices.yaml.example AGENTS.md
```

```bash
git commit -F - <<'EOF'
Describe the outcome fields in the README and the example registry

Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)
EOF
```

---

### Task 4: Say which boards run the clock, and nothing else

**Files:**
- Modify: `firmware/esphome/README.md` lines 34-66 (the "Which ESP32" section and its two subsections).
- Modify: `AGENTS.md` line 23.

No code or tests change. `flash.py`'s `--chip esp32` in `read_mac()` already refuses every other chip, and `test_read_mac_rejects_non_esp32` (around `tests/test_flash.py:275`) already covers it.

- [ ] **Step 1: Rewrite "Which ESP32"**

Replace everything from `## Which ESP32` up to (not including) `## Setup` in `firmware/esphome/README.md` with:

```markdown
## Which ESP32

These are the boards that can be programmed to run the clock: the plain
**ESP32-WROOM-32** devkit family, the common 30 or 38 pin board that sells for
a few dollars. WROOM-32, WROVER, DevKitC and NodeMCU-32S all work unchanged. If
the listing says ESP32 with no letter after it, that is the one. It needs to be
a board that can be powered directly from 5V on the clock's USB port, and these
all have a 3.3V regulator on board for that.

Nothing else is supported. `flash.py` checks the chip before it writes
anything and refuses any board that is not a classic ESP32 with esptool's own
"This chip is X, not ESP32" message, so a mistake costs nothing. That includes
the kit's own ESP8266, which has none of the timer and GPIO machinery the scan
leans on, and the newer ESP32-S2, S3, C3 and C6, on which this driver has never
been built or timed. What a port to those would take is written up in
`../../possible-future-directions.md`.

The four display pins have to sit below GPIO32. A frame goes out as a single
store to the low GPIO output register, and that register only reaches GPIO0
through GPIO31. `__init__.py` checks this and refuses to build if you pick a
higher pin.

```

(The trailing blank line keeps one empty line before `## Setup`.)

- [ ] **Step 2: AGENTS.md**

Replace line 23 of `AGENTS.md`:

```
- Keep hardware assumptions aligned with the supported ESP32 variants described in [firmware/esphome/README.md](firmware/esphome/README.md).
```

with:

```
- The only supported MCU is the classic ESP32 (the WROOM-32 devkit family), as described in [firmware/esphome/README.md](firmware/esphome/README.md). Do not add other chips or boards; notes on what that would take are in [possible-future-directions.md](possible-future-directions.md).
```

- [ ] **Step 3: Check nothing else in the tracked tree claims another chip**

Run from the repository root:

```bash
git ls-files | grep -v "^firmware/esphome/.esphome" | xargs grep -ln "ESP32-S[23]\|ESP32-C[36]\|S2, S3, C3\|other chip\|ESP32 variants" 2>/dev/null
```

Expected after this task: only `automatic-board-detection-roadmap.md`, `roadmap-to-multi-board-compatability.md` and `upstream-ideas.md` (Task 5 handles all three), plus the new firmware README line that names S2/S3/C3/C6 as *unsupported*. `README.md` and `docs/hardware.md` match the pattern `S2` only as the button name and `C6` only as a capacitor; they are fine.

- [ ] **Step 4: Run the suite** (nothing should move)

Run: `uv run pytest` from `firmware/esphome/`
Expected: 164 passed, 1 skipped.

- [ ] **Step 5: Commit**

```bash
git add firmware/esphome/README.md AGENTS.md
```

```bash
git commit -F - <<'EOF'
Name the boards that run the clock, and drop the other-chip notes

The firmware README's "Which ESP32" now lists the WROOM-32 devkit family
as the supported boards and says plainly that nothing else is: the S2, S3,
C3 and C6 subsection, which described an untested port, is gone, and the
ESP8266 note folds into the same paragraph. flash.py already refuses every
other chip before writing anything. AGENTS.md stops referring to
"supported ESP32 variants".

Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)
EOF
```

---

### Task 5: `possible-future-directions.md` replaces the two roadmaps

**Files:**
- Create: `possible-future-directions.md` (repository root).
- Delete: `automatic-board-detection-roadmap.md`, `roadmap-to-multi-board-compatability.md` (both have uncommitted working-tree edits; the working-tree versions are the ones to copy from, then `git rm -f`).
- Modify: `upstream-ideas.md` — status table row 5, the "Ignored for a month or more" bullet, the roadmap reference in "PR 2", and delete the whole "What comes next: the easy-flash system" section.

**Interfaces:** none. Documentation only.

- [ ] **Step 1: Write the top half of the new file**

Create `possible-future-directions.md` with exactly this content, then continue to Step 2 for the appendix:

````markdown
# Possible future directions

Ideas that were explored for this project and set aside on 2026-10-06, kept
here so the thinking is not lost. None of this is planned. The clock runs on
the classic ESP32 (the WROOM-32 devkit family) and `flash.py` flashes that
board; see `firmware/esphome/README.md`.

## Why this was set aside

Making the clock run on the ESP32-S2, S3, C3 and C6 looked like a few days
of work, and most of it would have gone into a tool that is only loosely
about this clock: identifying the attached board and fitting a project's pin
needs onto it. Building that for one clock and eight boards is a platform
with no second user. The honest scope of this repository is one clock, one
board family, and a registry of the boards that have been tried.

## The idea: a generic board-fitting tool

What was actually being circled is a tool in two halves that would sit
between any ESP32 project and any ESP32 board.

### Half one: identify the board

Only part of this is possible, and knowing which part is the whole lesson.

- **The chip is detectable.** esptool's `detect_chip()` (a public export in
  esptool 5.3.1; `esp.CHIP_NAME`, `esp.get_chip_description()`,
  `esp.get_chip_features()`) gives the chip family, the silicon revision and
  in-package flash and PSRAM from eFuse, so an S3 N8R8 is distinguishable from
  an N8. Do not parse esptool's stdout for this: "Chip is ESP32-S3" is
  esptool 4 phrasing and 5 prints `Chip type:` and `Features:` instead. For
  this project, detection is nothing more than deleting the `--chip esp32`
  hardcode in `read_mac()`.
- **The board is not detectable, by anyone.** Nothing over USB tells an
  ESP32-S3-DevKitC-1 from a Lolin S3 Mini from an unbranded AliExpress module:
  same chip, same flash ID, different pin headers, different onboard LED,
  different PSRAM wiring. The USB bridge VID/PID (already available as
  `p.vid` in `find_port()`) separates a devkit's native USB from a CH340 clone
  but does not close the gap. The classic ESP32 has the widest board spread
  of all (DevKitC, NodeMCU-32S, WROVER, Lolin32, bare WROOM) and they do not
  agree on where the LED is. ESP Web Tools keys its builds by chip family and
  has the human pick the manifest for exactly this reason.
- **So identity is chip plus one stored answer.** Detect what the wire can
  tell you, use it to shrink the question ("I see an ESP32-S3, 8MB flash, no
  PSRAM, CH343 bridge: DevKitC-1, Lolin S3 Mini, or other?"), ask once at
  first registration, store `chip` and `board` beside the MAC, never ask
  again. A known MAC reporting a different chip is an error, not an update.
  The registry fields have to be optional so every existing `devices.yaml`,
  and the copy inside the safety archive, still loads. A three-way prompt
  gets answered correctly; a 318-way list gets answered wrong.
- **Do not build a board database.** ESPHome ships one: 318 entries in
  `esphome/components/esp32/boards.py`, mapping board id to chip variant.
  Emit a `board:` string into that table and own nothing else.

### Half two: fit the project to the board

This half mostly exists already, under a different name.

- **The "generic connector that reports incompatibility" is ESPHome.** Its
  per-chip pin validators (`esphome/components/esp32/gpio_esp32_s3.py` and
  siblings) reject GPIO22 on an S3, warn on USB and strapping pins, and refuse
  flash and PSRAM pins, with real messages, at config time. A project's job
  is to declare its pin needs to it, not to replace it.
- **Nothing is "transcoded".** Once the register writes are made portable
  (see the appendix), the driver is one source for every chip. What varies per
  board is pin assignment plus two feature gates: the kind of indicator LED,
  and whether the Arduino framework is allowed at all (it is not, for the C6,
  in ESPHome 2026.9). That is pin-fitting and a capability check, not code
  transformation.
- **The one genuinely new artifact** would be a small pin requirements
  manifest and a fitter: "four outputs below GPIO32, writable in a single
  register store, two inputs, one indicator", solved against a given board's
  free pins, keyed by board rather than by chip because a chip-family default
  can only use pins legal on every board of that family. Neither esptool nor
  ESPHome has this. It is reusable across projects and it is small. It is
  also the part that turns a flashing script into a platform, which is why it
  stopped here.

### What it would have taken for this clock

For the record, the staged plan that was written and then set aside:

1. Make the driver chip-agnostic: `REG_WRITE(GPIO_OUT_W1TS_REG, mask)` and the
   `_W1TC` twin in `send_pair_()` and in the panel-test `sendFrame()`, and a
   variant check in `__init__.py` so an unsupported chip fails config
   validation with a plain message.
2. Identify the board in `flash.py` as above; store `chip` and `board` in the
   registry; an explicit `--board` override to change it later.
3. Per-board config packages in `boards/`, one file per ESPHome board id,
   holding only the `esp32:` block, the six pins and the heartbeat output.
   The "other" answer writes no package and the user supplies the pins.
4. Print the wiring after flashing: the GPIO-to-MCU-socket table for the
   board that was just configured, since the pins are no longer the ones in
   the README.
5. One physical board per variant on the bench before it is called
   supported, with the scan timing checked on a logic analyser.

Rough cost: two or three days for 1 to 4; open-ended for 5, which is hardware
purchases and bench time, not code.

## Appendix: what was learned about the other chips

Verified against ESP-IDF 5.5.5 headers in the ESPHome cache and ESPHome
2026.9's pin validators. No board other than the classic ESP32 has been built
or tested; every claim below about another chip is a header and validator
inspection, not a flash.

````

- [ ] **Step 2: Append the per-chip findings, copied verbatim**

Append to `possible-future-directions.md`, in this order, copying each section **verbatim** from the working-tree copy of the named file (the working tree carries edits newer than HEAD; use it). Keep each section's own heading but demote it one level (`##` becomes `###`, `###` becomes `####`) so it nests under the appendix. Copy from the heading line through the line before the next same-or-higher-level heading:

1. From `automatic-board-detection-roadmap.md`: `## What the firmware depends on` (the dependency table and its intro).
2. From `automatic-board-detection-roadmap.md`: `## Per chip`, with all of its `###` subsections (ESP32, ESP32-S3, ESP32-S2, ESP32-C3, ESP32-C6, ESP8266, ESP32-P4 and later).
3. From `automatic-board-detection-roadmap.md`: `## Portable code change` (the `GPIO_SET` macro block and the `REG_WRITE` note).
4. From `roadmap-to-multi-board-compatability.md`: `## Suggested S3 pin map` (the table and its two-line footer).
5. From `roadmap-to-multi-board-compatability.md`: these four subsections of `## Decisions`, each as `###` under a new `### Decisions that were reached` heading (so they become `####`): `Heartbeat: WS2812 (decided)`, `C6: needs ESP-IDF, so defer it`, `Register writes: REG_WRITE is the same store`, `Single core: unknown, and here is how to find out before buying`. **Skip** `Board identity: ask once, store it (decided)`; its content is already in "Half one" above.
6. From `automatic-board-detection-roadmap.md`: `## Open questions`.

After pasting, fix the cross-references inside the copied text so they do not point at the deleted files: replace every occurrence of `` `roadmap-to-multi-board-compatability.md` `` and `` `automatic-board-detection-roadmap.md` `` within `possible-future-directions.md` with `this file`, and reword the sentence around it if that leaves it ungrammatical (e.g. "The heartbeat LED is settled in this file (a one-LED ...)"). Also in the copied "Open questions", the item that begins "How many boards should the prompt offer" refers to "the Stage 3 prompt"; change that to "the board prompt described under Half one".

Check the result:

```bash
grep -n "roadmap-to-multi-board\|automatic-board-detection\|Stage [0-9]\|Step [0-9]" possible-future-directions.md
```

Expected: no matches except inside the numbered list under "What it would have taken for this clock", which does not use the word "Stage". If a copied sentence refers to "Stage 2" or "Step 6" of the deleted plan, reword it to refer to the numbered item in "What it would have taken for this clock" instead (item 1 for the register change, item 5 for hardware testing).

- [ ] **Step 3: Delete the two roadmaps**

```bash
git rm -f automatic-board-detection-roadmap.md roadmap-to-multi-board-compatability.md
```

- [ ] **Step 4: Edit `upstream-ideas.md`**

Four edits:

a. Status table row 5. Replace:

```
| 5, `flash.py` | not opened | | | on hold, see "What comes next" |
```

with:

```
| 5, `flash.py` | not opened | | | on hold |
```

b. The third outcome bullet under "Three possible outcomes". Replace:

```
- **Ignored for a month or more.** Stop offering small steps. Build the
  easy-flash system on the fork instead and, if anything goes upstream
  later, offer it as one self-contained PR.
```

with:

```
- **Ignored for a month or more.** Stop offering small steps. The fork's
  `flash.py` and per-clock device files are the way this project flashes
  regardless; if anything goes upstream later, offer PRs 3 to 5 together as
  one self-contained PR.
```

c. In "PR 2: heartbeat on the devkit LED", the caveats paragraph. Replace:

```
Caveats stated in the PR text: GPIO2 is the classic ESP32 devkit LED.
Other boards put their LED elsewhere or use a WS2812, so the block is a
no-op there rather than a fault. See
`automatic-board-detection-roadmap.md`. GPIO2 is also a strapping pin, so
```

with:

```
Caveats stated in the PR text: GPIO2 is the classic ESP32 devkit LED.
Other boards put their LED elsewhere or use a WS2812, so the block is a
no-op there rather than a fault; this fork supports only the classic
ESP32, and `possible-future-directions.md` has the notes on the rest.
GPIO2 is also a strapping pin, so
```

d. Delete the entire section from the line `## What comes next: the easy-flash system` up to, but not including, the line `## Not for upstream`. Leave exactly one blank line between the end of the "PR 5, later" section and `## Not for upstream`.

Check:

```bash
grep -n "What comes next\|easy-flash\|easy flash\|automatic-board-detection\|roadmap-to-multi" upstream-ideas.md
```

Expected: no output.

- [ ] **Step 5: Whole-tree check**

From the repository root:

```bash
git ls-files | xargs grep -ln "automatic-board-detection\|roadmap-to-multi-board\|easy-flash" 2>/dev/null
```

Expected: only `docs/superpowers/plans/2026-10-06-classic-esp32-only.md` (this plan). Then:

```bash
git status --short
```

Expected: `D` for the two roadmaps, `M upstream-ideas.md`, `?? possible-future-directions.md`.

- [ ] **Step 6: Run the suite** (docs only; a guard against an accidental edit)

Run: `uv run pytest` from `firmware/esphome/`
Expected: 164 passed, 1 skipped.

- [ ] **Step 7: Commit**

```bash
git add possible-future-directions.md upstream-ideas.md
```

```bash
git commit -F - <<'EOF'
Set the multi-board work aside in possible-future-directions.md

The clock targets the classic ESP32 and nothing else, decided 2026-10-06.
The two roadmaps that planned a port to the S2, S3, C3 and C6 are deleted;
what they found out moves into one file that explains the idea they were
circling - a board-identity step that can detect the chip but must ask for
the board, and a pin-fitting step that ESPHome already mostly provides -
and why it stopped here. The per-chip header findings, the suggested S3
pin map and the decisions on the heartbeat LED, the C6 framework, the
register writes and single-core jitter are kept verbatim as an appendix.

upstream-ideas.md loses its easy-flash section and the three sentences
that leaned on it; the PR tracking stays.

Created for Conrad Storz with the help of Claude Code (conradstorz@gmail.com)
EOF
```

---

## Self-review

**Spec coverage.** Classic-ESP32-only statement: Task 4 (firmware README, AGENTS.md). `flash.py` unchanged in what it accepts: Global Constraints and Task 4 note. Registry kept, with outcome of each attempt: Tasks 1–3. Naming scheme kept: untouched. New `possible-future-directions.md` with the generic-tool knowledge: Task 5 Step 1. Delete the feeding documents: Task 5 Step 3. `upstream-ideas.md` keeps PR tracking, loses the easy-flash section: Task 5 Step 4. Per-chip findings preserved as an appendix: Task 5 Step 2.

**Placeholder scan.** Task 5 Step 2 copies text by exact heading from named files rather than reproducing ~250 lines inline; the source files are in the working tree at execution time, so the instruction is fully determined. No "TBD"/"appropriate"/"similar to" anywhere.

**Type consistency.** `record_result(registry_path: Path, mac: str, now: datetime, result: str) -> Device` is the same in Task 1's interface block, implementation, tests, and Task 2's `record()` closure. `RESULTS` tuple order matches the README and example registry wording. Expected test counts: 150 passed → 158 (Task 1, +8) → 164 (Task 2, +6) → unchanged after, plus 1 skip throughout.
