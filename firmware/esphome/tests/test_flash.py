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
