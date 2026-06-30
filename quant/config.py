"""Central configuration for QuantLab.

Values are loaded from ``config/quant.env`` and may be overridden by process
environment variables. Keeping the parser in the standard library makes the
same configuration available to scripts, FastAPI, tests, and service startup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config" / "quant.env"


def load_env_file(path: Path | str | None = None, override: bool = False) -> Path:
    config_path = Path(
        path or os.environ.get("QUANT_CONFIG_FILE", DEFAULT_CONFIG_FILE)
    ).expanduser()
    if not config_path.exists():
        return config_path

    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        if key and (override or key not in os.environ):
            os.environ[key] = value
    return config_path


def _text(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _integer(name: str, default: int, minimum: int | None = None) -> int:
    value = int(_text(name, str(default)))
    return max(minimum, value) if minimum is not None else value


def _float(name: str, default: float, minimum: float | None = None) -> float:
    value = float(_text(name, str(default)))
    return max(minimum, value) if minimum is not None else value


def _boolean(name: str, default: bool = False) -> bool:
    raw = _text(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in _text(name, default).split(",") if value.strip())


@dataclass(frozen=True)
class AppSettings:
    backend_host: str
    backend_port: int
    frontend_host: str
    frontend_port: int
    cors_origins: tuple[str, ...]
    reload: bool
    memory_high: str
    memory_max: str


@dataclass(frozen=True)
class TushareSettings:
    token: str
    rpm: int
    fetch_adj_factor: bool
    workers: int
    retries: int
    timeout: float


@dataclass(frozen=True)
class TdxSettings:
    host: str
    port: int
    connect_timeout: float
    max_host_attempts: int
    host_cooldown: float
    workers: int
    request_retries: int
    empty_response_threshold: int
    probe_timeout: float
    probe_workers: int
    active_hosts: int
    host_cache_seconds: int


@dataclass(frozen=True)
class DataSettings:
    akshare_workers: int
    baostock_workers: int
    memory_budget_gb: float
    estimated_worker_mb: int
    request_delay: float
    max_consecutive_errors: int


@dataclass(frozen=True)
class AgentSettings:
    provider: str
    model: str
    anthropic_api_key: str
    deepseek_api_key: str
    deepseek_base_url: str
    max_tokens: int


@dataclass(frozen=True)
class FutuSettings:
    host: str
    port: int


@dataclass(frozen=True)
class Settings:
    config_file: Path
    app: AppSettings
    tushare: TushareSettings
    tdx: TdxSettings
    data: DataSettings
    agent: AgentSettings
    futu: FutuSettings

    def workers_for(self, source: str) -> int:
        return {
            "tdx": self.tdx.workers,
            "tushare": self.tushare.workers,
            "akshare": self.data.akshare_workers,
            "baostock": self.data.baostock_workers,
        }.get(source, 1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    config_file = load_env_file()
    return Settings(
        config_file=config_file,
        app=AppSettings(
            backend_host=_text("QUANT_BACKEND_HOST", "0.0.0.0"),
            backend_port=_integer("QUANT_BACKEND_PORT", 8001, 1),
            frontend_host=_text("QUANT_FRONTEND_HOST", "0.0.0.0"),
            frontend_port=_integer("QUANT_FRONTEND_PORT", 5174, 1),
            cors_origins=_csv(
                "QUANT_CORS_ORIGINS",
                "http://localhost:5174,http://127.0.0.1:5174",
            ),
            reload=_boolean("QUANT_RELOAD"),
            memory_high=_text("QUANT_MEMORY_HIGH", "9G"),
            memory_max=_text("QUANT_MEMORY_MAX", "10G"),
        ),
        tushare=TushareSettings(
            token=_text("TUSHARE_TOKEN"),
            rpm=_integer("TUSHARE_RPM", 45, 1),
            fetch_adj_factor=_boolean("TUSHARE_FETCH_ADJ_FACTOR"),
            workers=_integer("TUSHARE_WORKERS", 4, 1),
            retries=_integer("TUSHARE_RETRIES", 2, 0),
            timeout=_float("TUSHARE_TIMEOUT", 20.0, 1.0),
        ),
        tdx=TdxSettings(
            host=_text("TDX_HOST"),
            port=_integer("TDX_PORT", 7709, 1),
            connect_timeout=_float("TDX_CONNECT_TIMEOUT", 2.0, 0.1),
            max_host_attempts=_integer("TDX_MAX_HOST_ATTEMPTS", 4, 1),
            host_cooldown=_float("TDX_HOST_COOLDOWN", 120.0, 0.0),
            workers=_integer("TDX_WORKERS", 4, 1),
            request_retries=_integer("TDX_REQUEST_RETRIES", 2, 0),
            empty_response_threshold=_integer(
                "TDX_EMPTY_RESPONSE_THRESHOLD", 3, 1
            ),
            probe_timeout=_float("TDX_PROBE_TIMEOUT", 0.8, 0.1),
            probe_workers=_integer("TDX_PROBE_WORKERS", 24, 1),
            active_hosts=_integer("TDX_ACTIVE_HOSTS", 8, 1),
            host_cache_seconds=_integer("TDX_HOST_CACHE_SECONDS", 21600, 0),
        ),
        data=DataSettings(
            akshare_workers=_integer("AKSHARE_WORKERS", 4, 1),
            baostock_workers=_integer("BAOSTOCK_WORKERS", 1, 1),
            memory_budget_gb=_float("DATA_MEMORY_BUDGET_GB", 10.0, 1.0),
            estimated_worker_mb=_integer("DATA_ESTIMATED_WORKER_MB", 256, 1),
            request_delay=_float("DATA_REQUEST_DELAY", 0.08, 0.0),
            max_consecutive_errors=_integer(
                "DATA_MAX_CONSECUTIVE_ERRORS", 12, 1
            ),
        ),
        agent=AgentSettings(
            provider=_text("AGENT_PROVIDER", "deepseek").lower(),
            model=_text("AGENT_MODEL", "deepseek-v4-flash"),
            anthropic_api_key=_text("ANTHROPIC_API_KEY"),
            deepseek_api_key=_text("DEEPSEEK_API_KEY"),
            deepseek_base_url=_text(
                "DEEPSEEK_BASE_URL",
                "https://api.deepseek.com/v1",
            ),
            max_tokens=_integer("AGENT_MAX_TOKENS", 4096, 1),
        ),
        futu=FutuSettings(
            host=_text("FUTU_HOST", "127.0.0.1"),
            port=_integer("FUTU_PORT", 11111, 1),
        ),
    )


def reset_settings() -> None:
    """Clear cached settings, primarily for tests."""
    get_settings.cache_clear()
