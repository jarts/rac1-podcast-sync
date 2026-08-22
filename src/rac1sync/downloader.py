"""Descàrrega d'àudio a disc, en streaming i de manera atòmica."""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from .domain import DownloadError, Episode
from .http import HttpClient

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024
MAX_STEM_LENGTH = 120
#  Es conserven el punt volat i l'apòstrof: són lletres, no soroll, en català.
_UNSAFE = re.compile(r"[^\w.\-·' ]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


class HttpAudioDownloader:
    """Escriu primer a `<nom>.part` i fa `os.replace` en acabar.

    `os.replace` és atòmic dins del mateix sistema de fitxers, així que al
    directori de destí no hi apareix mai un MP3 truncat: o hi és sencer o no hi és.
    """

    def __init__(self, http: HttpClient, download_dir: Path) -> None:
        self._http = http
        self._download_dir = download_dir

    def download(self, episode: Episode, feed_name: str) -> Path:
        target = self._target_path(episode, feed_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")

        try:
            written = self._stream_to(episode.audio_url, partial)
        except (httpx.HTTPError, OSError) as exc:
            partial.unlink(missing_ok=True)
            raise DownloadError(f"Error descarregant «{episode.title}»: {exc}") from exc

        if written == 0:
            partial.unlink(missing_ok=True)
            raise DownloadError(f"Resposta buida per a «{episode.title}»")

        os.replace(partial, target)
        logger.info("Descarregat %s (%.1f MB)", target.name, written / 1_048_576)
        return target

    def _stream_to(self, url: str, destination: Path) -> int:
        written = 0
        with self._http.stream(url) as response:
            if response.status_code >= 400:
                raise DownloadError(f"HTTP {response.status_code} en descarregar {url}")

            expected = response.headers.get("Content-Length")
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes(CHUNK_SIZE):
                    handle.write(chunk)
                    written += len(chunk)

            #  Detecta transferències tallades: sense això et quedes amb àudios
            #  incomplets marcats com a descarregats correctament.
            if expected is not None and written != int(expected):
                raise DownloadError(
                    f"Descàrrega incompleta de {url}: {written} de {expected} bytes"
                )
        return written

    def _target_path(self, episode: Episode, feed_name: str) -> Path:
        date_prefix = (
            episode.published_at.strftime("%Y-%m-%d") if episode.published_at else "sense-data"
        )
        stem = f"{date_prefix}_{slugify(episode.title)}"[:MAX_STEM_LENGTH]
        return self._download_dir / slugify(feed_name) / f"{stem}{extension_of(episode.audio_url)}"


def slugify(value: str) -> str:
    """Nom de fitxer segur i portable, conservant accents i ela geminada."""
    normalized = unicodedata.normalize("NFC", value)
    cleaned = _UNSAFE.sub("", normalized)
    cleaned = _SPACES.sub("-", cleaned).strip("-. ")
    return cleaned or "sense-titol"


def extension_of(url: str, default: str = ".mp3") -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    return suffix if suffix in {".mp3", ".m4a", ".ogg", ".aac", ".wav"} else default
