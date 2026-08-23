"""Configuració tipada, carregada d'un TOML amb sobreescriptura per entorn.

Equivalent conceptual a `@ConfigurationProperties`: es valida un cop a l'arrencada
i a partir d'aquí l'aplicació treballa amb objectes immutables.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .domain import Rac1SyncError

DEFAULT_USER_AGENT = "rac1-podcast-sync/1.0 (+https://github.com/exemple/rac1-podcast-sync)"


class ConfigError(Rac1SyncError):
    """La configuració és absent, mal formada o incoherent."""


class TranscriptionServiceType(str, Enum):
    SoftCatala = "SoftCatala"
    Olama = "Olama"
    Whisper = "Whisper"


@dataclass(frozen=True, slots=True)
class FeedConfig:
    name: str
    url: str
    transcriptionService: TranscriptionServiceType | None = None
    #  Instruccions que rep el model en resumir aquest feed. Va per feed i no
    #  global perquè un magazín i una secció d'història no volen el mateix
    #  resum. Sense prompt no es genera cap resum.
    system_prompt: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigError("Cada feed necessita un 'name' no buit")
        if not self.url.startswith(("http://", "https://")):
            raise ConfigError(f"URL de feed invàlida per a '{self.name}': {self.url}")

        if self.transcriptionService is not None and not isinstance(self.transcriptionService, TranscriptionServiceType):
            try:
                parsed = TranscriptionServiceType(self.transcriptionService)
                object.__setattr__(self, "transcriptionService", parsed)
            except ValueError:
                raise ConfigError(f"Servei de transcripció no suportat per a '{self.name}': {self.transcriptionService}")

        if self.system_prompt is not None:
            prompt = self.system_prompt.strip()
            if not prompt:
                raise ConfigError(f"El 'system_prompt' de '{self.name}' és buit")
            object.__setattr__(self, "system_prompt", prompt)


@dataclass(frozen=True, slots=True)
class HttpConfig:
    timeout_seconds: float = 30.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    user_agent: str = DEFAULT_USER_AGENT


@dataclass(frozen=True, slots=True)
class StorageConfig:
    database: Path = Path("data/rac1.sqlite3")
    download_dir: Path = Path("data/podcasts")


@dataclass(frozen=True, slots=True)
class SyncConfig:
    max_concurrent_downloads: int = 2
    #  0 = sense límit. Útil per no descarregar tot l'arxiu la primera execució.
    max_episodes_per_run: int = 0
    interval_minutes: int = 60

    def __post_init__(self) -> None:
        if self.max_concurrent_downloads < 1:
            raise ConfigError("max_concurrent_downloads ha de ser >= 1")
        if self.interval_minutes < 1:
            raise ConfigError("interval_minutes ha de ser >= 1")


@dataclass(frozen=True, slots=True)
class SummaryConfig:
    """Resum del transcrit amb el model local que serveix Ollama."""

    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:latest"
    #  Un episodi de ràdio pot ocupar el model uns quants minuts.
    timeout_seconds: float = 600.0
    temperature: float = 0.2
    #  Ollama es queda a 4096 si no li diem res, massa poc per a un episodi
    #  sencer: sense això el principi de la transcripció cau de la finestra.
    num_ctx: int = 16384
    #  Els transcrits que no hi caben es resumeixen per trossos i es fonen
    #  després en una segona passada.
    chunk_chars: int = 12000

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ConfigError(f"URL d'Ollama invàlida: {self.base_url}")
        if not self.model.strip():
            raise ConfigError("summary.model no pot ser buit")
        if self.chunk_chars < 1000:
            raise ConfigError("summary.chunk_chars ha de ser >= 1000")


@dataclass(frozen=True, slots=True)
class AppConfig:
    feeds: tuple[FeedConfig, ...]
    storage: StorageConfig = field(default_factory=StorageConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    summary: SummaryConfig = field(default_factory=SummaryConfig)

    def __post_init__(self) -> None:
        if not self.feeds:
            raise ConfigError("Cal declarar com a mínim un [[feeds]]")
        names = [f.name for f in self.feeds]
        duplicats = {n for n in names if names.count(n) > 1}
        if duplicats:
            raise ConfigError(f"Noms de feed duplicats: {', '.join(sorted(duplicats))}")


def load_config(path: Path) -> AppConfig:
    """Carrega el TOML. Les variables RAC1SYNC_DB i RAC1SYNC_DOWNLOAD_DIR el sobreescriuen."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"No s'ha trobat el fitxer de configuració: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML invàlid a {path}: {exc}") from exc

    storage_raw = raw.get("storage", {})
    storage = StorageConfig(
        database=Path(os.getenv("RAC1SYNC_DB") or storage_raw.get("database", "data/rac1.sqlite3")),
        download_dir=Path(
            os.getenv("RAC1SYNC_DOWNLOAD_DIR")
            or storage_raw.get("download_dir", "data/podcasts")
        ),
    )

    try:
        return AppConfig(
            feeds=tuple(FeedConfig(**feed) for feed in raw.get("feeds", [])),
            storage=storage,
            http=HttpConfig(**raw.get("http", {})),
            sync=SyncConfig(**raw.get("sync", {})),
            summary=SummaryConfig(**raw.get("summary", {})),
        )
    except TypeError as exc:  # clau desconeguda al TOML
        raise ConfigError(f"Clau de configuració no reconeguda: {exc}") from exc
