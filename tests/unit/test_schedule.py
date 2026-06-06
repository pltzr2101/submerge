"""Tests for schedule module lock lifecycle and overlap protection."""

from __future__ import annotations

import asyncio

import pytest


def _get_schedule_mod():
    """Import schedule that avoids circular import with api.py.

    schedule → api → schedule is circular.  Loading ``submerge.api``
    first resolves the cycle, after which ``submerge.routers.schedule``
    is accessible as an already-loaded submodule.
    """
    import importlib

    importlib.import_module("submerge.api")  # resolve the import cycle

    return importlib.import_module("submerge.routers.schedule")


class TestScheduleLockLifecycle:
    """_execute_scheduled_merge lock guard and start_scheduler lock init."""

    def test_execute_scheduled_merge_without_init_does_not_crash(self, caplog):
        """_execute_scheduled_merge must not crash when scheduler not started."""
        sched_mod = _get_schedule_mod()
        sched_mod._schedule_merge_lock = None
        asyncio.run(sched_mod._execute_scheduled_merge())
        assert "scheduler not initialized" in caplog.text

    def test_schedule_lock_initialized_after_start(self, tmp_path, monkeypatch):
        """start_scheduler must initialize _schedule_merge_lock."""
        sched_mod = _get_schedule_mod()

        monkeypatch.setattr(
            sched_mod,
            "_load_app_settings",
            lambda: {"auto_merge_enabled": True, "schedule_time": "03:00"},
        )
        sched_mod._schedule_merge_lock = None
        try:
            pytest.importorskip("apscheduler")
            # APScheduler.start() requires a running event loop
            asyncio.run(_start_and_check(sched_mod))
        finally:
            sched_mod._schedule_merge_lock = None


async def _start_and_check(sched_mod):
    sched_mod.start_scheduler(None)
    assert sched_mod._schedule_merge_lock is not None
    sched_mod.stop_scheduler()
