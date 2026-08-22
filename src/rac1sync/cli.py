"""Punt d'entrada: parseig d'arguments i muntatge de dependències."""

from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import time
from pathlib import Path
from types import FrameType

from .config import AppConfig, load_config
from .domain import Rac1SyncError
from .downloader import HttpAudioDownloader
from .feed import RssFeedReader
from .http import HttpClient
from .service import PodcastSyncService
from .storage import SqliteEpisodeRepository

logger = logging.getLogger("rac1sync")

_stop_requested = False


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose)

    try:
        config = load_config(args.config)
    except Rac1SyncError as exc:
        logger.error("%s", exc)
        return 2

    if args.command == "list":
        return _run_list(config, args.feed, args.limit)

    _install_signal_handlers()
    if args.command == "daemon":
        return _run_daemon(config)
    return _run_once(config)


def _run_once(config: AppConfig) -> int:
    with _build_service(config) as service:
        reports = service.sync_all()
    return 1 if any(report.failed for report in reports) else 0


def _run_daemon(config: AppConfig) -> int:
    interval = config.sync.interval_minutes * 60
    logger.info("Mode dimoni: sincronització cada %d minuts", config.sync.interval_minutes)
    while not _stop_requested:
        _run_once(config)
        #  Jitter: evita que múltiples instàncies piquin l'origen alhora.
        delay = interval + random.uniform(0, interval * 0.1)
        deadline = time.monotonic() + delay
        while not _stop_requested and time.monotonic() < deadline:
            time.sleep(1)
    logger.info("Aturada neta")
    return 0


def _run_list(config: AppConfig, feed: str | None, limit: int) -> int:
    with SqliteEpisodeRepository(config.storage.database) as repository:
        rows = repository.list_episodes(feed, limit)
    if not rows:
        print("Encara no hi ha cap episodi descarregat.")
        return 0
    for row in rows:
        published = (row["published_at"] or "")[:10] or "----------"
        print(f"{published}  {row['feed']:<32}  {row['title']}")
    return 0


class _ServiceContext:
    """Manté vius el client HTTP i la connexió mentre s'executa el servei."""

    def __init__(self, config: AppConfig) -> None:
        self._http = HttpClient(config.http)
        self._repository = SqliteEpisodeRepository(config.storage.database)
        self._service = PodcastSyncService(
            config=config,
            feed_reader=RssFeedReader(self._http),
            downloader=HttpAudioDownloader(self._http, config.storage.download_dir),
            repository=self._repository,
        )

    def __enter__(self) -> PodcastSyncService:
        return self._service

    def __exit__(self, *_: object) -> None:
        self._repository.close()
        self._http.close()


def _build_service(config: AppConfig) -> _ServiceContext:
    return _ServiceContext(config)


def _install_signal_handlers() -> None:
    def handler(_signum: int, _frame: FrameType | None) -> None:
        global _stop_requested
        _stop_requested = True
        logger.info("Senyal rebut, s'acabarà la feina en curs...")

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rac1sync",
        description="Descarrega els podcasts de RAC1 sense repetits.",
    )
    parser.add_argument(
        "-c", "--config", type=Path, default=Path("config.toml"), help="fitxer TOML de configuració"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="registre detallat")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("sync", help="sincronitza un cop i surt (per a cron/systemd)")
    subparsers.add_parser("daemon", help="procés llarg que sincronitza periòdicament")

    list_parser = subparsers.add_parser("list", help="mostra els episodis ja descarregats")
    list_parser.add_argument("--feed", help="filtra per nom de feed")
    list_parser.add_argument("--limit", type=int, default=50)

    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "sync"
    return args


if __name__ == "__main__":
    sys.exit(main())
