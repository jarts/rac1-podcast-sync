"""Ports (interfícies) que separen el cas d'ús dels adaptadors concrets.

Són `Protocol`s estructurals: els adaptadors no els han d'heretar, només
complir la signatura. Això manté els tests lliures d'infraestructura.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .domain import Episode, FeedSnapshot, FeedState


@runtime_checkable
class FeedReader(Protocol):
    def read(self, url: str, state: FeedState) -> FeedSnapshot:
        """Llegeix el feed i retorna els episodis publicats.

        Ha de fer una petició condicional amb `state` i marcar
        `not_modified=True` si el servidor respon 304.
        """
        ...


@runtime_checkable
class AudioDownloader(Protocol):
    def download(self, episode: Episode, feed_name: str) -> Path:
        """Descarrega l'àudio i retorna la ruta final. Ha de ser atòmic:
        cap fitxer visible al directori de destí si la descàrrega falla."""
        ...


@runtime_checkable
class EpisodeRepository(Protocol):
    def is_downloaded(self, guid: str, audio_url: str) -> bool: ...

    def mark_downloaded(
        self, episode: Episode, feed_name: str, path: Path, size_bytes: int
    ) -> None: ...

    def load_feed_state(self, feed_url: str) -> FeedState: ...

    def save_feed_state(self, feed_url: str, state: FeedState) -> None: ...
