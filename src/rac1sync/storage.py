"""Persistència en SQLite: índex d'episodis descarregats i estat dels feeds."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .domain import Episode, FeedState

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    guid          TEXT PRIMARY KEY,
    feed          TEXT NOT NULL,
    title         TEXT NOT NULL,
    audio_url     TEXT NOT NULL,
    published_at  TEXT,
    file_path     TEXT NOT NULL,
    size_bytes    INTEGER,
    downloaded_at TEXT NOT NULL
);

-- Clau de deduplicació secundària: protegeix si el feed reassigna guids.
CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_audio_url ON episodes (audio_url);
CREATE INDEX IF NOT EXISTS idx_episodes_feed ON episodes (feed, published_at DESC);

CREATE TABLE IF NOT EXISTS feed_state (
    feed_url      TEXT PRIMARY KEY,
    etag          TEXT,
    last_modified TEXT,
    checked_at    TEXT NOT NULL
);
"""


class SqliteEpisodeRepository:
    """Repositori d'episodis.

    El registre s'escriu *després* que el fitxer estigui al seu lloc definitiu.
    Si el procés mor a mitja descàrrega, l'episodi simplement es tornarà a
    intentar a l'execució següent: mai queda marcat com a fet sense ser-ho.
    """

    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(SCHEMA)

    def is_downloaded(self, guid: str, audio_url: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM episodes WHERE guid = ? OR audio_url = ? LIMIT 1",
            (guid, audio_url),
        ).fetchone()
        return row is not None

    def mark_downloaded(
        self, episode: Episode, feed_name: str, path: Path, size_bytes: int
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO episodes
                (guid, feed, title, audio_url, published_at, file_path, size_bytes, downloaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (guid) DO NOTHING
            """,
            (
                episode.guid,
                feed_name,
                episode.title,
                episode.audio_url,
                episode.published_at.isoformat() if episode.published_at else None,
                str(path),
                size_bytes,
                datetime.now(UTC).isoformat(),
            ),
        )

    def load_feed_state(self, feed_url: str) -> FeedState:
        row = self._connection.execute(
            "SELECT etag, last_modified FROM feed_state WHERE feed_url = ?",
            (feed_url,),
        ).fetchone()
        if row is None:
            return FeedState()
        return FeedState(etag=row["etag"], last_modified=row["last_modified"])

    def save_feed_state(self, feed_url: str, state: FeedState) -> None:
        self._connection.execute(
            """
            INSERT INTO feed_state (feed_url, etag, last_modified, checked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (feed_url) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                checked_at = excluded.checked_at
            """,
            (feed_url, state.etag, state.last_modified, datetime.now(UTC).isoformat()),
        )

    def list_episodes(self, feed_name: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
        if feed_name:
            return self._connection.execute(
                "SELECT * FROM episodes WHERE feed = ? ORDER BY published_at DESC LIMIT ?",
                (feed_name, limit),
            ).fetchall()
        return self._connection.execute(
            "SELECT * FROM episodes ORDER BY published_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SqliteEpisodeRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
