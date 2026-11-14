"""Validación del JSON generado por el LLM para planes de fitness.

Schema mínimo esperado:
{
  "weekly_frequency": int,
  "rationale": str,
  "days": [
    {
      "day_of_week": int (0=lun .. 6=dom),
      "session_type": str,
      "duration_min": int,
      "equipment": list[str],
      "blocks": list
    }
  ]
}
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("zephyro.fitness.schema")


@dataclass
class SessionDay:
    day_of_week: int
    session_type: str
    duration_min: int
    equipment: list[str]
    blocks: list[Any]

    @classmethod
    def from_dict(cls, d: dict) -> "SessionDay":
        return cls(
            day_of_week=int(d.get("day_of_week", 0)),
            session_type=str(d.get("session_type", "general")),
            duration_min=max(10, int(d.get("duration_min", 45))),
            equipment=list(d.get("equipment") or []),
            blocks=list(d.get("blocks") or []),
        )


@dataclass
class WorkoutPlan:
    weekly_frequency: int
    rationale: str
    days: list[SessionDay] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "WorkoutPlan":
        days_raw = d.get("days") or []
        return cls(
            weekly_frequency=max(1, int(d.get("weekly_frequency", len(days_raw)))),
            rationale=str(d.get("rationale", "")),
            days=[SessionDay.from_dict(day) for day in days_raw],
        )


def validate_plan_json(raw: str) -> WorkoutPlan:
    """Parsea y valida el JSON del plan.
    Lanza ValueError si el JSON es inválido o le falta campos críticos.
    """
    try:
        # Strip markdown code fences if LLM returned them
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido del LLM: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("El plan debe ser un objeto JSON, no una lista u otro tipo")

    if "days" not in data or not isinstance(data["days"], list) or not data["days"]:
        raise ValueError("El plan debe tener al menos un día en 'days'")

    plan = WorkoutPlan.from_dict(data)
    if plan.weekly_frequency < 1:
        raise ValueError("weekly_frequency debe ser >= 1")
    return plan
