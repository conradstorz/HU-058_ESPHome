"""Test-wide safety net for the local data backup.

Two things in flash.py point at the user's own files, and a test that reaches
either one does real damage.

flash.backup_dir() points at their profile directory, where the clock registry
and secrets backup live. A test that reaches it writes fixture data over a real
backup - which happened once while this suite was being written, and would have
made flash.py mint new identities for clocks Home Assistant had already paired.

flash.REGISTRY_PATH, flash.SECRETS_PATH and flash.HERE point at the real
firmware/esphome/ in this checkout, and restore_local_data()'s whole job is to
*write* the first two. A test that calls it, or main(), without overriding them
writes into the user's live registry and secrets - the same accident as the
first, one direction more damaging.

Mocking either per test is one forgotten line away from repeating that, so
every test gets tmp_path for both by default. A test that wants a specific path
still overrides it with monkeypatch as usual.
"""
from __future__ import annotations

import pytest

import flash


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_backup_dir: opt out of the autouse backup_dir redirect below; "
        "reserved for the one test that asserts on the real, unmocked path. It "
        "does not opt out of the working-tree redirect, which no test may skip.",
    )


@pytest.fixture(autouse=True)
def _never_touch_the_real_backup(tmp_path, monkeypatch, request):
    # The working tree first, and for every test without exception. This is
    # where restore_local_data() writes, so the real paths must not be
    # reachable even from a test that wants the real backup_dir() for a
    # read-only assertion.
    monkeypatch.setattr(flash, "REGISTRY_PATH", tmp_path / "devices.yaml")
    monkeypatch.setattr(flash, "SECRETS_PATH", tmp_path / "secrets.yaml")
    # Safe to redirect: backup_readme() reads the global when it is called,
    # and the one test that needs the real directory derives it from
    # flash.__file__ instead.
    monkeypatch.setattr(flash, "HERE", tmp_path)
    # The tests that assert on the real, unmocked backup_dir() are the
    # deliberate, explicit exception to the redirect below, and they say so
    # with this marker instead of getting silently redirected like everything
    # else. Nothing above this line is skipped for them.
    if "real_backup_dir" in request.keywords:
        return
    monkeypatch.setattr(flash, "backup_dir", lambda: tmp_path / "autouse-backup")
