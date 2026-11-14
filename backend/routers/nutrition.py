"""Nutrition router — endpoints REST para tracking de nutrición, agua, pasos y peso.

Patrón: routers/schedule.py para auth + routers/vision.py para multipart uploads.
"""
from __future__ import annotations

import secrets
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core.auth_middleware import require_auth

router = APIRouter()

from core.paths import UPLOAD_DIR


def _get_service():
    from modules.nutrition.service import get_nutrition_service
    return get_nutrition_service()


# ── Pydantic models ───────────────────────────────────────────────────────────

class ProfileUpdateBody(BaseModel):
    daily_calories: Optional[int] = None
    daily_protein_g: Optional[int] = None
    daily_carbs_g: Optional[int] = None
    daily_fats_g: Optional[int] = None
    daily_water_ml: Optional[int] = None
    daily_fiber_g: Optional[int] = None
    daily_steps: Optional[int] = None
    insulin_resistance_mode: Optional[bool] = None
    meal_min_gap_hours: Optional[int] = None
    health_source: Optional[str] = None


class FoodLogBody(BaseModel):
    user_id: str = "admin"
    meal_type: str = "other"
    description: str
    analysis: dict


class WaterBody(BaseModel):
    user_id: str = "admin"
    amount_ml: int = 250


class StepsBody(BaseModel):
    user_id: str = "admin"
    steps: int
    date: Optional[str] = None
    source: str = "manual"


class WeightBody(BaseModel):
    user_id: str = "admin"
    weight_kg: float
    date: Optional[str] = None
    source: str = "manual"
    notes: Optional[str] = None


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str).date()
    except (ValueError, TypeError):
        try:
            return date.fromisoformat(date_str)
        except (ValueError, TypeError):
            return None


# ── Profile ───────────────────────────────────────────────────────────────────

@router.get("/nutrition/profile")
async def get_profile(session: dict = Depends(require_auth), user_id: str = "admin"):
    return _get_service().get_profile(user_id)


@router.put("/nutrition/profile")
async def update_profile(body: ProfileUpdateBody, session: dict = Depends(require_auth), user_id: str = "admin"):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    return _get_service().update_profile(user_id, **updates)


# ── Food analysis ─────────────────────────────────────────────────────────────

@router.post("/nutrition/food/analyze")
async def analyze_food(
    session: dict = Depends(require_auth),
    text: Optional[str] = Form(None),
    meal_type: str = Form("other"),
    file: Optional[UploadFile] = File(None),
):
    """Analiza comida por foto o texto. No persiste.
    Auth via Authorization/X-Token header (Depends require_auth); token Form param removed (auth-header-migration REQ-3).
    """
    svc = _get_service()

    if file is not None:
        # Análisis visual
        ext = Path(file.filename or "upload.jpg").suffix or ".jpg"
        tmp = UPLOAD_DIR / f"nutr_{secrets.token_hex(8)}{ext}"
        try:
            with tmp.open("wb") as buf:
                shutil.copyfileobj(file.file, buf)
            analysis = await svc.analyze_food_photo(str(tmp), meal_type)
        finally:
            if tmp.exists():
                tmp.unlink()

        return {
            "source": "photo",
            "meal_type": meal_type,
            "items": [i.__dict__ for i in analysis.items],
            "totals": analysis.totals,
            "glycemic_load_estimate": analysis.glycemic_load_estimate,
            "insulin_flag": analysis.insulin_flag,
            "flagged_items": analysis.flagged_items,
            "nudges": analysis.nudges,
            "confidence": analysis.confidence,
            "notes": analysis.notes,
        }

    if text:
        analysis = await svc.analyze_food_text(text, meal_type)
        return {
            "source": "text",
            "meal_type": meal_type,
            "items": [i.__dict__ for i in analysis.items],
            "totals": analysis.totals,
            "glycemic_load_estimate": analysis.glycemic_load_estimate,
            "insulin_flag": analysis.insulin_flag,
            "flagged_items": analysis.flagged_items,
            "nudges": analysis.nudges,
            "confidence": analysis.confidence,
            "notes": analysis.notes,
        }

    raise HTTPException(status_code=400, detail="Debe enviar 'text' o un archivo de imagen")


@router.post("/nutrition/food/log")
async def log_food(body: FoodLogBody, session: dict = Depends(require_auth)):
    """Persiste un análisis ya realizado en food_logs."""
    from modules.nutrition.service import FoodAnalysis, FoodItem

    # Reconstituir FoodAnalysis desde el dict
    analysis_dict = body.analysis
    items = [
        FoodItem(**{k: v for k, v in i.items() if k in FoodItem.__dataclass_fields__})
        for i in (analysis_dict.get("items") or [])
    ]
    analysis = FoodAnalysis(
        items=items,
        totals=analysis_dict.get("totals") or {},
        glycemic_load_estimate=analysis_dict.get("glycemic_load_estimate") or "medium",
        insulin_flag=bool(analysis_dict.get("insulin_flag")),
        flagged_items=analysis_dict.get("flagged_items") or [],
        confidence=analysis_dict.get("confidence") or "medium",
        notes=analysis_dict.get("notes") or "",
        nudges=analysis_dict.get("nudges") or [],
        source=analysis_dict.get("source") or "manual",
        meal_type=body.meal_type,
    )
    svc = _get_service()
    food_log = svc.log_food(body.user_id, analysis, source=analysis.source)
    if food_log is None:
        raise HTTPException(status_code=500, detail="Error al persistir el log")
    return {"logged": True, "id": food_log.id, "meal_type": food_log.meal_type}


@router.post("/nutrition/food/log-quick")
async def log_food_quick(
    session: dict = Depends(require_auth),
    user_id: str = Form("admin"),
    text: Optional[str] = Form(None),
    meal_type: str = Form("other"),
    file: Optional[UploadFile] = File(None),
):
    """One-shot: analiza + persiste. Sigue patrón de vision.py.
    Auth via Authorization/X-Token header (Depends require_auth); token Form param removed (auth-header-migration REQ-3).
    """
    svc = _get_service()

    if file is not None:
        ext = Path(file.filename or "upload.jpg").suffix or ".jpg"
        tmp = UPLOAD_DIR / f"nutr_{secrets.token_hex(8)}{ext}"
        try:
            with tmp.open("wb") as buf:
                shutil.copyfileobj(file.file, buf)
            analysis, food_log = await svc.log_food_from_photo(user_id, str(tmp), meal_type)
        finally:
            if tmp.exists():
                tmp.unlink()
    elif text:
        analysis, food_log = await svc.log_food_from_text(user_id, text, meal_type)
    else:
        raise HTTPException(status_code=400, detail="Debe enviar 'text' o un archivo de imagen")

    return {
        "logged": True,
        "log_id": food_log.id if food_log else None,
        "meal_type": meal_type,
        "totals": analysis.totals,
        "insulin_flag": analysis.insulin_flag,
        "flagged_items": analysis.flagged_items,
        "nudges": analysis.nudges,
        "confidence": analysis.confidence,
    }


# ── Daily summary ─────────────────────────────────────────────────────────────

@router.get("/nutrition/daily")
async def get_daily(session: dict = Depends(require_auth), user_id: str = "admin", date: Optional[str] = None):
    target_date = _parse_date(date)
    summary = _get_service().get_daily_summary(user_id, target_date)
    return {
        "date": str(summary.date),
        "user_id": summary.user_id,
        "calories": summary.calories,
        "protein_g": summary.protein_g,
        "carbs_g": summary.carbs_g,
        "fats_g": summary.fats_g,
        "fiber_g": summary.fiber_g,
        "water_ml": summary.water_ml,
        "insulin_flags_count": summary.insulin_flags_count,
        "targets": summary.targets,
        "adherence_score": summary.adherence_score,
        "status_indicator": summary.status_indicator,
        "meals": summary.meals,
    }


# ── Suggest next meal ─────────────────────────────────────────────────────────

@router.get("/nutrition/suggest")
async def suggest_meal(session: dict = Depends(require_auth), user_id: str = "admin"):
    suggestion = await _get_service().suggest_next_meal(user_id)
    return {
        "suggestions": suggestion.suggestions,
        "timing_note": suggestion.timing_note,
        "wait_message": suggestion.wait_message,
    }


# ── Water ─────────────────────────────────────────────────────────────────────

@router.post("/nutrition/water")
async def log_water(body: WaterBody, session: dict = Depends(require_auth)):
    status = _get_service().log_water(body.user_id, body.amount_ml)
    return {
        "amount_ml": status.amount_ml,
        "cumulative_day_ml": status.cumulative_day_ml,
        "target_ml": status.target_ml,
        "pct": status.pct,
    }


# ── Steps ─────────────────────────────────────────────────────────────────────

@router.post("/nutrition/steps")
async def log_steps(body: StepsBody, session: dict = Depends(require_auth)):
    log_date = _parse_date(body.date)
    status = _get_service().log_steps(body.user_id, body.steps, log_date, body.source)
    return {
        "steps": status.steps,
        "target": status.target,
        "bonus_kcal": status.bonus_kcal,
        "log_date": str(status.log_date),
    }


# ── Weight ────────────────────────────────────────────────────────────────────

@router.post("/nutrition/weight")
async def log_weight(body: WeightBody, session: dict = Depends(require_auth)):
    log_date = _parse_date(body.date)
    status = _get_service().log_weight(body.user_id, body.weight_kg, log_date, body.source, body.notes or "")
    return {
        "weight_kg": status.weight_kg,
        "log_date": str(status.log_date),
        "source": status.source,
    }


@router.get("/nutrition/weight/trend")
async def weight_trend(session: dict = Depends(require_auth), user_id: str = "admin", weeks: int = 12):
    trend = _get_service().get_weight_trend(user_id, weeks)
    return {
        "user_id": user_id,
        "weeks": weeks,
        "trend": [{"date": str(d), "weight_kg": w} for d, w in trend],
    }


# ── Weekly report ─────────────────────────────────────────────────────────────

@router.get("/nutrition/weekly")
async def weekly_report(session: dict = Depends(require_auth), user_id: str = "admin"):
    report = await _get_service().get_weekly_nutrition_report(user_id)
    return report
