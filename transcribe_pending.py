#!/usr/bin/env python3
"""Transcriu tots els MP3 d'una carpeta que encara no tenen fitxer .txt associat.

Amb `--config` també en fa el resum amb Ollama, usant el `system_prompt` del
feed indicat.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rac1sync.config import AppConfig, FeedConfig, TranscriptionServiceType, load_config
from rac1sync.domain import Rac1SyncError
from rac1sync.summarization import get_summarization_service, summary_path_for
from rac1sync.transcription import get_transcription_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def find_pending(folder: Path) -> list[Path]:
    """Retorna els MP3 que no tenen cap .txt amb el mateix stem a la mateixa carpeta."""
    pending = []
    for mp3 in sorted(folder.rglob("*.mp3")):
        txt = mp3.with_suffix(".txt")
        if not txt.exists():
            pending.append(mp3)
    return pending


def find_unsummarized(folder: Path) -> list[Path]:
    """Retorna els .txt que encara no tenen resum."""
    return [
        txt
        for txt in sorted(folder.rglob("*.txt"))
        if not summary_path_for(txt).exists()
    ]


def pick_feed(config: AppConfig, name: str | None) -> FeedConfig:
    """Tria el feed del qual s'agafarà el system prompt."""
    if name:
        for feed in config.feeds:
            if feed.name == name:
                return feed
        raise Rac1SyncError(f"El feed '{name}' no és a la configuració")
    if len(config.feeds) != 1:
        noms = ", ".join(f.name for f in config.feeds)
        raise Rac1SyncError(f"Hi ha més d'un feed, indica'n un amb --feed: {noms}")
    return config.feeds[0]


def main() -> None:
    services = [s.value for s in TranscriptionServiceType]
    parser = argparse.ArgumentParser(description="Transcriu MP3 pendents d'una carpeta")
    parser.add_argument(
        "folder",
        nargs="?",
        default="data/podcasts",
        help="Carpeta amb els fitxers MP3 (per defecte: data/podcasts)",
    )
    parser.add_argument(
        "-s", "--service",
        default="Whisper",
        choices=services,
        help=f"Servei de transcripció (per defecte: Whisper). Opcions: {', '.join(services)}",
    )
    parser.add_argument(
        "-m", "--model",
        default="medium",
        help="Model Whisper a usar (per defecte: medium). Només aplica amb --service Whisper",
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        help="fitxer TOML de configuració; activa el resum amb Ollama",
    )
    parser.add_argument(
        "--feed",
        help="nom del feed del qual s'agafa el system_prompt (per defecte, l'únic que hi hagi)",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        logger.error("La carpeta %s no existeix", folder)
        sys.exit(1)

    summarizer = system_prompt = None
    if args.config:
        try:
            config = load_config(args.config)
            feed = pick_feed(config, args.feed)
        except Rac1SyncError as exc:
            logger.error("%s", exc)
            sys.exit(2)
        if not feed.system_prompt:
            logger.error("El feed '%s' no té 'system_prompt': no hi ha res per resumir", feed.name)
            sys.exit(2)
        summarizer = get_summarization_service(config.summary)
        system_prompt = feed.system_prompt
        logger.info("Resums activats amb el system_prompt de '%s'", feed.name)

    pending = find_pending(folder)
    if not pending:
        logger.info("No hi ha MP3 pendents de transcripció a %s", folder)
        if summarizer:
            summarize_all(summarizer, system_prompt, find_unsummarized(folder))
        return

    logger.info("Trobats %d MP3 pendents de transcripció", len(pending))

    service_type = TranscriptionServiceType(args.service)
    service = get_transcription_service(service_type)
    if service is None:
        logger.error("Servei de transcripció no disponible: %s", args.service)
        sys.exit(1)

    # Permet configurar el model si és Whisper
    if hasattr(service, '_model_name') and args.model:
        service._model_name = args.model

    logger.info("Usant servei: %s", args.service)
    ok = errors = 0

    for i, mp3 in enumerate(pending, 1):
        logger.info("[%d/%d] Transcrivint %s ...", i, len(pending), mp3.name)
        result = service.transcribe(mp3)
        if result:
            ok += 1
        else:
            errors += 1

    logger.info("Fet: %d correctes, %d errors de %d totals", ok, errors, len(pending))

    if summarizer:
        summarize_all(summarizer, system_prompt, find_unsummarized(folder))


def summarize_all(summarizer, system_prompt: str, transcripts: list[Path]) -> None:
    if not transcripts:
        logger.info("No hi ha transcripcions pendents de resum")
        return

    logger.info("Trobades %d transcripcions pendents de resum", len(transcripts))
    ok = errors = 0
    for i, txt in enumerate(transcripts, 1):
        logger.info("[%d/%d] Resumint %s ...", i, len(transcripts), txt.name)
        if summarizer.summarize(txt, system_prompt):
            ok += 1
        else:
            errors += 1

    logger.info("Resums: %d correctes, %d errors de %d totals", ok, errors, len(transcripts))


if __name__ == "__main__":
    main()
