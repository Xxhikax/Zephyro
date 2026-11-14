"""NutritionService — tracking de comidas, agua, pasos y peso con foco IR.

Singleton accesible via get_nutrition_service().
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

log = logging.getLogger("zephyro.nutrition.service")

# ── Keywords que disparan la bandera IR ──────────────────────────────────────
_HIGH_GI_KEYWORDS: set[str] = {
    "arroz blanco", "pan blanco", "fideos blancos", "papa", "azúcar",
    "miel", "jugo", "gaseosa", "mermelada", "chancaca", "galleta",
    "pastel", "ketchup", "mazamorra", "chicha morada", "embutido",
}

# ── Nudges educativos por keyword ────────────────────────────────────────────
_NUDGE_TEMPLATES: dict[str, str] = {
    "arroz blanco": "El arroz blanco eleva la insulina rápidamente porque tiene GI alto — reemplazalo por quinua o arroz integral en porción controlada.",
    "pan blanco": "El pan blanco es harina refinada sin fibra — el pico glucémico llega rápido. Probá pan integral de centeno en porciones pequeñas.",
    "fideos blancos": "Los fideos blancos tienen GI alto. Optá por fideos de legumbre o reducí la porción y combiná con mucha proteína.",
    "papa": "La papa tiene GI entre medio y alto según el tipo de cocción. Preferí camote (GI más bajo) o reducí a porción chica con proteína.",
    "azúcar": "El azúcar añadida provoca picos de insulina directos. Reemplazala por stevia o evitala por completo en tu plan IR.",
    "miel": "La miel tiene GI alto a pesar de ser 'natural' — es azúcar concentrada para tu páncreas. Evitala en IR.",
    "jugo": "Aunque sea natural, el jugo pierde la fibra de la fruta y entrega azúcar directa al torrente sanguíneo. Comé la fruta entera o tomá agua.",
    "gaseosa": "Las gaseosas son azúcar pura en líquido — el peor formato para IR. Cambiala por agua con limón o agua mineral.",
    "mermelada": "La mermelada es casi puro azúcar con frutas. Reemplazala por palta o queso en el desayuno.",
    "chancaca": "La chancaca (azúcar morena de caña) tiene el mismo impacto que el azúcar refinada en insulina. Evitala.",
    "galleta": "Las galletas mezclan harina refinada y azúcar — pico glucémico doble. Preferí almendras o nueces como snack.",
    "pastel": "El pastel combina harina, azúcar y grasa saturada — impacto IR alto. Reservalo para ocasiones muy especiales.",
    "ketchup": "El ketchup comercial tiene azúcar oculta alta. Usá mostaza natural o limón como condimento.",
    "mazamorra": "La mazamorra morada lleva azúcar y almidón de chuño — carga glucémica alta. Evitala en tu plan IR.",
    "chicha morada": "La chicha morada con azúcar tiene GI alto. Prepará una versión sin azúcar (con stevia) o tomá agua.",
    "embutido": "Los embutidos suelen llevar azúcares ocultos y nitratos. Preferí proteína fresca (pollo, pescado, huevo).",
}


# ── Dataclasses de resultado ─────────────────────────────────────────────────

@dataclass
class FoodItem:
    name: str
    quantity_g: float = 100.0
    calories: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fats_g: float = 0.0
    fiber_g: float = 0.0
    glycemic_index: str = "medium"
    is_problematic: bool = False


@dataclass
class FoodAnalysis:
    items: list[FoodItem]
    totals: dict[str, float]
    glycemic_load_estimate: str
    insulin_flag: bool
    flagged_items: list[str]
    confidence: str
    notes: str
    nudges: list[str] = field(default_factory=list)
    source: str = "photo"
    meal_type: str = "other"


@dataclass
class FoodLog:
    id: int
    user_id: str
    logged_at: datetime
    meal_type: str
    description: str
    analysis: FoodAnalysis


@dataclass
class DailySummary:
    date: date
    user_id: str
    calories: float
    protein_g: float
    carbs_g: float
    fats_g: float
    fiber_g: float
    water_ml: int
    insulin_flags_count: int
    targets: dict[str, float]
    adherence_score: float
    status_indicator: str   # "🟢" | "🟡" | "🔴"
    meals: list[dict]


@dataclass
class WaterStatus:
    user_id: str
    amount_ml: int
    cumulative_day_ml: int
    target_ml: int
    pct: float


@dataclass
class StepStatus:
    user_id: str
    steps: int
    target: int
    bonus_kcal: int
    log_date: date


@dataclass
class WeightStatus:
    user_id: str
    weight_kg: float
    log_date: date
    source: str


@dataclass
class MealSuggestion:
    suggestions: list[dict]
    timing_note: Optional[str]
    wait_message: Optional[str] = None


# ── Helpers internos ─────────────────────────────────────────────────────────

def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _parse_food_json(raw: str) -> Optional[dict]:
    """Extrae dict JSON de la respuesta del LLM. Tolerante a markdown."""
    raw = raw.strip()
    # Quitar bloques de código markdown
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _apply_insulin_rules(analysis_dict: dict) -> tuple[bool, list[str], list[str]]:
    """Aplica reglas IR en código (no en LLM).

    Retorna (insulin_flag, flagged_items, nudges).
    """
    flagged: list[str] = list(analysis_dict.get("flagged_items") or [])
    nudges: list[str] = []

    # Revisar items individuales
    items = analysis_dict.get("items") or []
    description_text = " ".join(
        (item.get("name") or "").lower() for item in items
    )

    for keyword in _HIGH_GI_KEYWORDS:
        if keyword in description_text and keyword not in flagged:
            flagged.append(keyword)

    # Generar nudges educativos
    for kw in flagged:
        nudge = _NUDGE_TEMPLATES.get(kw)
        if nudge and nudge not in nudges:
            nudges.append(nudge)

    insulin_flag = len(flagged) > 0 or bool(analysis_dict.get("insulin_flag"))

    # Checar también contra catálogo de platos peruanos
    from modules.nutrition.peru_food import lookup_dish
    for item in items:
        dish_info = lookup_dish(item.get("name") or "")
        if dish_info and dish_info.get("flag"):
            for fi in (dish_info.get("flagged_items") or []):
                if fi not in flagged:
                    flagged.append(fi)
            note = dish_info.get("note")
            if note and note not in nudges:
                nudges.append(note)
            insulin_flag = True

    return insulin_flag, flagged, nudges


def _dict_to_analysis(data: dict, source: str, meal_type: str) -> FoodAnalysis:
    """Convierte dict JSON del LLM a FoodAnalysis, aplicando reglas IR."""
    items_raw = data.get("items") or []
    items = [
        FoodItem(
            name=i.get("name", ""),
            quantity_g=float(i.get("quantity_g") or 100),
            calories=float(i.get("calories") or 0),
            protein_g=float(i.get("protein_g") or 0),
            carbs_g=float(i.get("carbs_g") or 0),
            fats_g=float(i.get("fats_g") or 0),
            fiber_g=float(i.get("fiber_g") or 0),
            glycemic_index=i.get("glycemic_index") or "medium",
            is_problematic=bool(i.get("is_problematic")),
        )
        for i in items_raw
    ]

    totals_raw = data.get("totals") or {}
    totals = {
        "calories": float(totals_raw.get("calories") or sum(i.calories for i in items)),
        "protein_g": float(totals_raw.get("protein_g") or sum(i.protein_g for i in items)),
        "carbs_g": float(totals_raw.get("carbs_g") or sum(i.carbs_g for i in items)),
        "fats_g": float(totals_raw.get("fats_g") or sum(i.fats_g for i in items)),
        "fiber_g": float(totals_raw.get("fiber_g") or sum(i.fiber_g for i in items)),
    }

    insulin_flag, flagged, nudges = _apply_insulin_rules(data)

    return FoodAnalysis(
        items=items,
        totals=totals,
        glycemic_load_estimate=data.get("glycemic_load_estimate") or "medium",
        insulin_flag=insulin_flag,
        flagged_items=flagged,
        confidence=data.get("confidence") or "medium",
        notes=data.get("notes") or "",
        nudges=nudges,
        source=source,
        meal_type=meal_type,
    )


def _compute_adherence(day_data: dict, profile: dict) -> float:
    """Calcula adherencia 0-1 combinando cobertura de macros, agua y fibra.

    Fórmula ponderada:
      - Cobertura de calorías (0.9-1.15 ideal) → 25%
      - Cobertura de proteína (>=0.9 ideal) → 35%
      - Cobertura de agua (>=0.8 ideal) → 15%
      - Cobertura de fibra (>=0.8 ideal) → 15%
      - Penalización por insulin_flags_count → hasta -10%
    """
    def coverage(val: float, target: float) -> float:
        if target <= 0:
            return 1.0
        ratio = val / target
        if ratio > 1.15:
            return max(0.0, 1.0 - (ratio - 1.15) * 2)
        return min(1.0, ratio / 0.9)

    cal_score = coverage(day_data.get("total_calories", 0), profile.get("daily_calories", 2000))
    prot_score = coverage(day_data.get("total_protein", 0), profile.get("daily_protein_g", 175))
    water_score = coverage(day_data.get("total_water_ml", 0), profile.get("daily_water_ml", 3000))
    fiber_score = coverage(day_data.get("total_fiber", 0), profile.get("daily_fiber_g", 35))

    base = cal_score * 0.25 + prot_score * 0.35 + water_score * 0.15 + fiber_score * 0.15

    flags = day_data.get("insulin_flags_count", 0)
    penalty = min(0.20, flags * 0.05)

    return max(0.0, min(1.0, base - penalty))


# ── NutritionService ─────────────────────────────────────────────────────────

class NutritionService:
    """Servicio central de nutrición con tracking de comidas, agua, pasos y peso."""

    def __init__(self) -> None:
        from modules.nutrition.db import _ensure_schema
        _ensure_schema()
        self._llm: Optional[Any] = None

    def _get_llm(self) -> Optional[Any]:
        if self._llm is None:
            try:
                import web_app
                self._llm = web_app.app.state.orchestrator.llm
            except Exception:
                pass
        return self._llm

    def _get_vision(self) -> Optional[Any]:
        try:
            import web_app
            return web_app.app.state.orchestrator.vision_analyze
        except Exception:
            return None

    # ── Pool ────────────────────────────────────────────────────────────

    def _cursor(self, autocommit: bool = True):
        from core.postgres_pool import get_cursor
        return get_cursor(autocommit=autocommit)

    # ── Profile ─────────────────────────────────────────────────────────

    def get_profile(self, user_id: str = "admin") -> dict:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT user_id, daily_calories, daily_protein_g, daily_carbs_g, "
                    "daily_fats_g, daily_water_ml, daily_fiber_g, daily_steps, "
                    "insulin_resistance_mode, meal_min_gap_hours, health_source, updated_at "
                    "FROM nutrition_profile WHERE user_id=%s",
                    (user_id,),
                )
                row = cur.fetchone()
                if row:
                    cols = ["user_id", "daily_calories", "daily_protein_g", "daily_carbs_g",
                            "daily_fats_g", "daily_water_ml", "daily_fiber_g", "daily_steps",
                            "insulin_resistance_mode", "meal_min_gap_hours", "health_source", "updated_at"]
                    return dict(zip(cols, row))
        except Exception as exc:
            log.warning("[nutrition] get_profile error: %s", exc)
        # Defaults fallback
        return {
            "user_id": user_id,
            "daily_calories": 2000, "daily_protein_g": 175, "daily_carbs_g": 150,
            "daily_fats_g": 65, "daily_water_ml": 3000, "daily_fiber_g": 35,
            "daily_steps": 8000, "insulin_resistance_mode": True, "meal_min_gap_hours": 4,
        }

    def update_profile(self, user_id: str, **kwargs) -> dict:
        allowed = {
            "daily_calories", "daily_protein_g", "daily_carbs_g", "daily_fats_g",
            "daily_water_ml", "daily_fiber_g", "daily_steps", "insulin_resistance_mode",
            "meal_min_gap_hours", "health_source",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_profile(user_id)
        try:
            with self._cursor() as cur:
                sets = ", ".join(f"{k}=%s" for k in updates)
                vals = list(updates.values()) + [user_id]
                cur.execute(
                    f"INSERT INTO nutrition_profile (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id,),
                )
                cur.execute(
                    f"UPDATE nutrition_profile SET {sets}, updated_at=NOW() WHERE user_id=%s",
                    vals,
                )
        except Exception as exc:
            log.warning("[nutrition] update_profile error: %s", exc)
        return self.get_profile(user_id)

    # ── Food Analysis ───────────────────────────────────────────────────

    async def analyze_food_photo(self, image_path: str, meal_type: str = "other") -> FoodAnalysis:
        """Analiza foto de comida con Haiku Vision. No persiste."""
        from modules.nutrition.prompts import NUTRITION_VISION_PROMPT
        vision = self._get_vision()
        if vision is None:
            raise RuntimeError("Vision no disponible")

        raw = vision.analyze(image_path, NUTRITION_VISION_PROMPT, use_cloud=True, provider="anthropic")
        data = _parse_food_json(raw)
        if data is None:
            # Retry con prompt simplificado
            raw2 = vision.analyze(image_path, NUTRITION_VISION_PROMPT, use_cloud=True)
            data = _parse_food_json(raw2)
            if data is None:
                raise ValueError(f"No pude parsear la respuesta de visión: {raw[:200]}")

        return _dict_to_analysis(data, source="photo", meal_type=meal_type)

    async def analyze_food_text(self, description: str, meal_type: str = "other") -> FoodAnalysis:
        """Analiza descripción textual de comida con LLM. No persiste."""
        from modules.nutrition.prompts import NUTRITION_TEXT_PROMPT
        llm = self._get_llm()
        if llm is None:
            raise RuntimeError("LLM no disponible")

        prompt = f"{NUTRITION_TEXT_PROMPT}\n\nDescripción del usuario: {description}"
        raw = await llm.generate(prompt, force_cloud=True)
        data = _parse_food_json(str(raw))
        if data is None:
            # Retry
            raw2 = await llm.generate(prompt, force_cloud=True)
            data = _parse_food_json(str(raw2))
            if data is None:
                raise ValueError(f"No pude parsear la respuesta del LLM: {str(raw)[:200]}")

        return _dict_to_analysis(data, source="text", meal_type=meal_type)

    # ── Logging ─────────────────────────────────────────────────────────

    def log_food(self, user_id: str, analysis: FoodAnalysis, source: str = "manual") -> Optional[FoodLog]:
        """Persiste un FoodAnalysis en food_logs y actualiza nutrition_days."""
        today = _today_utc()
        items_json = json.dumps([
            {
                "name": i.name, "quantity_g": i.quantity_g,
                "calories": i.calories, "protein_g": i.protein_g,
                "carbs_g": i.carbs_g, "fats_g": i.fats_g, "fiber_g": i.fiber_g,
            }
            for i in analysis.items
        ])
        description = analysis.notes or ", ".join(i.name for i in analysis.items)

        try:
            with self._cursor() as cur:
                cur.execute(
                    """INSERT INTO food_logs (
                        user_id, meal_type, description, items,
                        calories, protein_g, carbs_g, fats_g, fiber_g,
                        glycemic_load_estimate, insulin_flag, flagged_items,
                        source, confidence, notes
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id, logged_at""",
                    (
                        user_id, analysis.meal_type, description, items_json,
                        analysis.totals.get("calories", 0),
                        analysis.totals.get("protein_g", 0),
                        analysis.totals.get("carbs_g", 0),
                        analysis.totals.get("fats_g", 0),
                        analysis.totals.get("fiber_g", 0),
                        analysis.glycemic_load_estimate,
                        analysis.insulin_flag,
                        analysis.flagged_items or [],
                        source, analysis.confidence, analysis.notes,
                    ),
                )
                row = cur.fetchone()
                log_id, logged_at = row

            # Actualizar nutrition_days
            self._update_nutrition_day(user_id, today, analysis)

            # Disparar warning extra si flags acumulados >= 2
            asyncio.ensure_future(self._maybe_extra_warning(user_id))

            return FoodLog(
                id=log_id, user_id=user_id, logged_at=logged_at,
                meal_type=analysis.meal_type, description=description,
                analysis=analysis,
            )
        except Exception as exc:
            log.error("[nutrition] log_food error: %s", exc)
            return None

    async def log_food_from_photo(self, user_id: str, image_path: str, meal_type: str = "other") -> tuple[FoodAnalysis, Optional[FoodLog]]:
        """Analyze + log en un solo paso desde foto."""
        analysis = await self.analyze_food_photo(image_path, meal_type)
        food_log = self.log_food(user_id, analysis, source="photo")
        return analysis, food_log

    async def log_food_from_text(self, user_id: str, description: str, meal_type: str = "other") -> tuple[FoodAnalysis, Optional[FoodLog]]:
        """Analyze + log en un solo paso desde texto."""
        analysis = await self.analyze_food_text(description, meal_type)
        food_log = self.log_food(user_id, analysis, source="text")
        return analysis, food_log

    def _update_nutrition_day(self, user_id: str, day: date, analysis: FoodAnalysis) -> None:
        try:
            with self._cursor() as cur:
                cur.execute(
                    """INSERT INTO nutrition_days (user_id, day, total_calories, total_protein, total_carbs, total_fats, total_fiber, insulin_flags_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, day) DO UPDATE SET
                        total_calories = nutrition_days.total_calories + EXCLUDED.total_calories,
                        total_protein = nutrition_days.total_protein + EXCLUDED.total_protein,
                        total_carbs = nutrition_days.total_carbs + EXCLUDED.total_carbs,
                        total_fats = nutrition_days.total_fats + EXCLUDED.total_fats,
                        total_fiber = nutrition_days.total_fiber + EXCLUDED.total_fiber,
                        insulin_flags_count = nutrition_days.insulin_flags_count + EXCLUDED.insulin_flags_count,
                        updated_at = NOW()""",
                    (
                        user_id, day,
                        analysis.totals.get("calories", 0),
                        analysis.totals.get("protein_g", 0),
                        analysis.totals.get("carbs_g", 0),
                        analysis.totals.get("fats_g", 0),
                        analysis.totals.get("fiber_g", 0),
                        1 if analysis.insulin_flag else 0,
                    ),
                )
            # Actualizar adherence_score
            self._refresh_adherence(user_id, day)
        except Exception as exc:
            log.warning("[nutrition] _update_nutrition_day error: %s", exc)

    def _refresh_adherence(self, user_id: str, day: date) -> None:
        try:
            profile = self.get_profile(user_id)
            with self._cursor() as cur:
                cur.execute(
                    "SELECT total_calories, total_protein, total_water_ml, total_fiber, insulin_flags_count "
                    "FROM nutrition_days WHERE user_id=%s AND day=%s",
                    (user_id, day),
                )
                row = cur.fetchone()
                if not row:
                    return
                day_data = {
                    "total_calories": row[0], "total_protein": row[1],
                    "total_water_ml": row[2], "total_fiber": row[3],
                    "insulin_flags_count": row[4],
                }
                score = _compute_adherence(day_data, profile)
                cur.execute(
                    "UPDATE nutrition_days SET adherence_score=%s WHERE user_id=%s AND day=%s",
                    (score, user_id, day),
                )
        except Exception as exc:
            log.warning("[nutrition] _refresh_adherence error: %s", exc)

    async def _maybe_extra_warning(self, user_id: str) -> None:
        """Si flags_count >= 2 hoy, disparar aviso extra via reminders."""
        try:
            from modules.nutrition.reminders import insulin_flag_extra_warning
            today = _today_utc()
            with self._cursor() as cur:
                cur.execute(
                    "SELECT insulin_flags_count FROM nutrition_days WHERE user_id=%s AND day=%s",
                    (user_id, today),
                )
                row = cur.fetchone()
                if row and row[0] >= 2:
                    await insulin_flag_extra_warning(user_id)
        except Exception as exc:
            log.debug("[nutrition] _maybe_extra_warning error: %s", exc)

    # ── Daily Summary ────────────────────────────────────────────────────

    def get_daily_summary(self, user_id: str = "admin", target_date: Optional[date] = None) -> DailySummary:
        today = target_date or _today_utc()
        profile = self.get_profile(user_id)

        # Obtener nutrition_days
        day_data = {
            "total_calories": 0.0, "total_protein": 0.0, "total_carbs": 0.0,
            "total_fats": 0.0, "total_fiber": 0.0, "total_water_ml": 0,
            "insulin_flags_count": 0,
        }
        meals: list[dict] = []

        try:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT total_calories, total_protein, total_carbs, total_fats, "
                    "total_fiber, total_water_ml, insulin_flags_count "
                    "FROM nutrition_days WHERE user_id=%s AND day=%s",
                    (user_id, today),
                )
                row = cur.fetchone()
                if row:
                    day_data = {
                        "total_calories": row[0], "total_protein": row[1],
                        "total_carbs": row[2], "total_fats": row[3],
                        "total_fiber": row[4], "total_water_ml": row[5] or 0,
                        "insulin_flags_count": row[6],
                    }

                # Meals del día
                from datetime import datetime as _dt
                day_start = _dt.combine(today, _dt.min.time()).replace(tzinfo=timezone.utc)
                day_end = _dt.combine(today, _dt.max.time()).replace(tzinfo=timezone.utc)
                cur.execute(
                    "SELECT meal_type, description, calories, insulin_flag, flagged_items, logged_at "
                    "FROM food_logs WHERE user_id=%s AND logged_at BETWEEN %s AND %s ORDER BY logged_at",
                    (user_id, day_start, day_end),
                )
                for r in cur.fetchall():
                    meals.append({
                        "meal_type": r[0], "description": r[1],
                        "calories": r[2], "insulin_flag": r[3],
                        "flagged_items": r[4] or [],
                        "logged_at": r[5].isoformat() if r[5] else None,
                    })
        except Exception as exc:
            log.warning("[nutrition] get_daily_summary error: %s", exc)

        targets = {
            "calories": profile.get("daily_calories", 2000),
            "protein_g": profile.get("daily_protein_g", 175),
            "carbs_g": profile.get("daily_carbs_g", 150),
            "fats_g": profile.get("daily_fats_g", 65),
            "water_ml": profile.get("daily_water_ml", 3000),
            "fiber_g": profile.get("daily_fiber_g", 35),
        }

        adherence = _compute_adherence(day_data, profile)
        if adherence >= 0.75 and day_data["insulin_flags_count"] == 0:
            indicator = "🟢"
        elif adherence >= 0.50 or day_data["insulin_flags_count"] <= 1:
            indicator = "🟡"
        else:
            indicator = "🔴"

        return DailySummary(
            date=today,
            user_id=user_id,
            calories=day_data["total_calories"],
            protein_g=day_data["total_protein"],
            carbs_g=day_data["total_carbs"],
            fats_g=day_data["total_fats"],
            fiber_g=day_data["total_fiber"],
            water_ml=day_data["total_water_ml"],
            insulin_flags_count=day_data["insulin_flags_count"],
            targets=targets,
            adherence_score=adherence,
            status_indicator=indicator,
            meals=meals,
        )

    # ── Suggest next meal ────────────────────────────────────────────────

    async def suggest_next_meal(self, user_id: str = "admin") -> MealSuggestion:
        """Sugiere próxima comida respetando perfil IR y timing."""
        from modules.nutrition.prompts import SUGGEST_NEXT_MEAL_PROMPT
        profile = self.get_profile(user_id)
        summary = self.get_daily_summary(user_id)
        gap_hours = profile.get("meal_min_gap_hours", 4)

        # Verificar timing: cuándo fue el último food_log
        last_meal_hours_ago: Optional[float] = None
        try:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT logged_at FROM food_logs WHERE user_id=%s ORDER BY logged_at DESC LIMIT 1",
                    (user_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    from datetime import datetime as _dt
                    last_dt = row[0]
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    diff = datetime.now(timezone.utc) - last_dt
                    last_meal_hours_ago = diff.total_seconds() / 3600
        except Exception as exc:
            log.warning("[nutrition] suggest timing check error: %s", exc)

        if last_meal_hours_ago is not None and last_meal_hours_ago < gap_hours:
            wait_h = gap_hours - last_meal_hours_ago
            return MealSuggestion(
                suggestions=[],
                timing_note=None,
                wait_message=(
                    f"Para respetar el ritmo de tu plan IR, lo ideal es esperar al menos {gap_hours} horas entre comidas. "
                    f"Comiste hace {last_meal_hours_ago:.1f}h — te quedan ~{wait_h:.1f}h. "
                    "Esto le da tiempo a tu insulina de bajar antes de volver a subir."
                ),
            )

        # Calcular macros restantes
        remaining = {
            "calories": max(0, summary.targets.get("calories", 2000) - summary.calories),
            "protein_g": max(0, summary.targets.get("protein_g", 175) - summary.protein_g),
            "carbs_g": max(0, summary.targets.get("carbs_g", 150) - summary.carbs_g),
            "fats_g": max(0, summary.targets.get("fats_g", 65) - summary.fats_g),
            "fiber_g": max(0, summary.targets.get("fiber_g", 35) - summary.fiber_g),
        }

        # Catálogo de platos peruanos como contexto
        from modules.nutrition.peru_food import PERU_FOOD
        safe_dishes = [name for name, info in PERU_FOOD.items() if not info["flag"]]
        safe_context = "Platos seguros para IR: " + ", ".join(safe_dishes[:10])

        llm = self._get_llm()
        if llm is None:
            return MealSuggestion(
                suggestions=[{"name": "Ceviche con quinua", "insulin_safe": True, "why": "Proteína alta, sin carbos refinados."}],
                timing_note="Buena opción para tu perfil IR.",
            )

        user_msg = (
            f"Macros restantes del día: {remaining}\n"
            f"Flags de insulina hoy: {summary.insulin_flags_count}\n"
            f"{safe_context}\n\n"
            f"{SUGGEST_NEXT_MEAL_PROMPT}"
        )
        raw = await llm.generate(user_msg, force_cloud=True)
        data = _parse_food_json(str(raw))
        if not data:
            return MealSuggestion(
                suggestions=[{"name": "Ceviche", "insulin_safe": True, "why": "Alto en proteína, bajo en carbos."}],
                timing_note="Opción segura para IR.",
            )

        return MealSuggestion(
            suggestions=data.get("suggestions") or [],
            timing_note=data.get("timing_note"),
        )

    # ── Water ────────────────────────────────────────────────────────────

    def log_water(self, user_id: str = "admin", amount_ml: int = 250) -> WaterStatus:
        today = _today_utc()
        profile = self.get_profile(user_id)
        target_ml = profile.get("daily_water_ml", 3000)

        # Calcular cumulative
        cumulative = amount_ml
        try:
            from datetime import datetime as _dt
            day_start = _dt.combine(today, _dt.min.time()).replace(tzinfo=timezone.utc)
            day_end = _dt.combine(today, _dt.max.time()).replace(tzinfo=timezone.utc)
            with self._cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(SUM(amount_ml),0) FROM water_logs "
                    "WHERE user_id=%s AND logged_at BETWEEN %s AND %s",
                    (user_id, day_start, day_end),
                )
                prev = cur.fetchone()[0] or 0
                cumulative = prev + amount_ml
                cur.execute(
                    "INSERT INTO water_logs (user_id, amount_ml, cumulative_day_ml) VALUES (%s,%s,%s)",
                    (user_id, amount_ml, cumulative),
                )
            # Actualizar nutrition_days
            with self._cursor() as cur:
                cur.execute(
                    """INSERT INTO nutrition_days (user_id, day, total_water_ml)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, day) DO UPDATE SET
                        total_water_ml = %s,
                        updated_at = NOW()""",
                    (user_id, today, cumulative, cumulative),
                )
        except Exception as exc:
            log.warning("[nutrition] log_water error: %s", exc)

        return WaterStatus(
            user_id=user_id,
            amount_ml=amount_ml,
            cumulative_day_ml=cumulative,
            target_ml=target_ml,
            pct=min(1.0, cumulative / target_ml) if target_ml > 0 else 0.0,
        )

    # ── Steps ────────────────────────────────────────────────────────────

    def log_steps(self, user_id: str = "admin", steps: int = 0, log_date: Optional[date] = None, source: str = "manual") -> StepStatus:
        today = log_date or _today_utc()
        profile = self.get_profile(user_id)
        target_steps = profile.get("daily_steps", 8000)
        bonus_kcal = max(0, (steps - target_steps) // 1000 * 50)

        try:
            with self._cursor() as cur:
                cur.execute(
                    """INSERT INTO step_logs (user_id, log_date, steps, source, bonus_kcal)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, log_date) DO UPDATE SET
                        steps = EXCLUDED.steps,
                        source = EXCLUDED.source,
                        bonus_kcal = EXCLUDED.bonus_kcal,
                        logged_at = NOW()""",
                    (user_id, today, steps, source, bonus_kcal),
                )
        except Exception as exc:
            log.warning("[nutrition] log_steps error: %s", exc)

        return StepStatus(
            user_id=user_id, steps=steps, target=target_steps,
            bonus_kcal=bonus_kcal, log_date=today,
        )

    # ── Weight ───────────────────────────────────────────────────────────

    def log_weight(self, user_id: str = "admin", weight_kg: float = 0.0, log_date: Optional[date] = None, source: str = "manual", notes: str = "") -> WeightStatus:
        today = log_date or _today_utc()
        try:
            with self._cursor() as cur:
                cur.execute(
                    "INSERT INTO weight_logs (user_id, log_date, weight_kg, source, notes) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (user_id, today, weight_kg, source, notes or None),
                )
        except Exception as exc:
            log.warning("[nutrition] log_weight error: %s", exc)

        return WeightStatus(user_id=user_id, weight_kg=weight_kg, log_date=today, source=source)

    def get_weight_trend(self, user_id: str = "admin", weeks: int = 12) -> list[tuple[date, float]]:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT log_date, weight_kg FROM weight_logs "
                    "WHERE user_id=%s AND log_date >= NOW() - INTERVAL '%s weeks' "
                    "ORDER BY log_date",
                    (user_id, weeks),
                )
                return [(row[0], row[1]) for row in cur.fetchall()]
        except Exception as exc:
            log.warning("[nutrition] get_weight_trend error: %s", exc)
            return []

    # ── Weekly report ────────────────────────────────────────────────────

    async def get_weekly_nutrition_report(self, user_id: str = "admin") -> dict:
        """Reporte semanal con análisis LLM."""
        from modules.nutrition.prompts import WEEKLY_REPORT_PROMPT

        try:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT day, total_calories, total_protein, total_carbs, total_fats, "
                    "total_fiber, total_water_ml, insulin_flags_count, adherence_score "
                    "FROM nutrition_days WHERE user_id=%s AND day >= NOW() - INTERVAL '7 days' "
                    "ORDER BY day",
                    (user_id,),
                )
                rows = cur.fetchall()
        except Exception as exc:
            log.warning("[nutrition] get_weekly_nutrition_report error: %s", exc)
            rows = []

        days_data = [
            {
                "day": str(r[0]), "calories": r[1], "protein_g": r[2],
                "carbs_g": r[3], "fats_g": r[4], "fiber_g": r[5],
                "water_ml": r[6], "insulin_flags": r[7], "adherence": r[8],
            }
            for r in rows
        ]

        if not days_data:
            return {
                "summary": "No hay datos de los últimos 7 días.",
                "strengths": [], "improvements": [],
                "protein_trend": "stable", "insulin_load_trend": "stable",
                "recommendation": "Empezá a registrar tus comidas para ver tendencias.",
                "days": days_data,
            }

        llm = self._get_llm()
        report_dict = {
            "summary": "Sin análisis disponible.", "strengths": [], "improvements": [],
            "protein_trend": "stable", "insulin_load_trend": "stable",
            "recommendation": "Seguí registrando para obtener análisis.", "days": days_data,
        }

        if llm:
            try:
                prompt = f"Datos semanales: {json.dumps(days_data, ensure_ascii=False)}\n\n{WEEKLY_REPORT_PROMPT}"
                raw = await llm.generate(prompt, force_cloud=True)
                parsed = _parse_food_json(str(raw))
                if parsed:
                    report_dict.update(parsed)
            except Exception as exc:
                log.warning("[nutrition] weekly report LLM error: %s", exc)

        report_dict["days"] = days_data
        return report_dict


# ── Singleton ────────────────────────────────────────────────────────────────

_service: Optional[NutritionService] = None
_service_lock = threading.Lock()


def get_nutrition_service() -> NutritionService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = NutritionService()
    return _service
