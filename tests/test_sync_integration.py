"""Integration tests for /api/sync endpoint — bidirectional sync, backups, edge cases."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient


@pytest.fixture
def client_and_sync_settings(monkeypatch, tmp_path):
    """Return TestClient with de-ko pairs configured. Clears per-test."""
    import submerge.api as api
    import submerge.config as cfg

    monkeypatch.setenv("SUBTOOLS_PAIRS", "de-ko")
    monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
    cfg.get_settings.cache_clear()
    api._runtime_settings.clear()

    client = TestClient(api.app)
    return client, tmp_path


class TestSyncBidirectional:
    """Bidirectional pair lookup — de→ko and ko→de."""

    def test_sync_de_uses_ko_as_reference(self, client_and_sync_settings):
        client, tmp_path = client_and_sync_settings

        de_sub = tmp_path / "film.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        ko_sub = tmp_path / "film.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n")
        (tmp_path / "film.mkv").touch()

        with patch("submerge.routers.merge.sync_subtitles") as mock_sync:
            mock_sync.return_value = MagicMock(
                success=True,
                output_path=de_sub,
                offset_ms=100,
            )
            resp = client.post(
                "/api/sync",
                json={"subtitle_path": str(de_sub), "lang": "de"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # Verify output path equals input path (in-place)
        assert resp.json()["output"] == str(de_sub)
        # Verify KO was used as reference
        call_args = mock_sync.call_args
        assert Path(call_args[0][0]) == ko_sub  # reference_path
        assert Path(call_args[0][1]) == de_sub  # input_path (in-place)

    def test_sync_ko_uses_de_as_reference(self, client_and_sync_settings):
        client, tmp_path = client_and_sync_settings

        ko_sub = tmp_path / "film.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n")
        de_sub = tmp_path / "film.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        (tmp_path / "film.mkv").touch()

        with patch("submerge.routers.merge.sync_subtitles") as mock_sync:
            mock_sync.return_value = MagicMock(
                success=True,
                output_path=ko_sub,
                offset_ms=100,
            )
            resp = client.post(
                "/api/sync",
                json={"subtitle_path": str(ko_sub), "lang": "ko"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["output"] == str(ko_sub)
        # Verify DE was used as reference
        call_args = mock_sync.call_args
        assert Path(call_args[0][0]) == de_sub  # reference_path
        assert Path(call_args[0][1]) == ko_sub  # input_path (in-place)


class TestSyncBackupBehavior:
    """Backup (.bak) is created and preserved."""

    def test_sync_creates_bak_before_overwrite(self, client_and_sync_settings):
        client, tmp_path = client_and_sync_settings

        de_sub = tmp_path / "film.de.srt"
        original_content = "1\n00:00:01,000 --> 00:00:02,000\nHallo\n"
        de_sub.write_text(original_content)
        ko_sub = tmp_path / "film.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n")
        (tmp_path / "film.mkv").touch()

        synced_content = "1\n00:00:01,000 --> 00:00:02,000\nHallo (synced)\n"

        # Mock only ffsubsync execution — let the real sync_subtitles
        # handle backup, tmp, and atomic replace.
        def _fake_run(cmd, **kwargs):
            # cmd[-1] is the -o output path (tmp file)
            import pathlib

            out = pathlib.Path(cmd[-1])
            out.write_text(synced_content)
            return MagicMock(returncode=0, stdout="offset: 200ms", stderr="")

        with (
            patch("submerge.sync.shutil.which", return_value="/usr/bin/ffs"),
            patch("submerge.sync.subprocess.run", side_effect=_fake_run),
        ):
            resp = client.post(
                "/api/sync",
                json={"subtitle_path": str(de_sub), "lang": "de"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        bak_path = tmp_path / "film.de.srt.bak"
        assert bak_path.exists()
        assert bak_path.read_text() == original_content
        assert de_sub.read_text() == synced_content

    def test_sync_backup_preserved_on_ffsubsync_failure(self, client_and_sync_settings):
        client, tmp_path = client_and_sync_settings

        de_sub = tmp_path / "film.de.srt"
        original_content = "1\n00:00:01,000 --> 00:00:02,000\nHallo\n"
        de_sub.write_text(original_content)
        ko_sub = tmp_path / "film.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n")
        (tmp_path / "film.mkv").touch()

        with (
            patch("submerge.sync.shutil.which", return_value="/usr/bin/ffs"),
            patch(
                "submerge.sync.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "ffs", stderr="sync error"),
            ),
        ):
            resp = client.post(
                "/api/sync",
                json={"subtitle_path": str(de_sub), "lang": "de"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

        bak_path = tmp_path / "film.de.srt.bak"
        assert bak_path.exists()
        assert bak_path.read_text() == original_content
        # Original file unchanged
        assert de_sub.read_text() == original_content
        # Temp file cleaned up
        assert not (tmp_path / "film.de.srt.tmp").exists()


class TestSyncEdgeCases:
    """HTTP 400 on missing files, unsupported lang, timeout, large offset."""

    def test_sync_missing_subtitle_returns_400(self, client_and_sync_settings):
        client, tmp_path = client_and_sync_settings

        resp = client.post(
            "/api/sync",
            json={"subtitle_path": str(tmp_path / "nonexistent.srt"), "lang": "de"},
        )

        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"]["message"].lower()

    def test_sync_ffsubsync_timeout(self, client_and_sync_settings):
        client, tmp_path = client_and_sync_settings

        de_sub = tmp_path / "film.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        ko_sub = tmp_path / "film.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n")
        (tmp_path / "film.mkv").touch()

        with (
            patch("submerge.sync.shutil.which", return_value="/usr/bin/ffs"),
            patch(
                "submerge.sync.subprocess.run",
                side_effect=subprocess.TimeoutExpired("ffs", 300),
            ),
        ):
            resp = client.post(
                "/api/sync",
                json={"subtitle_path": str(de_sub), "lang": "de"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "error"
        assert "timeout" in resp.json()["message"].lower()

    def test_sync_large_offset_returns_warning(self, client_and_sync_settings):
        client, tmp_path = client_and_sync_settings

        de_sub = tmp_path / "film.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        ko_sub = tmp_path / "film.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n")
        (tmp_path / "film.mkv").touch()

        from submerge.sync import SyncResult

        with patch(
            "submerge.routers.merge.sync_subtitles",
            return_value=SyncResult(success=False, output_path=de_sub, offset_ms=35000),
        ):
            resp = client.post(
                "/api/sync",
                json={"subtitle_path": str(de_sub), "lang": "de"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "warning"
        assert "verify result" in resp.json()["message"].lower()
        assert resp.json()["offset_ms"] == 35000

    def test_sync_unsupported_lang_returns_400(self, client_and_sync_settings):
        client, tmp_path = client_and_sync_settings

        fr_sub = tmp_path / "film.fr.srt"
        fr_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")

        resp = client.post(
            "/api/sync",
            json={"subtitle_path": str(fr_sub), "lang": "fr"},
        )

        assert resp.status_code == 400
        assert "not part of any configured pair" in resp.json()["detail"]["message"]


class TestSyncParallelSerialization:
    """Parallel sync calls on the same file are serialized."""

    @pytest.mark.asyncio
    async def test_sync_parallel_calls_serialized(self, client_and_sync_settings):
        _, tmp_path = client_and_sync_settings

        de_sub = tmp_path / "film.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        ko_sub = tmp_path / "film.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n")
        (tmp_path / "film.mkv").touch()

        call_order = []
        event1 = threading.Event()
        event2 = threading.Event()

        def _slow_sync(ref, inp, **kwargs):
            call_order.append("start")
            event1.set()
            event2.wait()
            call_order.append("finish")
            from submerge.sync import SyncResult

            return SyncResult(success=True, output_path=inp, offset_ms=100)

        import submerge.api

        transport = ASGITransport(app=submerge.api.app)

        with patch("submerge.routers.merge.sync_subtitles", side_effect=_slow_sync):
            async with AsyncClient(transport=transport, base_url="http://test") as client:

                async def _post():
                    return await client.post(
                        "/api/sync",
                        json={"subtitle_path": str(de_sub), "lang": "de"},
                    )

                task1 = asyncio.create_task(_post())
                # Wait for first request to start
                ok = await asyncio.to_thread(event1.wait, 5.0)
                assert ok is True, "first request did not start in time"
                # Now start the second request — should be blocked on the lock
                task2 = asyncio.create_task(_post())
                # Small delay to allow second request to hit the lock
                await asyncio.sleep(0.3)
                # At this point, only one "start" should be in call_order
                assert call_order == ["start"], f"call_order={call_order}"
                # Release the first request
                event2.set()
                resp1 = await task1
                resp2 = await task2

        assert resp1.json()["status"] in ("ok", "error")
        assert resp2.json()["status"] in ("ok", "error")
        # Both requests started — lock serialised them
        assert call_order == ["start", "finish", "start", "finish"], f"call_order={call_order}"
