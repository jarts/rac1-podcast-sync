from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from rac1sync.config import (
    AppConfig,
    FeedConfig,
    StorageConfig,
    SyncConfig,
    TranscriptionServiceType,
)
from rac1sync.domain import DownloadError, Episode, FeedSnapshot, FeedState
from rac1sync.service import PodcastSyncService
from rac1sync.summarization import summary_path_for


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


class FakeTranscriber:
    """Escriu un .txt al costat de l'MP3, com fan els serveis reals."""

    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.transcribed: list[str] = []

    def transcribe(self, audio_path: Path) -> Path | None:
        if audio_path.name in self.failing:
            return None
        self.transcribed.append(audio_path.name)
        txt = audio_path.with_suffix(".txt")
        txt.write_text(f"transcripció de {audio_path.stem}", encoding="utf-8")
        return txt


class FakeSummarizer:
    def __init__(self, failing: bool = False) -> None:
        self.failing = failing
        self.calls: list[tuple[Path, str]] = []

    def summarize(self, transcript_path: Path, system_prompt: str) -> Path | None:
        self.calls.append((transcript_path, system_prompt))
        if self.failing:
            return None
        out = summary_path_for(transcript_path)
        out.write_text("resum", encoding="utf-8")
        return out


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        feeds=(FeedConfig(name="via-lliure", url="https://example.cat/rss"),),
        storage=StorageConfig(database=tmp_path / "db.sqlite3", download_dir=tmp_path),
        sync=SyncConfig(max_concurrent_downloads=2, interval_minutes=60),
    )


def build(config, reader, downloader, repository, summarizer=None) -> PodcastSyncService:
    return PodcastSyncService(config, reader, downloader, repository, summarizer)


def with_transcription(
    config: AppConfig, tmp_path: Path, system_prompt: str | None
) -> AppConfig:
    """Còpia de la config amb un feed que transcriu (i potser resumeix)."""
    return AppConfig(
        feeds=(
            FeedConfig(
                name="via-lliure",
                url="https://example.cat/rss",
                transcriptionService=TranscriptionServiceType.Whisper,
                system_prompt=system_prompt,
            ),
        ),
        storage=config.storage,
        sync=config.sync,
    )


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


def test_resumeix_amb_el_system_prompt_del_feed(config, tmp_path, monkeypatch):
    transcriber = FakeTranscriber()
    monkeypatch.setattr("rac1sync.service.get_transcription_service", lambda _: transcriber)

    prompt = "Resumeix en català i en 5 punts."
    amb_resum = with_transcription(config, tmp_path, prompt)
    summarizer = FakeSummarizer()
    snapshot = FeedSnapshot(episodes=(make_episode(1),), state=FeedState())

    build(
        amb_resum, FakeFeedReader(snapshot), FakeDownloader(tmp_path), FakeRepository(), summarizer
    ).sync_feed(amb_resum.feeds[0])

    assert transcriber.transcribed == ["guid-1.mp3"]
    assert summarizer.calls == [(tmp_path / "guid-1.txt", prompt)]
    assert summary_path_for(tmp_path / "guid-1.txt").read_text() == "resum"


def test_no_resumeix_si_el_feed_no_te_system_prompt(config, tmp_path, monkeypatch):
    transcriber = FakeTranscriber()
    monkeypatch.setattr("rac1sync.service.get_transcription_service", lambda _: transcriber)

    sense_resum = with_transcription(config, tmp_path, None)
    summarizer = FakeSummarizer()
    snapshot = FeedSnapshot(episodes=(make_episode(1),), state=FeedState())

    build(
        sense_resum,
        FakeFeedReader(snapshot),
        FakeDownloader(tmp_path),
        FakeRepository(),
        summarizer,
    ).sync_feed(sense_resum.feeds[0])

    assert transcriber.transcribed == ["guid-1.mp3"]
    assert summarizer.calls == []


def test_una_transcripcio_fallida_no_arriba_al_resum(config, tmp_path, monkeypatch):
    """Sense transcrit no hi ha res a resumir, i la resta d'episodis ha de seguir."""
    transcriber = FakeTranscriber(failing={"guid-1.mp3"})
    monkeypatch.setattr("rac1sync.service.get_transcription_service", lambda _: transcriber)

    amb_resum = with_transcription(config, tmp_path, "Resumeix-ho.")
    summarizer = FakeSummarizer()
    snapshot = FeedSnapshot(episodes=(make_episode(1), make_episode(2)), state=FeedState())

    report = build(
        amb_resum, FakeFeedReader(snapshot), FakeDownloader(tmp_path), FakeRepository(), summarizer
    ).sync_feed(amb_resum.feeds[0])

    #  La descàrrega ha anat bé; només ha fallat la transcripció.
    assert report.downloaded == 2
    assert [path.name for path, _ in summarizer.calls] == ["guid-2.txt"]


def test_un_resum_fallit_no_tomba_la_sincronitzacio(config, tmp_path, monkeypatch):
    transcriber = FakeTranscriber()
    monkeypatch.setattr("rac1sync.service.get_transcription_service", lambda _: transcriber)

    amb_resum = with_transcription(config, tmp_path, "Resumeix-ho.")
    snapshot = FeedSnapshot(episodes=(make_episode(1),), state=FeedState("W/1"))
    repository = FakeRepository()

    report = build(
        amb_resum,
        FakeFeedReader(snapshot),
        FakeDownloader(tmp_path),
        repository,
        FakeSummarizer(failing=True),
    ).sync_feed(amb_resum.feeds[0])

    assert report.downloaded == 1
    assert report.failed == 0
    assert repository.states["https://example.cat/rss"].etag == "W/1"


def test_un_feed_caigut_no_tomba_la_resta(config, tmp_path):
    from rac1sync.domain import FeedError

    class BrokenReader:
        def read(self, url: str, state: FeedState) -> FeedSnapshot:
            raise FeedError("503")

    reports = build(config, BrokenReader(), FakeDownloader(tmp_path), FakeRepository()).sync_all()

    assert len(reports) == 1
    assert reports[0].failed == 1
