from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dagster import AssetSelection, Definitions, ScheduleDefinition, define_asset_job

from . import config

# dbt reads these through env_var() in profiles.yml and the source definitions.
# Set them before the dbt project is parsed (assets.py prepares it on import).
os.environ.setdefault("ZEPHYRO_RAW_DIR", config.raw_dir().as_posix())
os.environ.setdefault("ZEPHYRO_WAREHOUSE_PATH", config.warehouse_path().as_posix())
config.warehouse_path().parent.mkdir(parents=True, exist_ok=True)

from dagster_dbt import DbtCliResource  # noqa: E402

from .assets import dbt_models, dbt_project, raw_extract  # noqa: E402


def _dbt_executable() -> str:
    # The dbt installed next to this interpreter, so an unactivated venv still works.
    local = Path(sys.executable).parent / ("dbt.exe" if os.name == "nt" else "dbt")
    return str(local) if local.exists() else (shutil.which("dbt") or "dbt")


telemetry_job =define_asset_job("telemetry_pipeline", selection=AssetSelection.all())

defs = Definitions(
    assets=[raw_extract, dbt_models],
    jobs=[telemetry_job],
    schedules=[ScheduleDefinition(job=telemetry_job, cron_schedule="0 6 * * *", execution_timezone="UTC")],
    resources={"dbt": DbtCliResource(project_dir=dbt_project, dbt_executable=_dbt_executable())},
)
