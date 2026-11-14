"""Tests de memory_archival."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gzip
import json
import os
import time

import pytest

from modules.memory_extended.memory_archival import MemoryArchival


@pytest.fixture
def archival(tmp_path):
    return MemoryArchival(
        sessions_dir=tmp_path / "sessions",
        archive_dir=tmp_path / "archive",
        max_age_days=90.0,
    )


def _write_session(archival: MemoryArchival, name: str, age_days: float, content: dict):
    archival.sessions_dir.mkdir(parents=True, exist_ok=True)
    p = archival.sessions_dir / f"{name}.json"
    p.write_text(json.dumps(content), encoding="utf-8")
    # Forzar mtime
    age_ts = time.time() - (age_days * 86400)
    os.utime(p, (age_ts, age_ts))
    return p


@pytest.mark.asyncio
async def test_move_cold_archives_old_sessions(archival):
    old = _write_session(archival, "old1", age_days=120, content={"x": 1})
    new = _write_session(archival, "new1", age_days=10, content={"x": 2})

    result = await archival.move_cold()
    assert result["moved"] == 1
    assert result["errors"] == 0

    # El viejo debe estar movido
    assert not old.exists()
    assert (archival.archive_dir / "old1.json.gz").exists()
    # El nuevo se queda
    assert new.exists()


@pytest.mark.asyncio
async def test_archived_content_preserved(archival):
    payload = {"messages": [{"role": "user", "text": "hola"}]}
    _write_session(archival, "preserved", age_days=100, content=payload)

    await archival.move_cold()

    archive = archival.archive_dir / "preserved.json.gz"
    assert archive.exists()
    with gzip.open(archive, "rb") as f:
        decoded = json.loads(f.read().decode("utf-8"))
    assert decoded == payload


@pytest.mark.asyncio
async def test_no_sessions_dir_handled(archival, tmp_path):
    archival.sessions_dir = tmp_path / "nonexistent"
    result = await archival.move_cold()
    assert result.get("moved") == 0
    assert "no sessions" in result.get("reason", "").lower()


@pytest.mark.asyncio
async def test_restore_round_trip(archival):
    payload = {"k": "v"}
    _write_session(archival, "restorable", age_days=120, content=payload)
    await archival.move_cold()
    assert not (archival.sessions_dir / "restorable.json").exists()

    target = archival.restore("restorable")
    assert target is not None
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    # Y el archivo en archive ya no existe
    assert not (archival.archive_dir / "restorable.json.gz").exists()


def test_list_archived(archival):
    archival.archive_dir.mkdir(parents=True, exist_ok=True)
    (archival.archive_dir / "a.json.gz").write_bytes(b"x")
    (archival.archive_dir / "b.json.gz").write_bytes(b"yyy")
    archived = archival.list_archived()
    assert len(archived) == 2
    names = {a["name"] for a in archived}
    assert names == {"a", "b"}
