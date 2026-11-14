"""NutritionSkill — skill de tracking nutricional con foco IR.

Sigue el patrón exacto de backend/skills/builtin/schedule/skill.py.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill

log = logging.getLogger("zephyro.skill.nutrition")

# ── Regex de matching ────────────────────────────────────────────────────────

_FOOD_LOG_RE = re.compile(
    r"\b(?:hice|comí|comi|acabo\s+de\s+comer|desayuné|desayune|almorcé|almorzé|almorcé|cené|cene|"
    r"tomé|tome|me\s+comí|me\s+comi|acabo\s+de\s+desayunar|acabo\s+de\s+almorzar|acabo\s+de\s+cenar)\b",
    re.IGNORECASE,
)

_MACROS_RE = re.compile(
    r"^/macros?\b|mis\s+macros?|resumen\s+nutri|estado\s+nutri|cuánto\s+(?:llevo|comí)\s+hoy",
    re.IGNORECASE,
)

_SUGGEST_RE = re.compile(
    r"^/q[ue][eé]\s+como\b|qué\s+(?:cocino|como|preparo|como\s+ahora)|"
    r"qu[eé]\s+como\b|suger[ií]\s+comida|suger[ií]me|qu[eé]\s+comer",
    re.IGNORECASE,
)

_WATER_RE = re.compile(
    r"^/agua(?:\s+(\d+))?\s*$|tomé\s+(?:un\s+)?vaso|beb[ií]\s+agua|"
    r"registr[aá]\s+agua\s+(\d+)|tomar\s+agua",
    re.IGNORECASE,
)

_STEPS_RE = re.compile(
    r"^/pasos?\s+(\d+)|caminé\s+(\d+)\s*pasos?|llevo\s+(\d+)\s*pasos?|"
    r"registr[aá]\s+pasos?\s+(\d+)",
    re.IGNORECASE,
)

_WEIGHT_RE = re.compile(
    r"^/peso\s+([\d.]+)|me\s+pes[eé]\s+([\d.]+)|peso\s+([\d.]+)\s*kg|"
    r"registr[aá]\s+mi\s+peso\s+([\d.]+)",
    re.IGNORECASE,
)

_WEEKLY_RE = re.compile(
    r"resumen\s+(?:nutri(?:ci[oó]n)?|semanal)|reporte\s+nutri|mi\s+semana\s+nutri|"
    r"nutrici[oó]n\s+semanal",
    re.IGNORECASE,
)

_MEAL_TYPES = {
    "desayun": "breakfast",
    "almuerz": "lunch",
    "cen": "dinner",
    "merienda": "other",
    "snack": "other",
}


def _infer_meal_type(text: str) -> str:
    lower = text.lower()
    for kw, mt in _MEAL_TYPES.items():
        if kw in lower:
            return mt
    # Por hora del día
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        h = datetime.now(ZoneInfo("America/Lima")).hour
        if 5 <= h < 11:
            return "breakfast"
        elif 11 <= h < 16:
            return "lunch"
        elif 16 <= h < 22:
            return "dinner"
    except Exception:
        pass
    return "other"


def _extract_water_ml(text: str) -> int:
    """Extrae ml de agua del input. 'un vaso' = 250ml, 'un litro' = 1000ml."""
    lower = text.lower()
    # Número explícito
    m = re.search(r"(\d+)\s*(?:ml|mili(?:litros?)?)?", lower)
    if m:
        val = int(m.group(1))
        if val > 50:  # Evitar confundir pasos u otros números
            return val
    if "litro" in lower:
        m2 = re.search(r"([\d.]+)\s*litros?", lower)
        if m2:
            return int(float(m2.group(1)) * 1000)
    if "vaso" in lower:
        return 250
    if "botella" in lower:
        return 500
    return 250  # Default: 1 vaso


def _extract_number(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def _progress_bar(current: float, target: float, width: int = 10) -> str:
    """Genera barra de progreso ASCII."""
    if target <= 0:
        return "░" * width
    pct = min(1.0, current / target)
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled)


class NutritionSkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._service = None

    def _get_service(self):
        if self._service is None:
            from modules.nutrition.service import get_nutrition_service
            self._service = get_nutrition_service()
        return self._service

    # ── Matching ─────────────────────────────────────────────────────────

    def matches_command(self, user_input: str, ctx=None) -> bool:
        lower = user_input.lower().strip()

        # Triggers exactos del SKILL.md
        for trig in self.metadata.triggers:
            t = trig.lower()
            if lower == t or lower.startswith(t + " ") or lower.startswith(t + "\t"):
                return True

        # Regex patterns
        if _FOOD_LOG_RE.search(lower):
            return True
        if _MACROS_RE.search(lower):
            return True
        if _SUGGEST_RE.search(lower):
            return True
        if _WATER_RE.search(lower):
            return True
        if _STEPS_RE.search(lower):
            return True
        if _WEIGHT_RE.search(lower):
            return True
        if _WEEKLY_RE.search(lower):
            return True

        return False

    # ── Handle ───────────────────────────────────────────────────────────

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        lower = user_input.lower().strip()
        user_id = getattr(ctx, "user_role", None) or "admin"
        svc = self._get_service()

        # /macros → resumen del día
        if _MACROS_RE.search(lower):
            return await self._handle_macros(user_id, svc)

        # /agua → log water
        if _WATER_RE.search(lower):
            return await self._handle_water(user_input, user_id, svc)

        # /pasos → log steps
        if _STEPS_RE.search(lower):
            return await self._handle_steps(user_input, user_id, svc)

        # /peso → log weight
        if _WEIGHT_RE.search(lower):
            return await self._handle_weight(user_input, user_id, svc)

        # reporte semanal
        if _WEEKLY_RE.search(lower):
            return await self._handle_weekly(user_id, svc)

        # sugerencia de comida
        if _SUGGEST_RE.search(lower):
            return await self._handle_suggest(user_id, svc)

        # log de comida (texto)
        if _FOOD_LOG_RE.search(lower):
            return await self._handle_food_text(user_input, user_id, svc)

        return None

    # ── Handlers internos ─────────────────────────────────────────────────

    async def _handle_macros(self, user_id: str, svc) -> str:
        try:
            summary = svc.get_daily_summary(user_id)
            t = summary.targets

            def fmt(cur, tgt, unit="g"):
                bar = _progress_bar(cur, tgt)
                pct = int(cur / tgt * 100) if tgt > 0 else 0
                return f"{bar} {cur:.0f}/{tgt:.0f}{unit} ({pct}%)"

            flag_line = ""
            if summary.insulin_flags_count > 0:
                flag_line = f"\n⚠️ Flags insulina: {summary.insulin_flags_count}"

            meals_line = ""
            if summary.meals:
                meal_names = [m.get("description", "")[:30] for m in summary.meals[-3:]]
                meals_line = f"\nÚltimas comidas: {' | '.join(meal_names)}"

            return (
                f"Nutrición de hoy {summary.status_indicator}\n"
                f"Calorías: {fmt(summary.calories, t.get('calories', 2000), 'kcal')}\n"
                f"Proteína: {fmt(summary.protein_g, t.get('protein_g', 175))}\n"
                f"Carbos:   {fmt(summary.carbs_g, t.get('carbs_g', 150))}\n"
                f"Grasas:   {fmt(summary.fats_g, t.get('fats_g', 65))}\n"
                f"Fibra:    {fmt(summary.fiber_g, t.get('fiber_g', 35))}\n"
                f"Agua:     {fmt(summary.water_ml, t.get('water_ml', 3000), 'ml')}"
                f"{flag_line}{meals_line}\n"
                f"Adherencia: {summary.adherence_score:.0%}"
            )
        except Exception as exc:
            log.warning("[nutrition.skill] _handle_macros error: %s", exc)
            return "No pude obtener el resumen. Asegurate de que Postgres esté disponible."

    async def _handle_water(self, user_input: str, user_id: str, svc) -> str:
        try:
            ml = _extract_water_ml(user_input)
            status = svc.log_water(user_id=user_id, amount_ml=ml)
            pct = int(status.pct * 100)
            emoji = "💧" if status.pct < 0.5 else "🚰" if status.pct < 0.9 else "✅"
            return (
                f"{emoji} +{ml}ml de agua registrado.\n"
                f"Total hoy: {status.cumulative_day_ml}ml / {status.target_ml}ml ({pct}%)\n"
                f"{_progress_bar(status.cumulative_day_ml, status.target_ml, 12)}"
            )
        except Exception as exc:
            log.warning("[nutrition.skill] _handle_water error: %s", exc)
            return "No pude registrar el agua. Intentalo de nuevo."

    async def _handle_steps(self, user_input: str, user_id: str, svc) -> str:
        try:
            n = _extract_number(user_input)
            if n is None:
                return "Decime cuántos pasos caminaste. Ej: /pasos 9500"
            steps = int(n)
            status = svc.log_steps(user_id=user_id, steps=steps)
            bonus_msg = f" (+{status.bonus_kcal} kcal bonus!)" if status.bonus_kcal > 0 else ""
            goal_pct = int(steps / status.target * 100) if status.target > 0 else 0
            emoji = "🏆" if steps >= status.target else "👟"
            return (
                f"{emoji} {steps:,} pasos registrados{bonus_msg}\n"
                f"Meta: {status.target:,} pasos ({goal_pct}%)\n"
                f"{_progress_bar(steps, status.target, 12)}"
            )
        except Exception as exc:
            log.warning("[nutrition.skill] _handle_steps error: %s", exc)
            return "No pude registrar los pasos. Ej: /pasos 9500"

    async def _handle_weight(self, user_input: str, user_id: str, svc) -> str:
        try:
            n = _extract_number(user_input)
            if n is None:
                return "Decime tu peso en kg. Ej: /peso 96.5"
            status = svc.log_weight(user_id=user_id, weight_kg=n)
            return f"⚖️ Peso registrado: {status.weight_kg} kg ({status.log_date.strftime('%d/%m/%Y')})"
        except Exception as exc:
            log.warning("[nutrition.skill] _handle_weight error: %s", exc)
            return "No pude registrar el peso. Ej: /peso 96.5"

    async def _handle_weekly(self, user_id: str, svc) -> str:
        try:
            report = await svc.get_weekly_nutrition_report(user_id)
            summary = report.get("summary", "Sin datos")
            strengths = report.get("strengths") or []
            improvements = report.get("improvements") or []
            rec = report.get("recommendation", "")
            prot_trend = report.get("protein_trend", "stable")
            ins_trend = report.get("insulin_load_trend", "stable")

            trend_icons = {"improving": "📈", "stable": "➡️", "declining": "📉", "worsening": "📉"}

            lines = [f"Reporte nutricional semanal\n{summary}"]
            if strengths:
                lines.append("Puntos positivos:\n" + "\n".join(f"• {s}" for s in strengths))
            if improvements:
                lines.append("Para mejorar:\n" + "\n".join(f"• {i}" for i in improvements))
            lines.append(
                f"Proteína: {trend_icons.get(prot_trend, '➡️')} {prot_trend} | "
                f"Insulina: {trend_icons.get(ins_trend, '➡️')} {ins_trend}"
            )
            if rec:
                lines.append(f"Recomendación: {rec}")

            return "\n\n".join(lines)
        except Exception as exc:
            log.warning("[nutrition.skill] _handle_weekly error: %s", exc)
            return "No pude generar el reporte semanal. Asegurate de tener datos registrados."

    async def _handle_suggest(self, user_id: str, svc) -> str:
        try:
            suggestion = await svc.suggest_next_meal(user_id)
            if suggestion.wait_message:
                return suggestion.wait_message
            if not suggestion.suggestions:
                return "No tengo sugerencias por ahora. Asegurate de tener tu perfil configurado."

            lines = ["Sugerencias para tu próxima comida (foco IR):"]
            for i, s in enumerate(suggestion.suggestions[:2], 1):
                name = s.get("name", "")
                desc = s.get("description", "")
                why = s.get("why", "")
                macros = s.get("estimated_macros", {})
                safe = "✅" if s.get("insulin_safe") else "⚠️"
                lines.append(
                    f"{i}. {safe} {name}\n"
                    f"   {desc}\n"
                    f"   ~{macros.get('calories', 0):.0f}kcal | {macros.get('protein_g', 0):.0f}g prot\n"
                    f"   {why}"
                )

            if suggestion.timing_note:
                lines.append(f"Timing: {suggestion.timing_note}")

            return "\n\n".join(lines)
        except Exception as exc:
            log.warning("[nutrition.skill] _handle_suggest error: %s", exc)
            return "No pude generar una sugerencia ahora. Intentalo de nuevo."

    async def _handle_food_text(self, user_input: str, user_id: str, svc) -> str:
        try:
            meal_type = _infer_meal_type(user_input)
            # Limpiar el trigger del texto
            description = re.sub(
                r"^(?:hice|comí|comi|acabo\s+de\s+comer|desayuné|desayune|almorcé|"
                r"almorzé|cené|cene|tomé|tome|me\s+comí|me\s+comi|"
                r"acabo\s+de\s+(?:desayunar|almorzar|cenar))\s+",
                "", user_input, flags=re.IGNORECASE,
            ).strip()

            if not description:
                return "No entendí qué comiste. Contame más. Ej: 'hice ceviche con quinua'"

            analysis, food_log = await svc.log_food_from_text(
                user_id=user_id, description=description, meal_type=meal_type
            )

            # Construir respuesta
            totals = analysis.totals
            lines = [
                f"Registré: {description}",
                f"~{totals.get('calories', 0):.0f} kcal | "
                f"{totals.get('protein_g', 0):.0f}g prot | "
                f"{totals.get('carbs_g', 0):.0f}g carbos | "
                f"{totals.get('fiber_g', 0):.0f}g fibra",
                f"Confianza: {analysis.confidence}",
            ]

            if analysis.insulin_flag and analysis.flagged_items:
                lines.append(f"⚠️ Flag IR: {', '.join(analysis.flagged_items)}")
                if analysis.nudges:
                    lines.append(f"💡 {analysis.nudges[0]}")

            return "\n".join(lines)

        except Exception as exc:
            log.warning("[nutrition.skill] _handle_food_text error: %s", exc)
            return f"No pude analizar esa comida: {exc}. Intentalo con más detalle."
