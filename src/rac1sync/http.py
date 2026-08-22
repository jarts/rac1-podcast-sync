"""Client HTTP compartit: timeouts, reintents amb backoff i User-Agent identificable."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from .config import HttpConfig

logger = logging.getLogger(__name__)

#  Codis on té sentit reintentar: throttling i errors transitoris de servidor.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class HttpClient:
    """Embolcall prim sobre httpx.Client amb política de reintents.

    Es reintenta només allò que és transitori; un 404 falla immediatament perquè
    reintentar-lo només afegeix latència i càrrega a l'origen.
    """

    def __init__(self, config: HttpConfig) -> None:
        self._config = config
        self._client = httpx.Client(
            timeout=httpx.Timeout(config.timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": config.user_agent},
        )

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        response = self._execute(lambda: self._client.get(url, **kwargs))
        return response

    @contextmanager
    def stream(self, url: str, **kwargs: Any) -> Iterator[httpx.Response]:
        """Obre una resposta en streaming. Els reintents cobreixen l'establiment
        de la connexió; un tall a mitja transferència el gestiona qui consumeix."""
        attempt = 0
        while True:
            attempt += 1
            manager = self._client.stream("GET", url, **kwargs)
            try:
                response = manager.__enter__()
            except httpx.TransportError as exc:
                if attempt > self._config.max_retries:
                    raise
                self._sleep(attempt, reason=str(exc))
                continue

            if response.status_code in RETRYABLE_STATUS and attempt <= self._config.max_retries:
                manager.__exit__(None, None, None)
                self._sleep(attempt, reason=f"HTTP {response.status_code}")
                continue

            try:
                yield response
            finally:
                manager.__exit__(None, None, None)
            return

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _execute(self, call: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries + 2):
            try:
                response = call()
            except httpx.TransportError as exc:
                last_error = exc
                if attempt > self._config.max_retries:
                    raise
                self._sleep(attempt, reason=str(exc))
                continue

            if response.status_code in RETRYABLE_STATUS and attempt <= self._config.max_retries:
                self._sleep(attempt, reason=f"HTTP {response.status_code}")
                continue
            return response

        raise RuntimeError("bucle de reintents inconsistent") from last_error

    def _sleep(self, attempt: int, *, reason: str) -> None:
        #  Backoff exponencial amb jitter per no sincronitzar reintents.
        delay = self._config.backoff_base_seconds * (2 ** (attempt - 1))
        delay += random.uniform(0, delay * 0.25)
        logger.warning("Reintent %d en %.1fs (%s)", attempt, delay, reason)
        time.sleep(delay)
