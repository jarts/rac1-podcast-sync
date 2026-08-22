from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from rac1sync.config import AppConfig, FeedConfig, StorageConfig, SyncConfig
from rac1sync.domain import DownloadError, Episode, FeedSnapshot, FeedState
from rac1sync.service import PodcastSyncService


def make_episode(n: int) -> Episode:
    return Episode(
        guid=f"guid-{n}",
        title=f"Episodi {n}",
        audio_url=f"https://cdn.example/{n}.mp3",
        published_at=datetime(2026, 8, n, tzinfo=UTC),
    )


class FakeFeedReader:
    def __init__(self, snapshot: FeedSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[str, FeedState]] = []

    def read(self, url: str, state: FeedState) -> FeedSnapshot:
        self.calls.append((url, state))
        return self.snapshot


class FakeDownloader:
    def __init__(self, tmp_path: Path, failing: set[str] | None = None) -> None:
        self.tmp_path = tmp_path
        self.failing = failing or set()
        self.downloaded: list[str] = []

    def download(self, episode: Episode, feed_name: str) -> Path:
        if episode.guid in self.failing:
            raise DownloadError(f"boom {episode.guid}")
        path = self.tmp_path / f"{episode.guid}.mp3"
        path.write_bytes(b"audio")
        self.downloaded.append(episode.guid)
        return path


class FakeRepository:
    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known or set()
        self.marked: list[str] = []
        self.states: dict[str, FeedState] = {}

    def is_downloaded(self, guid: str, audio_url: str) -> bool:
        return guid in self.known or audio_url in self.known

    def mark_downloaded(self, episode, feed_name, path, size_bytes) -> None:
        self.known.add(episode.guid)
        self.marked.append(episode.guid)

    def load_feed_state(self, feed_url: str) -> FeedState:
        return self.states.get(feed_url, FeedState())

    def save_feed_state(self, feed_url: str, state: FeedState) -> None:
        self.states[feed_url] = state


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        feeds=(FeedConfig(name="via-lliure", url="https://example.cat/rss"),),
        storage=StorageConfig(database=tmp_path / "db.sqlite3", download_dir=tmp_path),
        sync=SyncConfig(max_concurrent_downloads=2, interval_minutes=60),
    )


def build(config, reader, downloader, repository) -> PodcastSyncService:
    return PodcastSyncService(config, reader, downloader, repository)


def test_descarrega_els_episodis_nous(config, tmp_path):
    snapshot = FeedSnapshot(episodes=(make_episode(1), make_episode(2)), state=FeedState("W/1"))
    downloader = FakeDownloader(tmp_path)
    repository = FakeRepository()

    report = build(config, FakeFeedReader(snapshot), downloader, repository).sync_feed(
        config.feeds[0]
    )

    assert report.downloaded == 2
    assert report.skipped == 0
    assert sorted(downloader.downloaded) == ["guid-1", "guid-2"]


def test_no_repeteix_els_ja_descarregats(config, tmp_path):
    snapshot = FeedSnapshot(episodes=(make_episode(1), make_episode(2)), state=FeedState())
    downloader = FakeDownloader(tmp_path)

    report = build(
        config, FakeFeedReader(snapshot), downloader, FakeRepository(known={"guid-1"})
    ).sync_feed(config.feeds[0])

    assert report.downloaded == 1
    assert report.skipped == 1
    assert downloader.downloaded == ["guid-2"]


def test_deduplica_per_url_si_canvia_el_guid(config, tmp_path):
    snapshot = FeedSnapshot(episodes=(make_episode(1),), state=FeedState())
    repository = FakeRepository(known={"https://cdn.example/1.mp3"})
    downloader = FakeDownloader(tmp_path)

    report = build(config, FakeFeedReader(snapshot), downloader, repository).sync_feed(
        config.feeds[0]
    )

    assert report.downloaded == 0
    assert downloader.downloaded == []


def test_304_no_descarrega_res(config, tmp_path):
    snapshot = FeedSnapshot(episodes=(), state=FeedState("W/1"), not_modified=True)
    downloader = FakeDownloader(tmp_path)

    report = build(config, FakeFeedReader(snapshot), downloader, FakeRepository()).sync_feed(
        config.feeds[0]
    )

    assert (report.downloaded, report.skipped, report.failed) == (0, 0, 0)


def test_una_descarrega_fallida_no_atura_la_resta(config, tmp_path):
    snapshot = FeedSnapshot(
        episodes=(make_episode(1), make_episode(2), make_episode(3)), state=FeedState()
    )
    downloader = FakeDownloader(tmp_path, failing={"guid-2"})
    repository = FakeRepository()

    report = build(config, FakeFeedReader(snapshot), downloader, repository).sync_feed(
        config.feeds[0]
    )

    assert report.downloaded == 2
    assert report.failed == 1
    assert "guid-2" not in repository.marked


def test_no_desa_letag_si_hi_ha_hagut_errors(config, tmp_path):
    """Desar l'ETag amb errors pendents amagaria els episodis darrere d'un 304."""
    snapshot = FeedSnapshot(episodes=(make_episode(1),), state=FeedState("W/nou"))
    repository = FakeRepository()

    build(config, FakeFeedReader(snapshot), FakeDownloader(tmp_path, failing={"guid-1"}),
          repository).sync_feed(config.feeds[0])

    assert repository.states == {}


def test_respecta_el_limit_per_execucio(config, tmp_path):
    snapshot = FeedSnapshot(episodes=tuple(make_episode(n) for n in range(1, 6)), state=FeedState())
    limited = AppConfig(
        feeds=config.feeds,
        storage=config.storage,
        sync=SyncConfig(max_episodes_per_run=2),
    )
    downloader = FakeDownloader(tmp_path)

    report = build(limited, FakeFeedReader(snapshot), downloader, FakeRepository()).sync_feed(
        limited.feeds[0]
    )

    assert report.downloaded == 2
    #  Els més recents primer.
    assert sorted(downloader.downloaded) == ["guid-4", "guid-5"]


def test_un_feed_caigut_no_tomba_la_resta(config, tmp_path):
    from rac1sync.domain import FeedError

    class BrokenReader:
        def read(self, url: str, state: FeedState) -> FeedSnapshot:
            raise FeedError("503")

    reports = build(config, BrokenReader(), FakeDownloader(tmp_path), FakeRepository()).sync_all()

    assert len(reports) == 1
    assert reports[0].failed == 1
