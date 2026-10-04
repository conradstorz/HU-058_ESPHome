"""Test-wide safety net for the local data backup.

flash.backup_dir() points at the user's real profile directory, where their
clock registry and secrets backup live. A test that reaches it writes fixture
data over a real backup - which happened once while this suite was being
written, and would have made flash.py mint new identities for clocks Home
Assistant had already paired. Mocking it per test is one forgotten line away
from repeating that, so every test gets a tmp directory by default. A test
that wants a specific path still overrides it with monkeypatch as usual.
"""
from __future__ import annotations

import pytest

import flash


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_backup_dir: opt out of the autouse backup_dir redirect below; "
        "reserved for the one test that asserts on the real, unmocked path.",
    )


@pytest.fixture(autouse=True)
def _never_touch_the_real_backup(tmp_path, monkeypatch, request):
    # test_backup_dir_is_outside_the_repository is the one deliberate,
    # explicit exception: its whole point is to assert on the real,
    # unmocked backup_dir(), so it opts out with this marker instead of
    # getting silently redirected like everything else.
    if "real_backup_dir" in request.keywords:
        return
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "autouse-backup")
