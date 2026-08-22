"""Adaptador de lectura de feeds RSS."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import feedparser
import httpx

from .domain import Episode, FeedError, FeedSnapshot, FeedState
from .http import HttpClient

logger = logging.getLogger(__name__)


class RssFeedReader:
    """Llegeix un feed RSS de podcast i n'extreu els episodis.

    Fa GET condicional amb ETag/Last-Modified: si res no ha canviat, el servidor
    respon 304 i ens estalviem parsejar i transferir el feed sencer. Amb una
    execució cada hora això és la diferència entre ser un bon ciutadà de la xarxa
    i no ser-ho.
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def read(self, url: str, state: FeedState) -> FeedSnapshot:
        headers: dict[str, str] = {}
        if state.etag:
            headers["If-None-Match"] = state.etag
        if state.last_modified:
            headers["If-Modified-Since"] = state.last_modified

        try:
            response = self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise FeedError(f"No s'ha pogut llegir el feed {url}: {exc}") from exc

        if response.status_code == httpx.codes.NOT_MODIFIED:
            logger.debug("Feed sense canvis: %s", url)
            return FeedSnapshot(episodes=(), state=state, not_modified=True)

        if response.status_code >= 400:
            raise FeedError(f"El feed {url} ha respost HTTP {response.status_code}")

        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            raise FeedError(f"XML invàlid a {url}: {parsed.bozo_exception}")

        episodes = tuple(
            episode
            for entry in parsed.entries
            if (episode := self._to_episode(entry)) is not None
        )
        new_state = FeedState(
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
        )
        logger.debug("Feed %s: %d episodis", url, len(episodes))
        return FeedSnapshot(episodes=episodes, state=new_state)

    def _to_episode(self, entry: feedparser.FeedParserDict) -> Episode | None:
        audio_url, size_bytes = self._extract_enclosure(entry)
        if audio_url is None:
            logger.debug("Entrada sense àudio, s'ignora: %s", entry.get("title"))
            return None

        #  El guid del feed és preferible; si no hi és, la URL de l'àudio és
        #  prou estable per fer de clau.
        guid = str(entry.get("id") or audio_url).strip()

        return Episode(
            guid=guid,
            title=str(entry.get("title") or guid).strip(),
            audio_url=audio_url,
            published_at=self._extract_published(entry),
            duration_seconds=self._extract_duration(entry),
            size_bytes=size_bytes,
        )

    @staticmethod
    def _extract_enclosure(
        entry: feedparser.FeedParserDict,
    ) -> tuple[str | None, int | None]:
        for enclosure in entry.get("enclosures", ()):
            href = enclosure.get("href")
            if not href:
                continue
            if not str(enclosure.get("type", "")).startswith("audio/"):
                continue
            try:
                length = int(enclosure.get("length") or 0) or None
            except (TypeError, ValueError):
                length = None
            return href, length
        return None, None

    @staticmethod
    def _extract_published(entry: feedparser.FeedParserDict) -> datetime | None:
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        if struct is None:
            return None
        #  feedparser normalitza a UTC però retorna un time.struct_time naïf.
        return datetime(*struct[:6], tzinfo=UTC)

    @staticmethod
    def _extract_duration(entry: feedparser.FeedParserDict) -> int | None:
        raw = entry.get("itunes_duration")
        if not raw:
            return None
        parts = str(raw).split(":")
        try:
            numbers = [int(p) for p in parts]
        except ValueError:
            return None
        seconds = 0
        for number in numbers:  # suporta SS, MM:SS i HH:MM:SS
            seconds = seconds * 60 + number
        return seconds
