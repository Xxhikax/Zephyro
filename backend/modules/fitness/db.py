"""Schema Postgres para el módulo de fitness.

Tablas:
  - workout_plans     : planes generados por LLM (home/gym/muay_thai/swimming)
  - workout_sessions  : instancias concretas de sesiones en la agenda
  - fitness_profile   : preferencias del usuario (días, horario, protecciones)
  - streaks           : racha actual y máxima
"""
from __future__ import annotations

import logging

log = logging.getLogger("zephyro.fitness.db")

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS workout_plans (
  id SERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  context TEXT NOT NULL DEFAULT 'home',
  status TEXT NOT NULL DEFAULT 'draft',
  goal TEXT NOT NULL DEFAULT 'body_recomp',
  weekly_frequency INT NOT NULL,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  generated_by TEXT,
  rationale TEXT,
  raw JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS workout_plans_user_status_idx
  ON workout_plans(user_id, status);

CREATE TABLE IF NOT EXISTS workout_sessions (
  id SERIAL PRIMARY KEY,
  plan_id INT REFERENCES workout_plans(id) ON DELETE SET NULL,
  schedule_event_id INT,
  user_id TEXT NOT NULL,
  day_of_week INT,
  session_type TEXT NOT NULL,
  planned_at TIMESTAMPTZ NOT NULL,
  duration_min INT NOT NULL,
  equipment TEXT[] NOT NULL,
  blocks JSONB NOT NULL,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'planned',
  verification_method TEXT,
  verification_score REAL,
  notes TEXT,
  points INT NOT NULL DEFAULT 0,
  streak_day INT
);
CREATE INDEX IF NOT EXISTS workout_sessions_user_planned_idx
  ON workout_sessions(user_id, planned_at);
CREATE INDEX IF NOT EXISTS workout_sessions_status_idx
  ON workout_sessions(status);

CREATE TABLE IF NOT EXISTS fitness_profile (
  user_id TEXT PRIMARY KEY,
  goal TEXT NOT NULL DEFAULT 'body_recomp',
  context TEXT NOT NULL DEFAULT 'home',
  available_equipment TEXT[] NOT NULL DEFAULT ARRAY['jump_rope','heavy_bag','space'],
  fitness_level TEXT NOT NULL DEFAULT 'intermediate',
  weekly_target INT NOT NULL DEFAULT 4,
  preferred_days INT[],
  preferred_time_window TEXT DEFAULT 'evening',
  preferred_start_hour INT DEFAULT 18,
  min_rest_hours_between INT NOT NULL DEFAULT 24,
  protected_categories TEXT[] NOT NULL
    DEFAULT ARRAY['university','uni_exam','uni_assignment','course_study','personal_project','client_project'],
  notes TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS streaks (
  user_id TEXT PRIMARY KEY,
  current INT NOT NULL DEFAULT 0,
  longest INT NOT NULL DEFAULT 0,
  last_completed_date DATE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_SEED_PROFILE = """
INSERT INTO fitness_profile (user_id)
VALUES ('admin')
ON CONFLICT (user_id) DO NOTHING;
"""

_SEED_STREAK = """
INSERT INTO streaks (user_id)
VALUES ('admin')
ON CONFLICT (user_id) DO NOTHING;
"""


def ensure_schema() -> None:
    """Crea las tablas si no existen y seed del usuario admin.
    Envuelto en try/except — si Postgres no está corriendo, el módulo igual importa.
    """
    try:
        from core.postgres_pool import get_cursor
        with get_cursor(autocommit=True) as cur:
            cur.execute(_CREATE_TABLES)
            cur.execute(_SEED_PROFILE)
            cur.execute(_SEED_STREAK)
        log.info("[fitness.db] schema OK")
    except Exception as exc:
        log.warning("[fitness.db] No se pudo inicializar schema: %s", exc)
