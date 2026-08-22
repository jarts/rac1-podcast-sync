"""Model de domini. Objectes de valor immutables, sense dependències d'infraestructura."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Episode:
    """Un episodi tal com el descriu el feed, abans de descarregar-lo.

    `guid` és la clau natural de deduplicació: és l'identificador estable que
    publica el feed RSS. `audio_url` s'usa com a clau secundària per si un feed
    reassigna guids (passa quan la font migra de plataforma).
    """

    guid: str
    title: str
    audio_url: str
    published_at: datetime | None = None
    duration_seconds: int | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class FeedState:
    """Validadors HTTP de l'última lectura, per fer peticions condicionals."""

    etag: str | None = None
    last_modified: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.etag is None and self.last_modified is None


@dataclass(frozen=True, slots=True)
class FeedSnapshot:
    """Resultat de llegir un feed. `not_modified` indica un 304."""

    episodes: tuple[Episode, ...]
    state: FeedState
    not_modified: bool = False


@dataclass(frozen=True, slots=True)
class SyncReport:
    feed: str
    discovered: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"{self.feed}: {self.discovered} al feed, {self.downloaded} nous, "
            f"{self.skipped} ja existents, {self.failed} errors"
        )


class Rac1SyncError(Exception):
    """Arrel de la jerarquia d'errors de l'aplicació."""


class FeedError(Rac1SyncError):
    """El feed no s'ha pogut llegir o parsejar."""


class DownloadError(Rac1SyncError):
    """La descàrrega d'un àudio ha fallat de manera no recuperable."""
