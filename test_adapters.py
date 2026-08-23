from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from rac1sync.config import SummaryConfig
from rac1sync.domain import Episode, FeedError, FeedState
from rac1sync.downloader import extension_of, slugify
from rac1sync.feed import RssFeedReader
from rac1sync.storage import SqliteEpisodeRepository
from rac1sync.summarization import OllamaSummarizationService, split_text, summary_path_for

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


#  --- Resum amb Ollama ---


def ollama(requests: list[dict], *, replies: list[str] | None = None, status: int = 200):
    """Transport fals que enregistra les peticions i respon com Ollama."""
    answers = iter(replies or ["El resum"])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, text="error")
        return httpx.Response(200, json={"message": {"content": next(answers)}})

    return httpx.MockTransport(handler)


def service(requests, config: SummaryConfig | None = None, **kwargs):
    return OllamaSummarizationService(config or SummaryConfig(), ollama(requests, **kwargs))


def test_resum_envia_el_system_prompt_i_desa_el_markdown(tmp_path: Path):
    txt = tmp_path / "episodi.txt"
    txt.write_text("Avui parlem de Roy Cohn.", encoding="utf-8")
    requests: list[dict] = []

    resultat = service(requests).summarize(txt, "Resumeix en català.")

    assert resultat == summary_path_for(txt) == tmp_path / "episodi.resum.md"
    assert resultat.read_text(encoding="utf-8") == "El resum\n"
    assert len(requests) == 1
    assert requests[0]["messages"] == [
        {"role": "system", "content": "Resumeix en català."},
        {"role": "user", "content": "Avui parlem de Roy Cohn."},
    ]
    assert requests[0]["model"] == "llama3.1:latest"
    assert requests[0]["stream"] is False
    assert requests[0]["options"]["num_ctx"] == 16384


def test_resum_llarg_va_per_trossos_i_es_fon(tmp_path: Path):
    txt = tmp_path / "llarg.txt"
    #  160 frases de 15 caràcters: en trossos de 1000 en surten 3, més la fusió.
    txt.write_text("Frase de prova. " * 160, encoding="utf-8")
    requests: list[dict] = []

    resultat = service(
        requests,
        SummaryConfig(chunk_chars=1000),
        replies=["parcial 1", "parcial 2", "parcial 3", "final"],
    ).summarize(txt, "Resumeix.")

    assert resultat.read_text(encoding="utf-8") == "final\n"
    #  3 crides de trossos + 1 de fusió, totes amb el mateix system prompt.
    assert len(requests) == 4
    assert all(r["messages"][0]["content"] == "Resumeix." for r in requests)
    assert "Fragment 1 de 3" in requests[0]["messages"][1]["content"]
    assert "resums parcials" in requests[-1]["messages"][1]["content"]
    assert "parcial 3" in requests[-1]["messages"][1]["content"]


def test_resum_retorna_none_si_ollama_falla(tmp_path: Path):
    txt = tmp_path / "episodi.txt"
    txt.write_text("Contingut.", encoding="utf-8")

    assert service([], status=500).summarize(txt, "Resumeix.") is None
    assert not summary_path_for(txt).exists()


def test_no_resumeix_una_transcripcio_buida(tmp_path: Path):
    txt = tmp_path / "buit.txt"
    txt.write_text("   \n", encoding="utf-8")
    requests: list[dict] = []

    assert service(requests).summarize(txt, "Resumeix.") is None
    assert requests == []


def test_split_text_no_talla_paraules():
    text = " ".join(f"paraula{i}" for i in range(200))

    trossos = split_text(text, 100)

    assert len(trossos) > 1
    assert all(len(t) <= 100 for t in trossos)
    assert " ".join(trossos).split() == text.split()


def test_split_text_deixa_passar_els_textos_curts():
    assert split_text("curt", 100) == ["curt"]
