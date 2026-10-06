import os
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
import yaml

import flash


NOW = datetime(2026, 9, 28, 14, 7, 0)


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


def test_append_secrets_escapes_quotes_and_backslashes(tmp_path):
    path = tmp_path / "secrets.yaml"
    path.write_text("")
    flash.append_secrets(path, {"odd": 'say "hi" \\ bye'}, comment="odd")
    assert yaml.safe_load(path.read_text()) == {"odd": 'say "hi" \\ bye'}
    assert path.read_text() == '# odd\nodd: "say \\"hi\\" \\\\ bye"\n'


def test_missing_secrets_non_mapping_file_errors(tmp_path):
    path = tmp_path / "secrets.yaml"
    path.write_text("just a string")
    with pytest.raises(flash.FlashError, match="mapping"):
        flash.missing_secrets(path, ["a"])


def test_load_registry_malformed_yaml_errors(tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text("devices: [unclosed")
    with pytest.raises(flash.FlashError, match="not valid YAML"):
        flash.load_registry(path)


def test_load_registry_non_list_devices_errors(tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text("devices: nope")
    with pytest.raises(flash.FlashError, match="list"):
        flash.load_registry(path)


@pytest.mark.parametrize("text", [
    "",
    "devices:\n",
    "devices:\n- mac: 20:50:0D:17:F4:58\n  name: a\n  friendly_name: A\n  first_flashed: 2026-10-03\n",
    "devices: [unclosed",
    "devices: nope",
    "just a string",
])
def test_load_registry_text_matches_load_registry_on_the_same_content(tmp_path, text):
    # One place decides what a registry is. The archive reads its copy out of
    # the zip as text, with no temporary file anywhere outside the repository
    # and backup_dir(), so both callers have to agree exactly - including on
    # which FlashError the bad shapes raise.
    path = tmp_path / "devices.yaml"
    path.write_text(text)

    try:
        expected = flash.load_registry(path)
    except flash.FlashError as e:
        with pytest.raises(flash.FlashError) as got:
            flash.load_registry_text(text, "devices.yaml")
        assert str(got.value) == str(e)
    else:
        assert flash.load_registry_text(text, "devices.yaml") == expected


def test_load_registry_text_puts_the_label_in_its_errors():
    with pytest.raises(flash.FlashError, match="archived devices.yaml is not valid YAML"):
        flash.load_registry_text("devices: [unclosed", "archived devices.yaml")


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
        calls.append((cmd, kw))
        class R:
            returncode = 0
            stdout = "MAC: 20:50:0D:17:F4:58\n"
            stderr = ""
        return R()

    monkeypatch.setattr(flash.subprocess, "run", fake_run)
    assert flash.read_mac("COM4") == "20:50:0d:17:f4:58"
    cmd, kw = calls[0]
    assert cmd == [flash.sys.executable, "-m", "esptool", "--chip", "esp32", "--port", "COM4", "read-mac"]
    assert "--chip" in cmd
    assert cmd[cmd.index("--chip") + 1] == "esp32"
    assert kw == {"capture_output": True, "text": True}


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


def test_read_mac_rejects_wrong_chip(monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            returncode = 2
            stdout = ""
            stderr = "A fatal error occurred: This chip is ESP8266, not ESP32. Wrong chip argument?"
        return R()

    monkeypatch.setattr(flash.subprocess, "run", fake_run)
    with pytest.raises(flash.FlashError, match="not ESP32"):
        flash.read_mac("COM4")


# --- resolve_device ---------------------------------------------------------

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
    # Deliberately in the shell the build check rejects: registering compiles
    # nothing, so it has to work here.
    monkeypatch.setattr(flash.sys, "platform", "win32")
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    reg, sec = _layout(tmp_path)
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "backup")
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
    monkeypatch.delenv("MSYSTEM", raising=False)  # not Git Bash
    reg, sec = _layout(tmp_path)
    flash.resolve_device("aa:bb:cc:dd:ee:ff", NOW, reg, sec, tmp_path)
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "backup")
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    monkeypatch.setattr(flash, "HERE", tmp_path)
    monkeypatch.setattr(flash, "find_port", lambda explicit: "COM4")
    monkeypatch.setattr(flash, "read_mac", lambda port: "aa:bb:cc:dd:ee:ff")
    calls = []
    codes = iter([0, 7])
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: calls.append((cmd, kw)) or next(codes))

    rc = flash.main(["--no-logs"])

    assert rc == 7
    compile_cmd, compile_kw = calls[0]
    run_cmd, run_kw = calls[1]
    assert compile_cmd[:2] == [flash.sys.executable, "-m"]
    assert compile_cmd[2:] == ["esphome", "compile", "clock-20260928-1407.yaml"]
    assert run_cmd[:2] == [flash.sys.executable, "-m"]
    assert run_cmd[2:] == ["esphome", "run", "clock-20260928-1407.yaml", "--device", "COM4", "--no-logs"]
    assert compile_kw["cwd"] == tmp_path
    assert run_kw["cwd"] == tmp_path


def test_main_reports_flash_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MSYSTEM", raising=False)  # not Git Bash
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "backup")
    monkeypatch.setattr(flash, "find_port", lambda explicit: (_ for _ in ()).throw(flash.FlashError("No USB serial port found")))
    assert flash.main([]) == 1
    assert "No USB serial port found" in capsys.readouterr().err


# --- the Git Bash guard -----------------------------------------------------

# ESP-IDF's cmake and ninja steps do not run under MSYS: ESPHome reports a
# successful compile, writes no firmware, and the upload then fails with
# nothing to explain it. flash-clock.py refuses that shell, and so must this
# script, which the README documents as a direct entry point.

def test_main_refuses_a_git_bash_build_before_touching_the_port(monkeypatch, capsys):
    monkeypatch.setattr(flash.sys, "platform", "win32")
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.setattr(flash, "find_port", lambda explicit: pytest.fail("must not open a port"))

    assert flash.main([]) == 1
    assert "PowerShell" in capsys.readouterr().err


def test_check_shell_allows_a_real_windows_shell(monkeypatch):
    monkeypatch.setattr(flash.sys, "platform", "win32")
    monkeypatch.delenv("MSYSTEM", raising=False)
    flash.check_shell()


def test_check_shell_ignores_msystem_off_windows(monkeypatch):
    monkeypatch.setattr(flash.sys, "platform", "linux")
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    flash.check_shell()


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


REPO_DIR = Path(flash.__file__).resolve().parent


@pytest.mark.real_backup_dir
def test_backup_dir_is_outside_the_repository():
    # REPO_DIR, not flash.HERE: the autouse fixture redirects HERE for every
    # test including this one, and un-patching it here would reintroduce the
    # hazard that fixture exists for.
    assert flash.backup_dir().name == "HU-058_ESPHome"
    assert REPO_DIR not in flash.backup_dir().parents
    assert flash.backup_dir() != REPO_DIR


def test_the_suite_never_resolves_the_real_backup_dir():
    # The autouse fixture in conftest.py redirects backup_dir for every test.
    # If this ever points into the user's profile again, a test run can
    # overwrite their real clock registry backup with fixture data.
    import platformdirs

    real = Path(platformdirs.user_data_dir("HU-058_ESPHome", appauthor=False))

    assert flash.backup_dir() != real
    assert real not in flash.backup_dir().parents


def test_the_suite_never_points_at_the_real_local_data():
    # The other half of the autouse redirect. restore_local_data() writes
    # REGISTRY_PATH and SECRETS_PATH, so a test that calls it without
    # overriding them writes the user's live registry and secrets.
    assert flash.REGISTRY_PATH.parent != REPO_DIR
    assert flash.SECRETS_PATH.parent != REPO_DIR
    assert flash.HERE != REPO_DIR


@pytest.mark.real_backup_dir
def test_the_real_backup_dir_opt_out_still_redirects_the_working_tree():
    # The two protections are independent on purpose: wanting the real
    # backup_dir() for a read-only assertion is no reason to be handed the
    # real registry and secrets as well.
    assert flash.REGISTRY_PATH.parent != REPO_DIR
    assert flash.SECRETS_PATH.parent != REPO_DIR
    assert flash.HERE != REPO_DIR


def test_local_data_paths_follow_the_module_constants(tmp_path, monkeypatch):
    monkeypatch.setattr(flash, "REGISTRY_PATH", tmp_path / "devices.yaml")
    monkeypatch.setattr(flash, "SECRETS_PATH", tmp_path / "secrets.yaml")

    assert flash._local_data_paths() == {
        "devices.yaml": tmp_path / "devices.yaml",
        "secrets.yaml": tmp_path / "secrets.yaml",
    }
    assert tuple(flash._local_data_paths()) == flash.BACKUP_MEMBERS


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


ARCHIVED_REGISTRY = (
    "devices:\n"
    "- mac: 'aa:bb:cc:dd:ee:ff'\n"
    "  name: clock-x\n"
    "  friendly_name: Clock X\n"
    "  first_flashed: '2026-10-03'\n"
)
ARCHIVED_SECRETS = "wifi_ssid: net\napi_key_clock_x: archived-key\n"


def _set_asides(archive):
    """The quarantined copies of archives flash.py could not open, oldest first.

    A glob here and nowhere in flash.py on purpose: the tool looks up exactly
    backup_path(), so a sibling is inert to it and only the tests have to find
    them.
    """
    return sorted(archive.parent.glob(flash.ARCHIVE_NAME + ".unreadable-*.zip"))


def _dead_archive(path, text="not a zip file"):
    """An archive zipfile cannot open at all: no members, no central directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _archive_with_a_damaged_registry(path):
    """A zip whose central directory is fine and whose devices.yaml is not.

    Hand built rather than taken from backup_local_data(), and stored rather
    than deflated, so the registry's bytes sit in the file verbatim: patching
    them in place with the same number of bytes keeps every offset and length
    in the central directory honest, and only the member's CRC stops matching.
    That is the shape of real rot - the archive opens, lists its members, and
    then one read of one member fails.

    secrets.yaml is left intact, because the whole point is that an intact
    member must survive a damaged neighbour.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("README.md", "# HU-058 clock safety backup\n")
        z.writestr("devices.yaml", ARCHIVED_REGISTRY)
        z.writestr("secrets.yaml", ARCHIVED_SECRETS)
    marker = b"Clock X"  # only in the registry member
    raw = path.read_bytes()
    assert raw.count(marker) == 1, "the fixture has drifted: patch the registry, nothing else"
    i = raw.index(marker)
    path.write_bytes(raw[:i] + b"@" * len(marker) + raw[i + len(marker):])
    with zipfile.ZipFile(path) as z:
        assert sorted(z.namelist()) == ["README.md", "devices.yaml", "secrets.yaml"]
        assert z.read("secrets.yaml").decode() == ARCHIVED_SECRETS
        with pytest.raises(Exception):
            z.read("devices.yaml")
    return path


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
    # The guard fires on what the archive would lose, so gaining a file is fine.
    reg, _ = _local_data(tmp_path, monkeypatch, registry=False)
    flash.backup_local_data()
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "secrets.yaml"]

    flash.save_registry(reg, [flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03")])
    flash.backup_local_data()

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "devices.yaml", "secrets.yaml"]


def test_backup_keeps_the_old_archive_when_the_members_are_swapped(tmp_path, monkeypatch, capsys):
    # {devices.yaml} archived and only {secrets.yaml} to hand: the sets are
    # incomparable, so a proper-subset test would let this through.
    reg, sec = _local_data(tmp_path, monkeypatch, secrets=False)
    flash.backup_local_data()
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "devices.yaml"]

    reg.unlink()
    sec.write_text("wifi_ssid: net\n")
    flash.backup_local_data()

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert "devices.yaml" in z.namelist()
    assert "devices.yaml" in capsys.readouterr().err


def test_backup_skips_a_registry_truncated_mid_entry(tmp_path, monkeypatch, capsys):
    # Valid YAML, then KeyError out of load_registry. Must not escape.
    reg, _ = _local_data(tmp_path, monkeypatch)
    reg.write_text("devices:\n- mac: 20:50:0d:17:f4:58\n  name: wifi-clock\n")

    flash.backup_local_data()

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "secrets.yaml"]
    assert "devices.yaml" in capsys.readouterr().err


def test_backup_keeps_the_old_archive_when_the_registry_is_emptied(tmp_path, monkeypatch, capsys):
    # A zero-byte registry is valid YAML and loads as no clocks at all, so the
    # member set is unchanged and the parse probe passes it.
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    before = flash.backup_path().read_bytes()

    reg.write_text("")
    flash.backup_local_data()

    assert flash.backup_path().read_bytes() == before
    assert "no clocks" in capsys.readouterr().err


def test_archive_state_tells_an_empty_registry_from_an_unreadable_one(tmp_path, monkeypatch):
    # The distinction the overwrite guard below is built on. Collapsing both to
    # "nothing" disarms every comparison exactly when the archive is damaged.
    # The names, not the count: len() is the count, and the staleness guard
    # needs the names on any dropped clock rather than only a shrunk count.
    _local_data(tmp_path, monkeypatch)
    archive = flash.backup_path()

    assert flash._archive_state(archive) == (set(), [])

    flash.backup_local_data()
    assert flash._archive_state(archive) == ({"devices.yaml", "secrets.yaml"}, ["clock-x"])

    flash.REGISTRY_PATH.write_text("")
    archive.unlink()
    flash.backup_local_data()
    assert flash._archive_state(archive) == ({"devices.yaml", "secrets.yaml"}, [])

    _archive_with_a_damaged_registry(archive)
    assert flash._archive_state(archive) == ({"devices.yaml", "secrets.yaml"}, None)

    archive.write_text("not a zip file")
    assert flash._archive_state(archive) == (set(), None)


def test_backup_keeps_an_archive_whose_registry_cannot_be_read(tmp_path, monkeypatch, capsys):
    # The archive opens and lists both members, so the loss guard sees nothing
    # missing; only the read of devices.yaml fails. Reporting that as 0 clocks
    # let a one-clock registry overwrite an archive that may have held eleven,
    # with the keys Home Assistant has and nothing else does.
    _local_data(tmp_path, monkeypatch)
    _archive_with_a_damaged_registry(flash.backup_path())
    before = flash.backup_path().read_bytes()

    flash.backup_local_data()

    assert flash.backup_path().read_bytes() == before
    err = capsys.readouterr().err
    assert "keeping the existing safety backup" in err
    assert str(flash.backup_path()) in err
    assert "cannot be read" in err
    # Deliberately not set aside, unlike an archive that will not open at all.
    # The member set is known here, and it is what closes the loss: the archive
    # holds secrets.yaml, so a run with no secrets.yaml to hand is refused by
    # the loss guard. Quarantining it would turn that closed loss into an open
    # one - a one-member fresh archive, and the only copy of the keys inside a
    # file the tool cannot read.
    assert _set_asides(flash.backup_path()) == []


def test_backup_sets_aside_an_archive_that_will_not_open_at_all(tmp_path, monkeypatch, capsys):
    # The owner's ruling. Refusing forever left the safety backup disabled
    # until someone read stderr; a zip whose central directory will not parse
    # still holds every member's bytes, recoverable by hand, so the bytes are
    # moved to a name saying what they are and a fresh backup is written in
    # their place. The member set was already unknown, so setting it aside
    # costs no guard anything it did not already lack.
    _local_data(tmp_path, monkeypatch)
    _dead_archive(flash.backup_path())

    flash.backup_local_data()

    aside = _set_asides(flash.backup_path())
    assert len(aside) == 1
    assert "unreadable" in aside[0].name
    assert aside[0].read_text() == "not a zip file"
    # Renamed, not copied: at no instant are there two copies of a file full of
    # credentials, and a copy that ran out of disk would leave a truncated
    # duplicate for the write to then make the only one.
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "devices.yaml", "secrets.yaml"]
    # Both paths in the one warning, because a user looking for their backup at
    # the normal path has to be told where the old bytes went.
    err = capsys.readouterr().err.strip()
    assert err.count("warning:") == 1
    assert str(flash.backup_path()) in err
    assert str(aside[0]) in err


def test_backup_sets_aside_two_dead_archives_without_clobbering_either(tmp_path, monkeypatch):
    # A fixed set-aside name, or a second-resolution timestamp, loses the first
    # quarantined copy to the second. This project already has a documented
    # same-minute collision (resolve_device's "registered less than a minute
    # ago"), so the name is mkstemp-unique.
    _local_data(tmp_path, monkeypatch)
    _dead_archive(flash.backup_path(), "not a zip file")
    flash.backup_local_data()
    _dead_archive(flash.backup_path(), "also not a zip file")
    flash.backup_local_data()

    aside = _set_asides(flash.backup_path())
    assert len(aside) == 2
    assert sorted(p.read_text() for p in aside) == ["also not a zip file", "not a zip file"]


def test_backup_neither_sets_aside_nor_writes_a_dead_archive_with_no_clocks(tmp_path, monkeypatch, capsys):
    # The empty-registry guard runs before the set-aside, not after it: once
    # the dead archive is out of the way there is nothing left to compare
    # against, and a registry truncated to nothing would become the content of
    # the fresh archive.
    reg, _ = _local_data(tmp_path, monkeypatch)
    reg.write_text("")
    _dead_archive(flash.backup_path())

    flash.backup_local_data()

    assert flash.backup_path().read_text() == "not a zip file"
    assert _set_asides(flash.backup_path()) == []
    err = capsys.readouterr().err
    assert "keeping the existing safety backup" in err
    assert str(flash.backup_path()) in err


def test_backup_refuses_the_write_when_the_set_aside_fails(tmp_path, monkeypatch, capsys):
    # Falling through to the write after a failed set-aside is the original
    # data-loss bug with extra steps. The invariant is that some file in the
    # backup directory holds the dead bytes at every instant.
    _local_data(tmp_path, monkeypatch)
    _dead_archive(flash.backup_path())
    real_replace = flash.os.replace

    def refuse_the_set_aside(src, dst):
        if "unreadable" in str(dst):
            raise OSError("cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(flash.os, "replace", refuse_the_set_aside)

    flash.backup_local_data()

    assert flash.backup_path().read_text() == "not a zip file"
    err = capsys.readouterr().err
    assert "keeping the existing safety backup" in err
    assert str(flash.backup_path()) in err
    # S2: mkstemp's empty set-aside file must not survive a replace that
    # failed on it - a stray *.unreadable-*.zip with nothing inside invites
    # the README's "unzip what you can from it" and finds nothing, which
    # reads as lost bytes rather than a file that was never written to.
    assert _set_asides(flash.backup_path()) == []


def test_a_set_aside_that_outlives_a_failed_write_is_named_in_the_warning(tmp_path, monkeypatch, capsys):
    # The acceptable half-way state: the bytes are quarantined and no fresh
    # archive got written, so backup_path() is simply absent. A user told only
    # "could not write the safety backup" would go looking at the normal path
    # and find nothing at all.
    _local_data(tmp_path, monkeypatch)
    _dead_archive(flash.backup_path())
    real_replace = flash.os.replace

    def refuse_the_archive(src, dst):
        if str(dst) == str(flash.backup_path()):
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(flash.os, "replace", refuse_the_archive)

    flash.backup_local_data()

    aside = _set_asides(flash.backup_path())
    assert len(aside) == 1
    assert aside[0].read_text() == "not a zip file"
    assert not flash.backup_path().exists()
    err = capsys.readouterr().err
    # That line, not merely somewhere in the output: the set-aside warning
    # above it already names the path, and it is the write failure the user
    # will be reading when they go looking.
    failure = [line for line in err.splitlines() if "could not write the safety backup" in line]
    assert len(failure) == 1
    assert str(aside[0]) in failure[0]


def test_the_keep_the_old_archive_warnings_name_the_archive(tmp_path, monkeypatch, capsys):
    # A user told "keeping the existing safety backup" has nothing to go look
    # at unless the message says where it is. restore_local_data() already
    # names it.
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()

    reg.unlink()
    flash.backup_local_data()
    assert str(flash.backup_path()) in capsys.readouterr().err

    reg.write_text("")
    flash.backup_local_data()
    assert str(flash.backup_path()) in capsys.readouterr().err


def test_backup_writes_a_shorter_registry_and_warns(tmp_path, monkeypatch, capsys):
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.save_registry(reg, [
        flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03"),
        flash.Device("11:22:33:44:55:66", "clock-y", "Clock Y", "2026-10-03"),
    ])
    flash.backup_local_data()

    flash.save_registry(reg, [flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03")])
    flash.backup_local_data()

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert "clock-y" not in z.read("devices.yaml").decode()
    assert "down to 1 from 2 clocks" in capsys.readouterr().err


# The shrink allowance above is reachable without anyone editing anything.
# `git checkout <a commit from before devices.yaml was untracked>` silently
# overwrites the gitignored registry with the older, shorter tracked content,
# exit 0 and no output. The restore then skips the file because it exists, the
# member sets match, the count is merely lower - and the warning above would
# replace the archive with the stale registry. secrets.yaml is the witness
# that tells the two apart: git never tracked it, so a rollback leaves it
# holding the keys of every clock the rollback dropped.

def _three_clocks():
    return [
        flash.Device("aa:bb:cc:dd:ee:ff", "clock-x", "Clock X", "2026-10-03"),
        flash.Device("11:22:33:44:55:66", "clock-y", "Clock Y", "2026-10-03"),
        flash.Device("22:33:44:55:66:77", "clock-z", "Clock Z", "2026-10-03"),
    ]


def _secrets_for(sec, names):
    lines = ["wifi_ssid: net"]
    for name in names:
        api_key_name, ota_password_name = flash.secret_names(name)
        lines += [f"{api_key_name}: key-{name}", f"{ota_password_name}: ota-{name}"]
    sec.write_text("\n".join(lines) + "\n")


def test_backup_keeps_the_old_archive_when_a_checkout_rolled_the_registry_back(tmp_path, monkeypatch, capsys):
    reg, sec = _local_data(tmp_path, monkeypatch)
    clocks = _three_clocks()
    flash.save_registry(reg, clocks)
    _secrets_for(sec, [d.name for d in clocks])
    flash.backup_local_data()
    before = flash.backup_path().read_bytes()
    capsys.readouterr()

    # What `git checkout <old commit>` does: the shorter tracked registry
    # lands on top of the gitignored one, and secrets.yaml is left alone.
    flash.save_registry(reg, clocks[:1])
    flash.backup_local_data()

    assert flash.backup_path().read_bytes() == before
    err = capsys.readouterr().err
    assert "keeping the existing safety backup" in err
    assert str(flash.backup_path()) in err
    assert "clock-y" in err
    assert "clock-z" in err
    assert "rolled back" in err
    # The shrink warning claims it is backing up the shorter registry, which
    # would be a lie on this path.
    assert "backing up the shorter registry" not in err


def test_backup_writes_a_shorter_registry_when_the_dropped_clocks_secrets_went_too(tmp_path, monkeypatch, capsys):
    # The legitimate hand edit the allowance exists for: the scrapped board's
    # entry and its keys both go, so there is no witness left behind.
    reg, sec = _local_data(tmp_path, monkeypatch)
    clocks = _three_clocks()
    flash.save_registry(reg, clocks)
    _secrets_for(sec, [d.name for d in clocks])
    flash.backup_local_data()
    capsys.readouterr()

    flash.save_registry(reg, clocks[:2])
    _secrets_for(sec, ["clock-x", "clock-y"])
    flash.backup_local_data()

    err = capsys.readouterr().err
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert "clock-z" not in z.read("devices.yaml").decode()
    assert "down to 2 from 3 clocks" in err
    assert "rolled back" not in err


def test_backup_keeps_the_old_archive_when_a_drop_hides_behind_two_new_clocks(tmp_path, monkeypatch, capsys):
    # The guard is top level now, so it asks about dropped names whatever the
    # count did - including a count that went up, which the old nesting could
    # not see either: 2 archived against 3 here is not a shrink. The clock
    # that went missing is no less missing for having been outnumbered.
    reg, sec = _local_data(tmp_path, monkeypatch)
    clocks = _three_clocks()
    extra = flash.Device("33:44:55:66:77:88", "clock-w", "Clock W", "2026-10-03")
    flash.save_registry(reg, clocks[:2])
    _secrets_for(sec, [d.name for d in clocks])
    flash.backup_local_data()
    before = flash.backup_path().read_bytes()
    capsys.readouterr()

    flash.save_registry(reg, [clocks[0], clocks[2], extra])
    flash.backup_local_data()

    assert flash.backup_path().read_bytes() == before
    err = capsys.readouterr().err
    assert "keeping the existing safety backup" in err
    assert "clock-y" in err
    assert "rolled back" in err
    # Nothing shrank, so the shrink wording must stay out of it.
    assert "down to" not in err


def test_the_rollback_guard_is_silent_when_the_registry_grows_or_holds(tmp_path, monkeypatch, capsys):
    # Silent here because nothing was dropped - the guard fires on a name the
    # archive holds and the registry does not, and there is no such name to
    # testify about. clock-z is keyed in secrets throughout and never in the
    # registry until the last step, which is an orphaned api_key_* on its own
    # and, on purpose, not what the guard is watching for.
    reg, sec = _local_data(tmp_path, monkeypatch)
    clocks = _three_clocks()
    flash.save_registry(reg, clocks[:2])
    _secrets_for(sec, [d.name for d in clocks])
    flash.backup_local_data()
    capsys.readouterr()

    flash.backup_local_data()            # same count
    flash.save_registry(reg, clocks)     # grown
    flash.backup_local_data()

    assert capsys.readouterr().err == ""
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert "clock-z" in z.read("devices.yaml").decode()


def test_backup_keeps_the_old_archive_when_a_rollback_kept_the_clock_count(tmp_path, monkeypatch, capsys):
    # Found by the whole-branch review. The guard used to be nested inside the
    # shrink warning, so a substitution that kept the count the same never
    # reached it - and that fall-through wrote the archive with no guard and no
    # warning at all, more quietly than the shrink it was guarding.
    #
    # No fault required: a scrapped clock-y was removed by hand and a new
    # clock-z registered, so the registry and the archive hold {x, z} while
    # secrets.yaml still keys x, y and z - nothing removes keys automatically.
    # A checkout of a commit from before devices.yaml was untracked then puts
    # {x, y} back in place. Same count, so the shrink comparison says nothing,
    # and the archive used to become {x, y} - losing clock-z from both copies
    # silently, and handing it a second identity on its next flash.
    reg, sec = _local_data(tmp_path, monkeypatch)
    clocks = _three_clocks()
    flash.save_registry(reg, [clocks[0], clocks[2]])
    _secrets_for(sec, [d.name for d in clocks])
    flash.backup_local_data()
    before = flash.backup_path().read_bytes()
    capsys.readouterr()

    flash.save_registry(reg, clocks[:2])
    flash.backup_local_data()

    assert flash.backup_path().read_bytes() == before
    err = capsys.readouterr().err
    assert "keeping the existing safety backup" in err
    assert str(flash.backup_path()) in err
    assert "clock-z" in err
    assert "rolled back" in err
    # Nothing shrank, so claiming a shorter registry would be a lie here too.
    assert "backing up the shorter registry" not in err


def test_backup_writes_a_count_neutral_edit_when_the_dropped_clocks_secrets_went_too(tmp_path, monkeypatch, capsys):
    # The legitimate shape of the same count change: clock-y was scrapped by
    # hand, its api_key_* and ota_password_* went with it, and clock-z was
    # registered. No witness left behind, so no guard, and - unlike the shrink
    # allowance, which has a count to report - nothing worth saying either.
    reg, sec = _local_data(tmp_path, monkeypatch)
    clocks = _three_clocks()
    flash.save_registry(reg, clocks[:2])
    _secrets_for(sec, ["clock-x", "clock-y"])
    flash.backup_local_data()
    capsys.readouterr()

    flash.save_registry(reg, [clocks[0], clocks[2]])
    _secrets_for(sec, ["clock-x", "clock-z"])
    flash.backup_local_data()

    assert capsys.readouterr().err == ""
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert "clock-z" in z.read("devices.yaml").decode()
        assert "clock-y" not in z.read("devices.yaml").decode()


def test_the_rollback_guard_does_not_block_a_shrink_when_secrets_are_absent(tmp_path, monkeypatch, capsys):
    # The archive must never have held secrets.yaml, or the loss guard returns
    # first and this would pass without the new guard ever running.
    reg, _ = _local_data(tmp_path, monkeypatch, secrets=False)
    clocks = _three_clocks()
    flash.save_registry(reg, clocks)
    flash.backup_local_data()
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "devices.yaml"]
    capsys.readouterr()

    flash.save_registry(reg, clocks[:1])
    flash.backup_local_data()

    err = capsys.readouterr().err
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert "clock-y" not in z.read("devices.yaml").decode()
    assert "down to 1 from 3 clocks" in err
    assert "rolled back" not in err


def test_the_rollback_guard_does_not_block_a_shrink_on_unreadable_secrets(tmp_path, monkeypatch, capsys):
    # secrets.yaml that will not parse is skipped from the candidates with its
    # own warning, and must not also be grounds for refusing the backup: the
    # witness cannot be read, so it cannot testify either way.
    reg, sec = _local_data(tmp_path, monkeypatch, secrets=False)
    clocks = _three_clocks()
    flash.save_registry(reg, clocks)
    flash.backup_local_data()
    capsys.readouterr()

    flash.save_registry(reg, clocks[:1])
    sec.write_text("key: [unclosed\n")
    flash.backup_local_data()

    err = capsys.readouterr().err
    with zipfile.ZipFile(flash.backup_path()) as z:
        assert "clock-y" not in z.read("devices.yaml").decode()
    assert "down to 1 from 3 clocks" in err
    assert "rolled back" not in err


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes; Windows uses the profile ACL")
def test_backup_is_readable_only_by_its_owner(tmp_path, monkeypatch):
    _local_data(tmp_path, monkeypatch)

    flash.backup_local_data()

    assert flash.backup_path().stat().st_mode & 0o777 == 0o600
    assert flash.backup_path().parent.stat().st_mode & 0o777 == 0o700


def test_backup_asks_for_owner_only_permissions_on_every_host(tmp_path, monkeypatch):
    # The POSIX test above is skipped on Windows, and tmp_path is always made
    # fresh, so neither covers tightening a directory that was already there
    # with a looser mode. Assert the calls instead: the intent is then
    # exercised wherever the suite runs.
    _local_data(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(flash.os, "chmod", lambda path, mode: calls.append((Path(path), mode)))

    flash.backup_local_data()

    assert (flash.backup_path(), 0o600) in calls
    assert (flash.backup_path().parent, 0o700) in calls


def test_backup_tightens_a_directory_that_already_exists(tmp_path, monkeypatch):
    # mkdir(mode=...) does nothing for a directory that is already there, so
    # the chmod is the only thing that reaches one left loose by an earlier
    # version or by the user's umask.
    _local_data(tmp_path, monkeypatch)
    flash.backup_path().parent.mkdir(parents=True, exist_ok=True)
    calls = []
    monkeypatch.setattr(flash.os, "chmod", lambda path, mode: calls.append((Path(path), mode)))

    flash.backup_local_data()

    assert (flash.backup_path().parent, 0o700) in calls


def test_backup_survives_a_chmod_it_cannot_do_and_says_so_separately(tmp_path, monkeypatch, capsys):
    # The archive is written and correct by the time the chmod runs. Reporting
    # "could not write the safety backup" would send the user looking for a
    # file that is sitting right there.
    _local_data(tmp_path, monkeypatch)
    monkeypatch.setattr(
        flash.os, "chmod", lambda path, mode: (_ for _ in ()).throw(OSError("not supported"))
    )

    flash.backup_local_data()

    with zipfile.ZipFile(flash.backup_path()) as z:
        assert sorted(z.namelist()) == ["README.md", "devices.yaml", "secrets.yaml"]
    err = capsys.readouterr().err
    assert "could not write the safety backup" not in err
    assert "could not restrict it to your account" in err


def test_backup_leaves_no_temporary_file_behind(tmp_path, monkeypatch):
    _local_data(tmp_path, monkeypatch)

    flash.backup_local_data()

    assert [p.name for p in flash.backup_path().parent.iterdir()] == [flash.ARCHIVE_NAME]


def test_backup_writes_through_a_temporary_file_named_for_this_run_alone(tmp_path, monkeypatch):
    # Two terminals share this directory by design - a --port COM4 beside a
    # --port COM7, or a --register-only beside a flash. A fixed temporary name
    # has both runs writing one file, and whichever replace lands last
    # installs the interleaving as the archive.
    _local_data(tmp_path, monkeypatch)
    real_replace = flash.os.replace
    seen = []
    monkeypatch.setattr(
        flash.os, "replace", lambda src, dst: seen.append(Path(src)) or real_replace(src, dst)
    )

    flash.backup_local_data()
    flash.backup_local_data()

    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert flash.ARCHIVE_NAME + ".tmp" not in [p.name for p in seen]


def test_backup_warns_and_does_not_raise_when_it_cannot_write(tmp_path, monkeypatch, capsys):
    _local_data(tmp_path, monkeypatch)
    blocker = tmp_path / "backup"
    blocker.write_text("")  # a file where the directory needs to be

    flash.backup_local_data()

    assert "warning" in capsys.readouterr().err


def test_backup_warns_and_does_not_raise_when_the_backup_dir_cannot_be_resolved(tmp_path, monkeypatch, capsys):
    # backup_path() is inside the guard too: a flash must survive anything
    # platformdirs does, not just an unwritable directory.
    _local_data(tmp_path, monkeypatch)
    monkeypatch.setattr(flash, "backup_dir", lambda: (_ for _ in ()).throw(RuntimeError("no data dir")))

    flash.backup_local_data()

    assert "no data dir" in capsys.readouterr().err


def test_restore_puts_back_a_missing_registry(tmp_path, monkeypatch, capsys):
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    reg.unlink()

    flash.restore_local_data()

    assert len(flash.load_registry(reg)) == 1
    out = capsys.readouterr().out
    assert "Restored devices.yaml" in out
    # Not "(1 clock)" verbatim: the archive's age now rides in the same
    # parenthetical (test_restore_names_the_archives_age below), so this only
    # pins the count.
    assert "1 clock" in out
    assert str(flash.backup_path()) in out


def test_restore_names_the_archives_age(tmp_path, monkeypatch, capsys):
    # S1: the rollback guard in backup_local_data() can leave the archive
    # unwritten for weeks while new clocks keep getting registered. A restore
    # that only says what it put back, never how old it is, hides exactly
    # that - so the message has to carry the archive's own write date.
    reg, sec = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    old = datetime(2026, 8, 14, 12, 0, 0).timestamp()
    os.utime(flash.backup_path(), (old, old))
    reg.unlink()
    sec.unlink()

    flash.restore_local_data()

    out = capsys.readouterr().out
    assert out.count("written 2026-08-14") == 2  # devices.yaml and secrets.yaml


def test_archive_written_degrades_to_none_when_stat_fails(tmp_path, monkeypatch):
    # The unit-level half of S1's robustness requirement: stat() failing must
    # not raise out of the helper, only degrade to "no date to show".
    archive = tmp_path / "archive.zip"
    archive.write_text("not a real zip, stat still has to work on it")
    monkeypatch.setattr(
        Path, "stat", lambda self, *a, **k: (_ for _ in ()).throw(OSError("denied"))
    )

    assert flash._archive_written(archive) is None


def test_restore_still_reports_the_restore_when_the_archives_date_is_unreadable(
    tmp_path, monkeypatch, capsys
):
    # The integration-level half: restore_local_data() must not raise or lose
    # the restored-files report just because the archive's age could not be
    # read. A counting patch lets the one stat() call restore_local_data()
    # itself needs (archive.exists()) through, and fails only the later one
    # _archive_written() makes - proving the degrade path is actually taken
    # rather than the function returning early with nothing restored at all.
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    reg.unlink()
    archive = flash.backup_path()
    real_stat = Path.stat
    calls = {"n": 0}

    def flaky_stat(self, *a, **kw):
        if self == archive:
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError("denied")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    flash.restore_local_data()

    assert len(flash.load_registry(reg)) == 1
    out = capsys.readouterr().out
    assert "Restored devices.yaml" in out
    assert "1 clock" in out
    assert "written" not in out
    assert calls["n"] > 1  # the degraded path, not a vacuous early return


def test_the_keep_the_old_archive_warnings_name_the_archives_age(tmp_path, monkeypatch, capsys):
    # One of the four refusals the date was mirrored into - the loss guard.
    # The other three (central-directory-parsed-but-damaged-member, the
    # registry-emptied guard, and the rollback guard) share the same
    # target_desc plumbing, so this is the representative case.
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    old = datetime(2026, 8, 14, 12, 0, 0).timestamp()
    os.utime(flash.backup_path(), (old, old))

    reg.unlink()
    flash.backup_local_data()

    err = capsys.readouterr().err
    assert "keeping the existing safety backup" in err
    assert str(flash.backup_path()) in err
    assert "written 2026-08-14" in err


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


def test_restore_leaves_no_temporary_file_behind(tmp_path, monkeypatch):
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    reg.unlink()

    flash.restore_local_data()

    assert reg.exists()
    assert not [f for f in reg.parent.iterdir() if ".restoring" in f.name]


def test_restore_writes_through_a_temporary_file_named_for_this_run_alone(tmp_path, monkeypatch):
    # Same reason as the archive's: two runs in two terminals, one fixed name,
    # one interleaved file installed as the registry.
    reg, sec = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    reg.unlink()
    sec.unlink()
    real_replace = flash.os.replace
    seen = []
    monkeypatch.setattr(
        flash.os, "replace", lambda src, dst: seen.append(Path(src)) or real_replace(src, dst)
    )

    flash.restore_local_data()

    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert {reg.name + ".restoring", sec.name + ".restoring"}.isdisjoint(p.name for p in seen)


def test_restore_recovers_an_intact_member_past_a_damaged_one(tmp_path, monkeypatch, capsys):
    # The loss sequence this exists to stop: devices.yaml is attempted first,
    # so one bad CRC on the registry used to end the restore and hide an
    # intact secrets.yaml behind it. The flash then minted a new identity,
    # wrote a one-clock secrets.yaml, and the next backup replaced the archive
    # that still held every old key - keys Home Assistant has and that cannot
    # be regenerated.
    reg, sec = _local_data(tmp_path, monkeypatch)
    _archive_with_a_damaged_registry(flash.backup_path())
    reg.unlink()
    sec.unlink()

    flash.restore_local_data()

    assert not reg.exists()
    assert sec.read_text() == ARCHIVED_SECRETS
    out = capsys.readouterr()
    assert "Restored secrets.yaml" in out.out
    assert "Restored devices.yaml" not in out.out
    assert "devices.yaml" in out.err
    assert str(flash.backup_path()) in out.err
    # Nothing half written and no temporary file for the next run to trip on.
    assert not [f for f in tmp_path.iterdir() if ".restoring" in f.name]


def test_restore_cleans_up_when_the_move_into_place_fails(tmp_path, monkeypatch, capsys):
    # The invariant the temporary file exists for. A partial devices.yaml left
    # at the destination is a file that exists, which restore refuses to
    # overwrite: automatic recovery would be blocked for good.
    reg, _ = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    reg.unlink()
    monkeypatch.setattr(
        flash.os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("denied"))
    )

    flash.restore_local_data()

    assert not reg.exists()
    assert not [f for f in tmp_path.iterdir() if ".restoring" in f.name]
    err = capsys.readouterr().err
    assert "could not restore devices.yaml" in err
    assert "denied" in err


def test_restore_reports_only_what_it_actually_put_back(tmp_path, monkeypatch, capsys):
    # restored used to be initialised inside the guarded block and rebound by
    # the loop, so a run that landed one file and then failed reported either
    # nothing or the wrong thing.
    reg, sec = _local_data(tmp_path, monkeypatch)
    flash.backup_local_data()
    reg.unlink()
    sec.unlink()
    real_replace = flash.os.replace

    def replace_once(src, dst):
        if Path(dst) == sec:
            raise OSError("denied")
        return real_replace(src, dst)

    monkeypatch.setattr(flash.os, "replace", replace_once)

    flash.restore_local_data()

    out = capsys.readouterr()
    assert "Restored devices.yaml" in out.out
    assert "Restored secrets.yaml" not in out.out
    assert "could not restore secrets.yaml" in out.err
    assert reg.exists()
    assert not sec.exists()


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


def test_restore_warns_and_does_not_raise_when_the_backup_dir_cannot_be_resolved(tmp_path, monkeypatch, capsys):
    # backup_path() is inside the guard here too, as it is for the backup: a
    # flash must survive anything platformdirs does, and this one runs before
    # the registry is read, so raising would stop the flash dead.
    reg, _ = _local_data(tmp_path, monkeypatch)
    reg.unlink()
    monkeypatch.setattr(flash, "backup_dir", lambda: (_ for _ in ()).throw(RuntimeError("no data dir")))

    flash.restore_local_data()

    assert "no data dir" in capsys.readouterr().err
    assert not reg.exists()


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


# --- the build cache --------------------------------------------------------

# ESPHome installs ccache with the ESP-IDF tools but resolves whether to use it
# against this process's PATH, not the build's, so its own ccache is invisible
# to its own probe and every new MAC recompiles the framework from scratch.
# Putting the directory on the PATH we hand the subprocess is what turns it on.

def _fake_ccache_install(root: Path, version: str, mtime: float) -> Path:
    exe = root / "tools" / "ccache" / version / f"ccache-{version}-windows-x86_64" / "ccache.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("")
    os.utime(exe, (mtime, mtime))
    return exe.parent


def test_find_ccache_dir_picks_the_newest_install(tmp_path, monkeypatch):
    monkeypatch.setenv("ESPHOME_ESP_IDF_PREFIX", str(tmp_path))
    old = _fake_ccache_install(tmp_path, "4.9", mtime=1_000_000)
    new = _fake_ccache_install(tmp_path, "4.12.1", mtime=2_000_000)

    # By name "4.9" sorts after "4.12.1"; by mtime the newer install wins.
    assert sorted([old.parent.name, new.parent.name])[-1] == "4.9"
    assert flash.find_ccache_dir() == new.resolve()


def test_find_ccache_dir_without_an_install_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("ESPHOME_ESP_IDF_PREFIX", str(tmp_path))
    assert flash.find_ccache_dir() is None


# The prefix override is ESPHome's, and ESPHome normalizes it before using it.
# Resolving it here by hand would let the build install ccache in one place and
# this lookup go searching in another, which silently costs the whole cache.

def test_find_ccache_dir_expands_a_tilde_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("ESPHOME_ESP_IDF_PREFIX", "~/idf")
    ccache_dir = _fake_ccache_install(tmp_path / "idf", "4.12.1", mtime=2_000_000)

    assert flash.find_ccache_dir() == ccache_dir.resolve()


def test_find_ccache_dir_ignores_whitespace_around_the_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("ESPHOME_ESP_IDF_PREFIX", f"  {tmp_path}	")
    ccache_dir = _fake_ccache_install(tmp_path, "4.12.1", mtime=2_000_000)

    assert flash.find_ccache_dir() == ccache_dir.resolve()


def test_find_ccache_dir_reads_a_blank_prefix_as_unset(tmp_path, monkeypatch):
    # Path("") is the CWD, which is both wrong and what esphome clean-all would
    # delete, so a blank override has to fall back to the machine-global dir.
    import platformdirs

    monkeypatch.setattr(platformdirs, "user_cache_dir", lambda *a, **kw: str(tmp_path))
    ccache_dir = _fake_ccache_install(tmp_path / "idf", "4.12.1", mtime=2_000_000)

    monkeypatch.setenv("ESPHOME_ESP_IDF_PREFIX", "   ")
    assert flash.find_ccache_dir() == ccache_dir.resolve()

    monkeypatch.delenv("ESPHOME_ESP_IDF_PREFIX")
    assert flash.find_ccache_dir() == ccache_dir.resolve()


def test_build_env_puts_ccache_first_on_the_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ESPHOME_ESP_IDF_PREFIX", str(tmp_path))
    monkeypatch.delenv("CCACHE_MAXSIZE", raising=False)
    ccache_dir = _fake_ccache_install(tmp_path, "4.12.1", mtime=2_000_000)

    env = flash.build_env()

    assert env["PATH"].split(os.pathsep)[0] == str(ccache_dir)
    assert env["CCACHE_MAXSIZE"] == "20G"


def test_build_env_keeps_a_maxsize_the_user_set(tmp_path, monkeypatch):
    monkeypatch.setenv("ESPHOME_ESP_IDF_PREFIX", str(tmp_path))
    monkeypatch.setenv("CCACHE_MAXSIZE", "2G")
    _fake_ccache_install(tmp_path, "4.12.1", mtime=2_000_000)

    assert flash.build_env()["CCACHE_MAXSIZE"] == "2G"


def test_build_env_without_ccache_is_the_plain_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ESPHOME_ESP_IDF_PREFIX", str(tmp_path))
    monkeypatch.delenv("CCACHE_MAXSIZE", raising=False)

    env = flash.build_env()

    assert env == dict(os.environ)
    assert "CCACHE_MAXSIZE" not in env


def test_main_hands_the_build_env_to_esphome(tmp_path, monkeypatch):
    monkeypatch.delenv("MSYSTEM", raising=False)  # not Git Bash
    reg, sec = _layout(tmp_path)
    flash.resolve_device("aa:bb:cc:dd:ee:ff", NOW, reg, sec, tmp_path)
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "backup")
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    monkeypatch.setattr(flash, "HERE", tmp_path)
    monkeypatch.setattr(flash, "find_port", lambda explicit: "COM4")
    monkeypatch.setattr(flash, "read_mac", lambda port: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(flash, "build_env", lambda: {"PATH": "sentinel"})
    calls = []
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: calls.append(kw) or 0)

    flash.main([])

    assert [c["env"] for c in calls] == [{"PATH": "sentinel"}, {"PATH": "sentinel"}]


# --- the example registry ---------------------------------------------------

# devices.yaml is gitignored, so devices.yaml.example is the only version of
# the registry a reader ever sees. It is hand written and nothing else loads
# it, which is exactly how an example drifts into a shape flash.py rejects.

EXAMPLE_REGISTRY = Path(__file__).resolve().parents[1] / "devices.yaml.example"


def test_the_example_registry_still_loads():
    devices = flash.load_registry(EXAMPLE_REGISTRY)
    assert [d.name for d in devices] == ["wifi-clock", "clock-20260929-0912"]
    assert all(flash.normalize_mac(d.mac) == d.mac for d in devices)


def test_a_missing_registry_is_empty_not_an_error(tmp_path):
    # What a fresh clone has, now that the real registry is not committed.
    assert flash.load_registry(tmp_path / "devices.yaml") == []
