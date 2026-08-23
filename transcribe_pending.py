#!/usr/bin/env python3
"""Transcriu tots els MP3 d'una carpeta que encara no tenen fitxer .txt associat."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rac1sync.config import TranscriptionServiceType
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
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        logger.error("La carpeta %s no existeix", folder)
        sys.exit(1)

    pending = find_pending(folder)
    if not pending:
        logger.info("No hi ha MP3 pendents de transcripció a %s", folder)
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


if __name__ == "__main__":
    main()
