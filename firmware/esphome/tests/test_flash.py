import os
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
    # Deliberately in the shell the build check rejects: registering compiles
    # nothing, so it has to work here.
    monkeypatch.setattr(flash.sys, "platform", "win32")
    monkeypatch.setenv("MSYSTEM", "MINGW64")
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
    monkeypatch.delenv("MSYSTEM", raising=False)  # not Git Bash
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
    monkeypatch.delenv("MSYSTEM", raising=False)  # not Git Bash
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
    monkeypatch.setattr(flash, "REGISTRY_PATH", reg)
    monkeypatch.setattr(flash, "SECRETS_PATH", sec)
    monkeypatch.setattr(flash, "HERE", tmp_path)
    monkeypatch.setattr(flash, "find_port", lambda explicit: "COM4")
    monkeypatch.setattr(flash, "read_mac", lambda port: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(flash, "build_env", lambda: {"PATH": "sentinel"})
    calls = []
    monkeypatch.setattr(flash.subprocess, "call", lambda cmd, **kw: calls.append(kw) or 0)

    flash.main([])

    assert calls[0]["env"] == {"PATH": "sentinel"}


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
