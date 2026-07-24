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

from submerge.sync import AlassNotFoundError


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
        assert not (tmp_path / "film.de.sync_tmp.srt").exists()


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
        assert resp.json()["engine"] == "ffsubsync"

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


class TestSyncAlassFallback:
    """alass is preferred for SRT-to-SRT, ffsubsync as fallback."""

    def test_sync_uses_alass_when_available(self, client_and_sync_settings):
        """When alass is installed, sync_subtitles_alass is called, NOT sync_subtitles."""
        from submerge.sync import SyncResult

        client, tmp_path = client_and_sync_settings

        de_sub = tmp_path / "film.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        ko_sub = tmp_path / "film.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n")
        (tmp_path / "film.mkv").touch()

        with (
            patch(
                "submerge.routers.merge.sync_subtitles_alass",
                return_value=SyncResult(
                    success=True, output_path=de_sub, offset_ms=100, engine_used="alass"
                ),
            ) as mock_alass,
            patch("submerge.routers.merge.sync_subtitles") as mock_ffs,
        ):
            resp = client.post("/api/sync", json={"subtitle_path": str(de_sub), "lang": "de"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["engine"] == "alass"
        mock_alass.assert_called_once()
        mock_ffs.assert_not_called()

    def test_sync_ffsubsync_fallback_when_alass_missing(self, client_and_sync_settings, caplog):
        """When alass is not installed, ffsubsync is used as fallback with warning logged."""
        from submerge.sync import SyncResult

        client, tmp_path = client_and_sync_settings

        de_sub = tmp_path / "film.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        ko_sub = tmp_path / "film.ko.srt"
        ko_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n")
        (tmp_path / "film.mkv").touch()

        import logging

        caplog.set_level(logging.WARNING, logger="submerge.routers.merge")

        with (
            patch(
                "submerge.routers.merge.sync_subtitles_alass",
                side_effect=AlassNotFoundError("alass not found"),
            ),
            patch(
                "submerge.routers.merge.sync_subtitles",
                return_value=SyncResult(
                    success=True, output_path=de_sub, offset_ms=100, engine_used="ffsubsync"
                ),
            ) as mock_ffs,
        ):
            resp = client.post("/api/sync", json={"subtitle_path": str(de_sub), "lang": "de"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["engine"] == "ffsubsync"
        mock_ffs.assert_called_once()
        assert "alass not found, falling back to ffsubsync" in caplog.text

    def test_sync_video_path_uses_video_sync(self, client_and_sync_settings):
        """When only video is available (no ref subtitles), uses sync_subtitles_to_video."""
        from submerge.sync import SyncResult

        client, tmp_path = client_and_sync_settings

        de_sub = tmp_path / "film.de.srt"
        de_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        # No KO subtitle → ref_path will be None, video fallback
        video_file = tmp_path / "film.mkv"
        video_file.touch()

        with (
            patch(
                "submerge.routers.merge.sync_subtitles_to_video",
                return_value=SyncResult(
                    success=True, output_path=de_sub, offset_ms=200, engine_used="ffsubsync"
                ),
            ) as mock_video_sync,
            patch("submerge.routers.merge.sync_subtitles_alass") as mock_alass,
            patch("submerge.routers.merge.sync_subtitles") as mock_ffs,
        ):
            resp = client.post("/api/sync", json={"subtitle_path": str(de_sub), "lang": "de"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["engine"] == "ffsubsync"
        mock_video_sync.assert_called_once()
        mock_alass.assert_not_called()
        mock_ffs.assert_not_called()


# =============================================================================
# /api/sync/arbitrary  —  flexible cross-language sync
# =============================================================================


@pytest.fixture
def arbitrary_client(monkeypatch, tmp_path):
    """Return TestClient with minimal pairs and media_root in tmp_path."""
    import submerge.api as api
    import submerge.config as cfg

    monkeypatch.setenv("SUBTOOLS_PAIRS", "de-ko")
    monkeypatch.setenv("SUBTOOLS_MEDIA_ROOT", str(tmp_path))
    cfg.get_settings.cache_clear()
    api._runtime_settings.clear()

    return TestClient(api.app), tmp_path


class TestSyncArbitraryHappyPath:
    """Happy-path tests for POST /api/sync/arbitrary."""

    def test_sync_single_target(self, arbitrary_client):
        client, tmp_path = arbitrary_client

        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        tgt = tmp_path / "film.ko.srt"
        tgt.write_text("1\n00:00:01,500 --> 00:00:02,500\n안녕\n")

        with patch(
            "submerge.routers.merge.sync_subtitles_alass",
            return_value=MagicMock(
                success=True, output_path=tgt, offset_ms=100, engine_used="alass"
            ),
        ) as mock_alass:
            resp = client.post(
                "/api/sync/arbitrary",
                json={"reference_path": str(ref), "target_paths": [str(tgt)]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "ok"
        assert data["results"][0]["path"] == str(tgt)
        assert data["results"][0]["engine"] == "alass"
        mock_alass.assert_called_once()

    def test_sync_two_targets(self, arbitrary_client):
        client, tmp_path = arbitrary_client

        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        tgt1 = tmp_path / "film.ko.srt"
        tgt1.write_text("1\n00:00:01,500 --> 00:00:02,500\n안녕\n")
        tgt2 = tmp_path / "film.en.srt"
        tgt2.write_text("1\n00:00:01,500 --> 00:00:02,500\nHello\n")

        with patch(
            "submerge.routers.merge.sync_subtitles_alass",
            return_value=MagicMock(
                success=True, output_path=tgt1, offset_ms=100, engine_used="alass"
            ),
        ):
            resp = client.post(
                "/api/sync/arbitrary",
                json={
                    "reference_path": str(ref),
                    "target_paths": [str(tgt1), str(tgt2)],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 2
        assert all(r["status"] == "ok" for r in data["results"])

    def test_alass_fallback_to_ffsubsync(self, arbitrary_client, caplog):
        import logging

        client, tmp_path = arbitrary_client

        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        tgt = tmp_path / "film.ko.srt"
        tgt.write_text("1\n00:00:01,500 --> 00:00:02,500\n안녕\n")

        caplog.set_level(logging.WARNING, logger="submerge.routers.merge")

        with (
            patch(
                "submerge.routers.merge.sync_subtitles_alass",
                side_effect=AlassNotFoundError("alass not found"),
            ),
            patch(
                "submerge.routers.merge.sync_subtitles",
                return_value=MagicMock(
                    success=True, output_path=tgt, offset_ms=100, engine_used="ffsubsync"
                ),
            ) as mock_ffs,
        ):
            resp = client.post(
                "/api/sync/arbitrary",
                json={"reference_path": str(ref), "target_paths": [str(tgt)]},
            )

        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "ok"
        assert resp.json()["results"][0]["engine"] == "ffsubsync"
        mock_ffs.assert_called_once()
        assert "alass not found, falling back to ffsubsync" in caplog.text


class TestSyncArbitraryErrors:
    """Error cases for POST /api/sync/arbitrary."""

    def test_source_equals_target_returns_400(self, arbitrary_client):
        client, tmp_path = arbitrary_client

        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")

        resp = client.post(
            "/api/sync/arbitrary",
            json={"reference_path": str(ref), "target_paths": [str(ref)]},
        )

        assert resp.status_code == 400
        assert "source and target must differ" in resp.json()["detail"]["message"]

    def test_empty_target_paths_returns_400(self, arbitrary_client):
        client, tmp_path = arbitrary_client

        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")

        resp = client.post(
            "/api/sync/arbitrary",
            json={"reference_path": str(ref), "target_paths": []},
        )

        assert resp.status_code == 400
        assert "must not be empty" in resp.json()["detail"]["message"]

    def test_missing_reference_path_returns_400(self, arbitrary_client):
        client, tmp_path = arbitrary_client

        resp = client.post(
            "/api/sync/arbitrary",
            json={"target_paths": [str(tmp_path / "x.srt")]},
        )

        assert resp.status_code == 400
        assert "reference_path required" in resp.json()["detail"]["message"]

    def test_missing_target_paths_returns_400(self, arbitrary_client):
        client, tmp_path = arbitrary_client

        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")

        resp = client.post(
            "/api/sync/arbitrary",
            json={"reference_path": str(ref)},
        )

        assert resp.status_code == 400
        assert "target_paths required" in resp.json()["detail"]["message"]

    def test_reference_outside_media_root_returns_400(self, arbitrary_client):
        client, tmp_path = arbitrary_client

        outside = Path("/tmp/outside.srt")
        tgt = tmp_path / "film.ko.srt"
        tgt.write_text("1\n00:00:01,500 --> 00:00:02,500\n안녕\n")

        resp = client.post(
            "/api/sync/arbitrary",
            json={"reference_path": str(outside), "target_paths": [str(tgt)]},
        )

        assert resp.status_code == 400
        assert "must be within media root" in resp.json()["detail"]["message"]

    def test_target_outside_media_root_returns_400(self, arbitrary_client):
        client, tmp_path = arbitrary_client

        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        outside = Path("/tmp/outside.srt")

        resp = client.post(
            "/api/sync/arbitrary",
            json={"reference_path": str(ref), "target_paths": [str(outside)]},
        )

        assert resp.status_code == 400
        assert "must be within media root" in resp.json()["detail"]["message"]

    def test_exceeds_50_targets_returns_400(self, arbitrary_client):
        client, tmp_path = arbitrary_client

        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")

        resp = client.post(
            "/api/sync/arbitrary",
            json={
                "reference_path": str(ref),
                "target_paths": [str(ref)] * 51,
            },
        )

        assert resp.status_code == 400
        assert "exceeds maximum of 50" in resp.json()["detail"]["message"]


class TestSyncArbitraryPartialSuccess:
    """Partial success when one target fails but others succeed."""

    def test_partial_success(self, arbitrary_client):
        from submerge.sync import SyncError

        client, tmp_path = arbitrary_client

        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        tgt_ok = tmp_path / "film.ko.srt"
        tgt_ok.write_text("1\n00:00:01,500 --> 00:00:02,500\n안녕\n")
        tgt_bad = tmp_path / "film.en.srt"
        tgt_bad.write_text("1\n00:00:01,500 --> 00:00:02,500\nHello\n")

        call_count = 0

        def _alternating_sync(ref_p, inp_p, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(
                    success=True, output_path=inp_p, offset_ms=100, engine_used="alass"
                )
            raise SyncError("simulated failure")

        with patch(
            "submerge.routers.merge.sync_subtitles_alass",
            side_effect=_alternating_sync,
        ):
            resp = client.post(
                "/api/sync/arbitrary",
                json={
                    "reference_path": str(ref),
                    "target_paths": [str(tgt_ok), str(tgt_bad)],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 2
        statuses = {r["path"]: r["status"] for r in data["results"]}
        assert statuses[str(tgt_ok)] == "ok"
        assert statuses[str(tgt_bad)] == "error"


# =============================================================================
# /api/sync/folder  —  episode-scoped bulk sync
# =============================================================================


class TestSyncFolder:
    """Tests for POST /api/sync/folder — episode-scoped bulk sync."""

    def test_syncs_all_same_episode_siblings(self, arbitrary_client):
        """All subtitle files of the same episode are synced."""
        client, tmp_path = arbitrary_client

        (tmp_path / "film.mkv").touch()
        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        tgt1 = tmp_path / "film.ko.srt"
        tgt1.write_text("1\n00:00:01,500 --> 00:00:02,500\n안녕\n")
        tgt2 = tmp_path / "film.en.srt"
        tgt2.write_text("1\n00:00:01,500 --> 00:00:02,500\nHello\n")

        with patch(
            "submerge.routers.merge.sync_subtitles_alass",
            return_value=MagicMock(
                success=True, output_path=tgt1, offset_ms=100, engine_used="alass"
            ),
        ):
            resp = client.post(
                "/api/sync/folder",
                json={"reference_path": str(ref)},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["synced"] == 2
        assert data["errors"] == 0

    def test_skips_other_episodes_in_same_folder(self, arbitrary_client):
        """Subtitle files from other episodes in the same folder are NOT touched."""
        client, tmp_path = arbitrary_client

        # Episode 1
        (tmp_path / "Show.S01E01.mkv").touch()
        ref = tmp_path / "Show.S01E01.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        tgt = tmp_path / "Show.S01E01.en.srt"
        tgt.write_text("1\n00:00:01,500 --> 00:00:02,500\nHello\n")

        # Episode 2 — should NOT be synced
        (tmp_path / "Show.S01E02.mkv").touch()
        other_ep = tmp_path / "Show.S01E02.de.srt"
        other_ep.write_text("1\n00:00:01,500 --> 00:00:02,500\nAndere\n")

        with patch(
            "submerge.routers.merge.sync_subtitles_alass",
            return_value=MagicMock(
                success=True, output_path=tgt, offset_ms=100, engine_used="alass"
            ),
        ) as mock_alass:
            resp = client.post(
                "/api/sync/folder",
                json={"reference_path": str(ref)},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1  # Only the E01 target, NOT the E02 file
        assert data["synced"] == 1

        # Verify only same-episode target was synced
        call_args = mock_alass.call_args
        assert Path(call_args[0][0]) == ref.resolve()  # reference
        assert Path(call_args[0][1]) == tgt.resolve()  # same episode target

    def test_excludes_reference_itself(self, arbitrary_client):
        """Reference subtitle is excluded from targets."""
        client, tmp_path = arbitrary_client

        (tmp_path / "film.mkv").touch()
        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")

        with patch(
            "submerge.routers.merge.sync_subtitles_alass",
            return_value=MagicMock(
                success=True, output_path=ref, offset_ms=100, engine_used="alass"
            ),
        ) as mock_alass:
            resp = client.post(
                "/api/sync/folder",
                json={"reference_path": str(ref)},
            )

        assert resp.status_code == 200
        mock_alass.assert_not_called()  # reference is excluded

    def test_no_other_subs_returns_empty(self, arbitrary_client):
        """When the episode has no other subtitles, empty results are returned."""
        client, tmp_path = arbitrary_client

        (tmp_path / "film.mkv").touch()
        ref = tmp_path / "film.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")

        resp = client.post(
            "/api/sync/folder",
            json={"reference_path": str(ref)},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["synced"] == 0
        assert data["errors"] == 0
        assert data["results"] == []

    def test_no_video_returns_400(self, arbitrary_client):
        """When the episode's video cannot be found, a clear 400 error is returned."""
        client, tmp_path = arbitrary_client

        ref = tmp_path / "orphan.de.srt"
        ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")

        resp = client.post(
            "/api/sync/folder",
            json={"reference_path": str(ref)},
        )

        assert resp.status_code == 400
        assert "Cannot determine which episode" in resp.json()["detail"]["message"]

    def test_missing_reference_path_returns_400(self, arbitrary_client):
        client, tmp_path = arbitrary_client

        resp = client.post("/api/sync/folder", json={})

        assert resp.status_code == 400
        assert "reference_path required" in resp.json()["detail"]["message"]
