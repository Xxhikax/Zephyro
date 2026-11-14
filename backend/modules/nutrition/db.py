"""Schema Postgres y seed de datos para el módulo de nutrición.

Tablas: nutrition_profile, food_logs, water_logs, step_logs, weight_logs, nutrition_days.
Seed: user_id='admin' con defaults de Brax (perfil IR).
"""
from __future__ import annotations

import logging

log = logging.getLogger("zephyro.nutrition.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nutrition_profile (
  user_id TEXT PRIMARY KEY,
  daily_calories INT NOT NULL DEFAULT 2000,
  daily_protein_g INT NOT NULL DEFAULT 175,
  daily_carbs_g INT NOT NULL DEFAULT 150,
  daily_fats_g INT NOT NULL DEFAULT 65,
  daily_water_ml INT NOT NULL DEFAULT 3000,
  daily_fiber_g INT NOT NULL DEFAULT 35,
  daily_steps INT NOT NULL DEFAULT 8000,
  insulin_resistance_mode BOOLEAN NOT NULL DEFAULT TRUE,
  meal_min_gap_hours INT NOT NULL DEFAULT 4,
  health_source TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS food_logs (
  id SERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  meal_type TEXT NOT NULL,
  description TEXT NOT NULL,
  photo_path TEXT,
  items JSONB NOT NULL,
  calories REAL NOT NULL,
  protein_g REAL NOT NULL,
  carbs_g REAL NOT NULL,
  fats_g REAL NOT NULL,
  fiber_g REAL NOT NULL,
  glycemic_load_estimate REAL,
  insulin_flag BOOLEAN NOT NULL DEFAULT FALSE,
  flagged_items TEXT[],
  source TEXT NOT NULL,
  confidence TEXT,
  notes TEXT
);
CREATE INDEX IF NOT EXISTS food_logs_user_date_idx ON food_logs(user_id, logged_at);

CREATE TABLE IF NOT EXISTS water_logs (
  id SERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  amount_ml INT NOT NULL,
  cumulative_day_ml INT NOT NULL
);
CREATE INDEX IF NOT EXISTS water_logs_user_date_idx ON water_logs(user_id, logged_at);

CREATE TABLE IF NOT EXISTS step_logs (
  id SERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  log_date DATE NOT NULL,
  steps INT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  bonus_kcal INT NOT NULL DEFAULT 0,
  logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(user_id, log_date)
);

CREATE TABLE IF NOT EXISTS weight_logs (
  id SERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  log_date DATE NOT NULL,
  weight_kg REAL NOT NULL,
  body_fat_pct REAL,
  source TEXT NOT NULL DEFAULT 'manual',
  notes TEXT,
  logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS weight_logs_user_date_idx ON weight_logs(user_id, log_date);

CREATE TABLE IF NOT EXISTS nutrition_days (
  user_id TEXT NOT NULL,
  day DATE NOT NULL,
  total_calories REAL NOT NULL DEFAULT 0,
  total_protein REAL NOT NULL DEFAULT 0,
  total_carbs REAL NOT NULL DEFAULT 0,
  total_fats REAL NOT NULL DEFAULT 0,
  total_fiber REAL NOT NULL DEFAULT 0,
  total_water_ml INT NOT NULL DEFAULT 0,
  insulin_flags_count INT NOT NULL DEFAULT 0,
  adherence_score REAL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, day)
);
"""

_SEED_SQL = """
INSERT INTO nutrition_profile (
  user_id, daily_calories, daily_protein_g, daily_carbs_g, daily_fats_g,
  daily_water_ml, daily_fiber_g, daily_steps, insulin_resistance_mode, meal_min_gap_hours
) VALUES (
  'admin', 2000, 175, 150, 65,
  3000, 35, 8000, TRUE, 4
) ON CONFLICT (user_id) DO NOTHING;
"""


def _ensure_schema() -> None:
    """Crea tablas y seed. Silencia errores si Postgres no está disponible."""
    try:
        from core.postgres_pool import get_cursor
        with get_cursor(autocommit=True) as cur:
            cur.execute(_SCHEMA_SQL)
            cur.execute(_SEED_SQL)
        log.info("[nutrition.db] Schema y seed aplicados correctamente")
    except ImportError:
        log.warning("[nutrition.db] postgres_pool no disponible — skip schema")
    except Exception as exc:
        log.warning("[nutrition.db] Error al aplicar schema: %s", exc)
