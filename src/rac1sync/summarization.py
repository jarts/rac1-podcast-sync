"""Resum de les transcripcions amb el model local que serveix Ollama.

Ollama exposa una API HTTP a localhost; aquí només se'n fa servir `/api/chat`
en mode no-streaming. El system prompt no viu en aquest mòdul: arriba de la
configuració de cada feed, perquè és allà on se sap què s'espera del resum.

Un episodi sencer no hi cap en una sola crida, així que quan el transcrit passa
de `chunk_chars` es resumeix per trossos i es fon en una segona passada.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import httpx

from .config import SummaryConfig
from .domain import SummaryError

logger = logging.getLogger(__name__)

#  El resum acompanya l'MP3 i el .txt: episodi.mp3 / episodi.txt / episodi.resum.md
SUMMARY_SUFFIX = ".resum.md"

_PARTIAL_PROMPT = (
    "Fragment {index} de {total} de la transcripció d'un episodi. Resumeix-lo "
    "seguint les instruccions del sistema. Encara no és el resum definitiu: "
    "conserva els detalls que caldrà per fondre'l amb la resta de fragments.\n\n"
    "{text}"
)

_REDUCE_PROMPT = (
    "A continuació hi ha els resums parcials dels fragments d'un mateix "
    "episodi, en ordre. Fon-los en un únic resum final seguint les "
    "instruccions del sistema, sense repeticions ni cap referència als "
    "fragments.\n\n{text}"
)

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


class SummarizationService(Protocol):
    """Protocol per als serveis de resum."""

    def summarize(self, transcript_path: Path, system_prompt: str) -> Path | None:
        """Resumeix un fitxer .txt i retorna la ruta del resum resultant.
        Retorna None si el resum falla.
        """
        ...


class OllamaSummarizationService:
    """Implementació del servei de resum mitjançant Ollama local."""

    def __init__(self, config: SummaryConfig, transport: httpx.BaseTransport | None = None) -> None:
        self._config = config
        #  Punt d'injecció per als tests; en producció sempre és None.
        self._transport = transport

    def summarize(self, transcript_path: Path, system_prompt: str) -> Path | None:
        try:
            text = transcript_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.error("No s'ha pogut llegir %s: %s", transcript_path.name, exc)
            return None

        if not text:
            logger.warning("La transcripció %s és buida, no es resumeix", transcript_path.name)
            return None

        logger.info(
            "Resumint %s amb Ollama (%s)...", transcript_path.name, self._config.model
        )
        try:
            with self._open_client() as client:
                summary = self._summarize_text(client, text, system_prompt)
        except (SummaryError, httpx.HTTPError) as exc:
            logger.error("Error resumint %s: %s", transcript_path.name, exc)
            return None

        out_md = summary_path_for(transcript_path)
        try:
            out_md.write_text(summary + "\n", encoding="utf-8")
        except OSError as exc:
            logger.error("No s'ha pogut desar el resum a %s: %s", out_md.name, exc)
            return None

        logger.info("Resum desat a %s", out_md.name)
        return out_md

    def _open_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._config.base_url,
            timeout=httpx.Timeout(self._config.timeout_seconds),
            transport=self._transport,
        )

    def _summarize_text(self, client: httpx.Client, text: str, system_prompt: str) -> str:
        chunks = split_text(text, self._config.chunk_chars)
        if len(chunks) == 1:
            return self._chat(client, system_prompt, chunks[0])

        logger.info(
            "Transcripció de %d caràcters: es resumeix en %d trossos i es fon",
            len(text),
            len(chunks),
        )
        partials = [
            self._chat(
                client,
                system_prompt,
                _PARTIAL_PROMPT.format(index=i, total=len(chunks), text=chunk),
            )
            for i, chunk in enumerate(chunks, 1)
        ]
        return self._chat(client, system_prompt, _REDUCE_PROMPT.format(text="\n\n".join(partials)))

    def _chat(self, client: httpx.Client, system_prompt: str, user_content: str) -> str:
        response = client.post(
            "/api/chat",
            json={
                "model": self._config.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "options": {
                    "temperature": self._config.temperature,
                    "num_ctx": self._config.num_ctx,
                },
            },
        )

        #  Ollama respon 404 quan el model no s'ha descarregat mai; l'error per
        #  defecte ("Not Found") no diu què cal fer.
        if response.status_code == 404:
            raise SummaryError(
                f"Ollama no té el model «{self._config.model}»: "
                f"executa `ollama pull {self._config.model}`"
            )
        if response.status_code >= 400:
            raise SummaryError(f"Ollama ha respost HTTP {response.status_code}: {response.text}")

        content = str(response.json().get("message", {}).get("content", "")).strip()
        if not content:
            raise SummaryError("Ollama ha respost sense contingut")
        return content


def summary_path_for(transcript_path: Path) -> Path:
    """Ruta del resum corresponent a una transcripció."""
    return transcript_path.with_name(transcript_path.stem + SUMMARY_SUFFIX)


def split_text(text: str, max_chars: int) -> list[str]:
    """Parteix el text en trossos de com a molt `max_chars`.

    Talla per paràgraf i, si un paràgraf no hi cap, per frase; només arriba a
    tallar per paraula si una frase sola ja supera el límit. Mai enmig d'una
    paraula, perquè el model no rebi text mutilat.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for atom in _atoms(text, max_chars):
        if current and size + len(atom) + 1 > max_chars:
            chunks.append(" ".join(current))
            current, size = [], 0
        current.append(atom)
        size += len(atom) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def _atoms(text: str, max_chars: int) -> Iterator[str]:
    """Unitats indivisibles del text, totes de mida <= max_chars."""
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            yield paragraph
            continue
        for sentence in _SENTENCE_END.split(paragraph):
            if len(sentence) <= max_chars:
                yield sentence
            else:
                yield from _by_words(sentence, max_chars)


def _by_words(text: str, max_chars: int) -> Iterator[str]:
    current: list[str] = []
    size = 0
    for word in text.split():
        if current and size + len(word) + 1 > max_chars:
            yield " ".join(current)
            current, size = [], 0
        current.append(word)
        size += len(word) + 1
    if current:
        yield " ".join(current)


def get_summarization_service(config: SummaryConfig) -> SummarizationService:
    """Factoria del servei de resum. De moment només hi ha Ollama local."""
    return OllamaSummarizationService(config)
