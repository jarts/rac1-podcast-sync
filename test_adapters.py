from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from rac1sync.domain import Episode, FeedError, FeedState
from rac1sync.downloader import extension_of, slugify
from rac1sync.feed import RssFeedReader
from rac1sync.storage import SqliteEpisodeRepository

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Via lliure</title>
    <item>
      <guid isPermaLink="false">abc-123</guid>
      <title>Il·lustres execrables: Roy Cohn</title>
      <pubDate>Sat, 23 May 2026 10:00:00 +0000</pubDate>
      <itunes:duration>31:24</itunes:duration>
      <enclosure url="https://cdn.example/roy.mp3" type="audio/mpeg" length="30150000"/>
    </item>
    <item>
      <title>Entrada sense àudio</title>
      <guid>sense-audio</guid>
    </item>
  </channel>
</rss>
"""


class StubHttp:
    """Substitueix HttpClient: retorna respostes httpx construïdes a mà."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.headers: dict[str, str] = {}

    def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        self.headers = headers or {}
        return self.response


def response(status: int, content: bytes = b"", headers: dict[str, str] | None = None):
    return httpx.Response(
        status_code=status,
        content=content,
        headers=headers or {},
        request=httpx.Request("GET", "https://example.cat/rss"),
    )


def test_parseja_els_episodis_amb_enclosure_daudio():
    reader = RssFeedReader(StubHttp(response(200, RSS.encode(), {"ETag": "W/1"})))

    snapshot = reader.read("https://example.cat/rss", FeedState())

    assert len(snapshot.episodes) == 1  # l'entrada sense àudio queda fora
    episode = snapshot.episodes[0]
    assert episode.guid == "abc-123"
    assert episode.audio_url == "https://cdn.example/roy.mp3"
    assert episode.published_at == datetime(2026, 5, 23, 10, 0, tzinfo=UTC)
    assert episode.duration_seconds == 31 * 60 + 24
    assert episode.size_bytes == 30_150_000
    assert snapshot.state.etag == "W/1"


def test_envia_les_capceleres_condicionals():
    http = StubHttp(response(304))
    reader = RssFeedReader(http)

    snapshot = reader.read("https://example.cat/rss", FeedState(etag="W/1", last_modified="ahir"))

    assert snapshot.not_modified is True
    assert http.headers == {"If-None-Match": "W/1", "If-Modified-Since": "ahir"}


def test_error_http_del_feed():
    reader = RssFeedReader(StubHttp(response(500)))

    with pytest.raises(FeedError, match="500"):
        reader.read("https://example.cat/rss", FeedState())


def test_repositori_deduplica_per_guid_o_url(tmp_path: Path):
    episode = Episode(guid="g1", title="T", audio_url="https://cdn/1.mp3")

    with SqliteEpisodeRepository(tmp_path / "db.sqlite3") as repository:
        assert repository.is_downloaded("g1", "https://cdn/1.mp3") is False

        repository.mark_downloaded(episode, "feed", tmp_path / "1.mp3", 10)

        assert repository.is_downloaded("g1", "https://altra/url.mp3") is True
        assert repository.is_downloaded("altre-guid", "https://cdn/1.mp3") is True
        #  Idempotent: reinserir el mateix guid no ha de petar.
        repository.mark_downloaded(episode, "feed", tmp_path / "1.mp3", 10)


def test_repositori_persisteix_lestat_del_feed(tmp_path: Path):
    with SqliteEpisodeRepository(tmp_path / "db.sqlite3") as repository:
        assert repository.load_feed_state("https://f").is_empty
        repository.save_feed_state("https://f", FeedState(etag="W/2", last_modified="avui"))
        assert repository.load_feed_state("https://f").etag == "W/2"


@pytest.mark.parametrize(
    ("entrada", "esperat"),
    [
        ("Il·lustres execrables: Roy Cohn", "Il·lustres-execrables-Roy-Cohn"),
        ("Dissabte 13/06/26 - 13 h", "Dissabte-130626---13-h"),
        ("Via lliure - L'hora a hora", "Via-lliure---L'hora-a-hora"),
        ("../../etc/passwd", "etcpasswd"),
        ("***", "sense-titol"),
    ],
)
def test_slugify(entrada: str, esperat: str):
    assert slugify(entrada) == esperat


@pytest.mark.parametrize(
    ("url", "esperat"),
    [
        ("https://cdn/a.mp3", ".mp3"),
        ("https://cdn/a.m4a?token=x", ".m4a"),
        ("https://cdn/redirect", ".mp3"),
    ],
)
def test_extension_of(url: str, esperat: str):
    assert extension_of(url) == esperat
