"""Where the pipeline reads from and writes to.

Two source modes:
- ``synthetic`` (default): SQLite files produced by ``synthetic.py``, same schema as
  Zephyro's. This is what CI and the public repo run.
- ``real``: Zephyro's own ``storage/*.db``. Never leaves the machine: the data dir
  is gitignored and the extractor drops every free-text column.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ANALYTICS_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = ANALYTICS_DIR.parent
DBT_DIR = ANALYTICS_DIR / "dbt"

SOURCE_ENV = "ZEPHYRO_ANALYTICS_SOURCE"
DATA_ENV = "ZEPHYRO_ANALYTICS_DATA"
SALT_ENV = "ZEPHYRO_ANALYTICS_SALT"


@dataclass(frozen=True)
class SourceDbs:
    api_usage: Path
    task_envelopes: Path
    conversation_history: Path


def source_mode() -> str:
    mode = os.getenv(SOURCE_ENV, "synthetic").strip().lower()
    if mode not in ("synthetic", "real"):
        raise ValueError(f"{SOURCE_ENV} must be 'synthetic' or 'real', got {mode!r}")
    return mode


def data_dir() -> Path:
    return Path(os.getenv(DATA_ENV, ANALYTICS_DIR / "data")).resolve()


def mode_dir(mode: str | None = None) -> Path:
    # Each mode gets its own landing zone, watermarks and warehouse: switching to
    # `real` never mixes personal rows into the synthetic warehouse, or the reverse.
    return data_dir() / (mode or source_mode())


def raw_dir(mode: str | None = None) -> Path:
    return mode_dir(mode) / "raw"


def state_file(mode: str | None = None) -> Path:
    return mode_dir(mode) / "state" / "watermarks.json"


def warehouse_path(mode: str | None = None) -> Path:
    return mode_dir(mode) / "warehouse.duckdb"


def synthetic_dir() -> Path:
    return data_dir() / "synthetic_sources"


def source_dbs(mode: str | None = None) -> SourceDbs:
    base = REPO_DIR / "storage" if (mode or source_mode()) == "real" else synthetic_dir()
    return SourceDbs(
        api_usage=base / "api_usage.db",
        task_envelopes=base / "task_envelopes.db",
        conversation_history=base / "conversation_history.db",
    )


def pii_salt() -> str:
    # The synthetic default keeps CI deterministic; set a private salt for real data.
    return os.getenv(SALT_ENV, "zephyro-synthetic-salt")
