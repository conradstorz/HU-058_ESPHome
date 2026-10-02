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
