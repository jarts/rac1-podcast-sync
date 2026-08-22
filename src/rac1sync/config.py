"""Configuració tipada, carregada d'un TOML amb sobreescriptura per entorn.

Equivalent conceptual a `@ConfigurationProperties`: es valida un cop a l'arrencada
i a partir d'aquí l'aplicació treballa amb objectes immutables.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .domain import Rac1SyncError

DEFAULT_USER_AGENT = "rac1-podcast-sync/1.0 (+https://github.com/exemple/rac1-podcast-sync)"


class ConfigError(Rac1SyncError):
    """La configuració és absent, mal formada o incoherent."""


@dataclass(frozen=True, slots=True)
class FeedConfig:
    name: str
    url: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigError("Cada feed necessita un 'name' no buit")
        if not self.url.startswith(("http://", "https://")):
            raise ConfigError(f"URL de feed invàlida per a '{self.name}': {self.url}")


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
class AppConfig:
    feeds: tuple[FeedConfig, ...]
    storage: StorageConfig = field(default_factory=StorageConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)

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
        )
    except TypeError as exc:  # clau desconeguda al TOML
        raise ConfigError(f"Clau de configuració no reconeguda: {exc}") from exc
