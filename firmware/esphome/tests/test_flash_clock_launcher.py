"""Tests for the root launcher, ../../../flash-clock.py.

Only the part that writes a file is covered. secrets.yaml holds the API keys
and OTA passwords of every clock already paired with Home Assistant, and
flash.py will not regenerate them, so a launcher bug there costs you a trip
inside every case. The launcher is loaded by path because it lives outside
this package, at the repository root, where it has no import name.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

LAUNCHER_PATH = Path(__file__).resolve().parents[3] / "flash-clock.py"


@pytest.fixture(scope="module")
def launcher():
    spec = importlib.util.spec_from_file_location("flash_clock", LAUNCHER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AWKWARD = {
    "wifi_ssid": 'Net "Guest" #1',
    "wifi_password": r'p@ss\with"quote#hash',
    "ap_password": "a: b",
    "timezone": "America/Louisville",
}


def test_values_survive_quoting(launcher):
    """A password with a quote, a backslash or a '#' reads back unchanged."""
    assert yaml.safe_load(launcher.render_secrets(AWKWARD)) == AWKWARD


def test_no_per_device_placeholders(launcher):
    """Only the four shared keys. The example's api_key_wifi_clock is a real
    registered clock's entry, and an invalid placeholder would break it."""
    assert set(yaml.safe_load(launcher.render_secrets(AWKWARD))) == {
        "wifi_ssid",
        "wifi_password",
        "ap_password",
        "timezone",
    }


def test_never_overwrites_an_existing_secrets_file(launcher, tmp_path):
    path = tmp_path / "secrets.yaml"
    path.write_text("api_key_wifi_clock: irreplaceable\n")
    with pytest.raises(launcher.LauncherError):
        launcher.write_secrets(path, AWKWARD)
    assert path.read_text() == "api_key_wifi_clock: irreplaceable\n"


def test_review_pass_edits_one_value(launcher):
    """Four answers, then '4' to amend the timezone, then Enter to accept."""
    answers = iter(["MyNet", "pw", "appw", "", "4", "Etc/GMT+5", ""])
    values = launcher.prompt_values(ask=lambda _: next(answers), out=lambda *_: None)
    assert values == {
        "wifi_ssid": "MyNet",
        "wifi_password": "pw",
        "ap_password": "appw",
        "timezone": "Etc/GMT+5",
    }


def test_empty_answer_is_refused_unless_there_is_a_default(launcher):
    """Enter on the SSID asks again; Enter on the timezone takes Etc/UTC."""
    answers = iter(["", "MyNet", "pw", "appw", "", ""])
    values = launcher.prompt_values(ask=lambda _: next(answers), out=lambda *_: None)
    assert values["wifi_ssid"] == "MyNet"
    assert values["timezone"] == "Etc/UTC"


def test_credential_whitespace_is_taken_as_typed(launcher):
    """A WPA passphrase may begin or end with a space, so nothing is trimmed.

    Only a completely empty answer counts as not given, which is why the
    bare-space answers below are kept rather than triggering the default.
    """
    answers = iter(["  Guest Net ", " pad ", " ", "   ", ""])
    values = launcher.prompt_values(ask=lambda _: next(answers), out=lambda *_: None)
    assert values == {
        "wifi_ssid": "  Guest Net ",
        "wifi_password": " pad ",
        "ap_password": " ",
        "timezone": "   ",
    }
    assert yaml.safe_load(launcher.render_secrets(values)) == values


def test_review_listing_quotes_values_so_stray_spaces_show(launcher):
    answers = iter(["net", " pad ", "ap", "", ""])
    shown: list[str] = []
    launcher.prompt_values(ask=lambda _: next(answers), out=shown.append)
    assert any('2. WiFi password: " pad "' in line for line in shown), shown


def test_a_failed_write_is_reported_not_raised_raw(launcher, tmp_path, monkeypatch):
    """A full disk after the exclusive create must not reach the user as a
    traceback, and must not leave the half-written file behind."""
    path = tmp_path / "secrets.yaml"
    real_open = open

    def failing_open(*args, **kwargs):
        handle = real_open(*args, **kwargs)

        class Failing:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                handle.close()
                return False

            def write(self, text):
                handle.write(text[:20])
                raise OSError(28, "No space left on device")

        return Failing()

    monkeypatch.setitem(launcher.__builtins__, "open", failing_open)
    with pytest.raises(launcher.LauncherError, match="Could not write"):
        launcher.write_secrets(path, AWKWARD)
    assert not path.exists()


# --- the Git Bash guard -----------------------------------------------------

# Only the ESP-IDF compile is broken in that shell, so only a run that
# compiles is refused. flash.py takes --register-only through argparse, which
# accepts abbreviations, and this launcher has to recognise the same spellings
# or it would refuse a run that never builds.

@pytest.mark.parametrize("arg", ["--register-only", "--register", "--reg", "--r"])
def test_register_only_is_recognised_however_it_is_abbreviated(launcher, arg):
    assert launcher.is_register_only(arg)


@pytest.mark.parametrize("arg", ["--port", "--no-logs", "--", "-r", "COM7"])
def test_other_arguments_are_not_register_only(launcher, arg):
    assert not launcher.is_register_only(arg)


@pytest.fixture
def no_handoff(launcher, monkeypatch):
    """Stop main() after the preflight, and record whether it got there."""
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.setattr(launcher, "check_layout", lambda: None)
    monkeypatch.setattr(launcher, "find_uv", lambda: "uv")
    monkeypatch.setattr(launcher, "ensure_secrets", lambda: None)
    reached = []
    monkeypatch.setattr(launcher, "run", lambda uv, args, sync=True: reached.append(args) or 0)
    return reached


def test_a_build_is_refused_under_git_bash(launcher, no_handoff, capsys):
    assert launcher.main(["--no-logs"]) == 1
    assert "PowerShell" in capsys.readouterr().err
    assert no_handoff == []


def test_register_only_is_allowed_under_git_bash(launcher, no_handoff):
    assert launcher.main(["--register-only"]) == 0
    assert no_handoff == [["--register-only"]]


def test_help_is_allowed_under_git_bash(launcher, no_handoff, capsys):
    assert launcher.main(["--help"]) == 0
    assert no_handoff == [["--help"]]
