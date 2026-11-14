"""Dagster assets: incremental extraction into the landing zone, then dbt.

The extraction asset keys (``raw/api_calls``...) match the dbt source
``raw.<table>``, so Dagster draws one lineage graph from SQLite to the marts.
"""
# No `from __future__ import annotations` here: Dagster inspects the `context`
# annotation at definition time and rejects it as a string.
from datetime import datetime, timezone

from dagster import AssetExecutionContext, AssetKey, AssetSpec, MaterializeResult, MetadataValue, multi_asset
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

from . import config, extract, synthetic

RAW_TABLES = ("api_calls", "requests", "request_steps", "turns")

dbt_project = DbtProject(project_dir=config.DBT_DIR, profiles_dir=config.DBT_DIR)
dbt_project.prepare_if_dev()


@multi_asset(
    specs=[
        AssetSpec(
            AssetKey(["raw", name]),
            group_name="landing",
            kinds={"python", "parquet"},
            description=f"Incremental Parquet extract of `{name}`, PII stripped.",
        )
        for name in RAW_TABLES
    ],
    can_subset=False,
)
def raw_extract(context: AssetExecutionContext):
    mode = config.source_mode()
    if mode == "synthetic":
        added = synthetic.ensure_until(config.synthetic_dir(), datetime.now(timezone.utc).date())
        context.log.info(f"synthetic sources: {len(added)} new day(s)")

    dbs = config.source_dbs(mode)
    salt = config.pii_salt()
    state_path = config.state_file(mode)
    marks = extract.load_watermarks(state_path)
    loaded_at = datetime.now(timezone.utc)
    run_id = context.run_id[:8]

    calls, calls_mark = extract.extract_api_calls(dbs.api_usage, marks.get("api_calls", 0))
    requests, steps, requests_mark = extract.extract_requests(
        dbs.task_envelopes, marks.get("requests", {}), salt)
    turns, turns_mark = extract.extract_turns(dbs.conversation_history, marks.get("turns", 0), salt)

    batches = {"api_calls": calls, "requests": requests, "request_steps": steps, "turns": turns}
    written = {name: extract.write_batch(b, config.raw_dir(mode), run_id, loaded_at)
               for name, b in batches.items()}

    # Watermarks move only after every file is on disk: a failed run re-extracts
    # the same rows next time, and staging deduplicates them.
    extract.save_watermarks(state_path, {"api_calls": calls_mark, "requests": requests_mark,
                                         "turns": turns_mark})

    for name, batch in batches.items():
        yield MaterializeResult(
            asset_key=AssetKey(["raw", name]),
            metadata={
                "source_mode": mode,
                "new_rows": len(batch.rows),
                "file": MetadataValue.path(str(written[name])) if written[name] else "no new rows",
            },
        )


@dbt_assets(manifest=dbt_project.manifest_path)
def dbt_models(context: AssetExecutionContext, dbt: DbtCliResource):
    # `build` = seed + run + test in DAG order: a failing test on a staging model
    # stops the marts built on top of it.
    yield from dbt.cli(["build"], context=context).stream()
