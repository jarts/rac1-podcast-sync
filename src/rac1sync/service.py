"""Cas d'ús: sincronitzar feeds amb el directori local."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .config import AppConfig, FeedConfig
from .domain import DownloadError, Episode, FeedError, SyncReport
from .ports import AudioDownloader, EpisodeRepository, FeedReader

logger = logging.getLogger(__name__)


class PodcastSyncService:
    """Orquestra lectura de feed, deduplicació i descàrrega.

    Les descàrregues van en threads (són I/O pur), però totes les escriptures a
    SQLite es fan al thread principal a mesura que arriben els resultats. Així
    s'evita compartir la connexió entre threads sense necessitat de locks.
    """

    def __init__(
        self,
        config: AppConfig,
        feed_reader: FeedReader,
        downloader: AudioDownloader,
        repository: EpisodeRepository,
    ) -> None:
        self._config = config
        self._feed_reader = feed_reader
        self._downloader = downloader
        self._repository = repository

    def sync_all(self) -> list[SyncReport]:
        reports = []
        for feed in self._config.feeds:
            try:
                reports.append(self.sync_feed(feed))
            except FeedError as exc:
                #  Un feed caigut no ha de tombar la resta.
                logger.error("%s", exc)
                reports.append(SyncReport(feed=feed.name, failed=1))
        return reports

    def sync_feed(self, feed: FeedConfig) -> SyncReport:
        state = self._repository.load_feed_state(feed.url)
        snapshot = self._feed_reader.read(feed.url, state)

        if snapshot.not_modified:
            logger.info("%s: sense canvis des de l'última comprovació", feed.name)
            return SyncReport(feed=feed.name)

        pending = [
            episode
            for episode in snapshot.episodes
            if not self._repository.is_downloaded(episode.guid, episode.audio_url)
        ]
        skipped = len(snapshot.episodes) - len(pending)

        pending.sort(key=lambda e: (e.published_at is None, e.published_at), reverse=True)
        limit = self._config.sync.max_episodes_per_run
        if limit and len(pending) > limit:
            logger.info(
                "%s: %d episodis nous, es limita a %d en aquesta execució",
                feed.name,
                len(pending),
                limit,
            )
            pending = pending[:limit]

        downloaded, failed, paths = self._download_all(pending, feed.name)

        #  L'estat del feed només es desa si tot ha anat bé. Si desem un ETag
        #  havent fallat descàrregues, el 304 següent amagaria els pendents.
        if failed == 0:
            self._repository.save_feed_state(feed.url, snapshot.state)
            
        # Transcripcions de les descàrregues completades
        if feed.transcriptionService and paths:
            from .transcription import get_transcription_service
            ts = get_transcription_service(feed.transcriptionService)
            if ts:
                logger.info("%s: Iniciant %d transcripcions en segon pla...", feed.name, len(paths))
                workers = min(self._config.sync.max_concurrent_downloads, len(paths))
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="transcription") as t_pool:
                    futures = [t_pool.submit(ts.transcribe, p) for p in paths]
                    for tf in futures:
                        try:
                            tf.result()
                        except Exception as e:
                            logger.error("Error en la transcripció: %s", e)

        report = SyncReport(
            feed=feed.name,
            discovered=len(snapshot.episodes),
            downloaded=downloaded,
            skipped=skipped,
            failed=failed,
        )
        logger.info("%s", report)
        return report

    def _download_all(self, episodes: list[Episode], feed_name: str) -> tuple[int, int, list[Path]]:
        if not episodes:
            return 0, 0, []

        downloaded = failed = 0
        paths = []
        workers = min(self._config.sync.max_concurrent_downloads, len(episodes))

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="download") as pool:
            futures: dict[Future[Path], Episode] = {
                pool.submit(self._downloader.download, episode, feed_name): episode
                for episode in episodes
            }
            for future in futures:
                episode = futures[future]
                try:
                    path = future.result()
                except DownloadError as exc:
                    logger.error("%s", exc)
                    failed += 1
                    continue
                except Exception:  # noqa: BLE001 - un episodi no pot tombar el lot
                    logger.exception("Error inesperat amb «%s»", episode.title)
                    failed += 1
                    continue

                self._repository.mark_downloaded(
                    episode, feed_name, path, path.stat().st_size
                )
                downloaded += 1
                paths.append(path)

        return downloaded, failed, paths
