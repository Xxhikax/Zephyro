"""Recordatorios de nutrición programados en APScheduler.

Cada función es async, toma user_id, lee el estado y envía vía ChannelManager.broadcast_text.
Si el app no está disponible (test/import-time), log warning y return.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

log = logging.getLogger("zephyro.nutrition.reminders")


def _get_channel_manager():
    try:
        import web_app
        return web_app.app.state.channel_manager
    except Exception:
        return None


def _get_service():
    try:
        from modules.nutrition.service import get_nutrition_service
        return get_nutrition_service()
    except Exception:
        return None


def _now_lima() -> datetime:
    """Hora actual en zona Lima (UTC-5)."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Lima"))


async def remind_breakfast(user_id: str = "admin") -> None:
    """07:30 — Mensaje de bienvenida con objetivo del día."""
    cm = _get_channel_manager()
    if cm is None:
        log.warning("[nutrition.reminder] ChannelManager no disponible — remind_breakfast skipped")
        return

    svc = _get_service()
    targets = {"calories": 2000, "protein_g": 175, "water_ml": 3000}
    if svc:
        try:
            profile = svc.get_profile(user_id)
            targets = {
                "calories": profile.get("daily_calories", 2000),
                "protein_g": profile.get("daily_protein_g", 175),
                "water_ml": profile.get("daily_water_ml", 3000),
            }
        except Exception:
            pass

    texto = (
        f"Buenos días! Arrancamos el día.\n"
        f"Tu objetivo de hoy: {targets['calories']} kcal | {targets['protein_g']}g de proteína | {targets['water_ml']//1000}L de agua.\n"
        "Recordá: carbos low-GI, sin azúcar añadida, proteína en cada comida. "
        "Registrá tu desayuno con 'hice X' o desde el panel de Salud."
    )
    try:
        await cm.broadcast_text(texto)
    except Exception as exc:
        log.warning("[nutrition.reminder] remind_breakfast error: %s", exc)


async def remind_lunch_if_missing(user_id: str = "admin") -> None:
    """13:00 — Recordatorio de almuerzo si no hay log entre 12:00-13:00."""
    cm = _get_channel_manager()
    if cm is None:
        log.warning("[nutrition.reminder] ChannelManager no disponible — remind_lunch skipped")
        return

    svc = _get_service()
    has_lunch = False

    if svc:
        try:
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo
            lima = ZoneInfo("America/Lima")
            now_lima = _dt.now(lima)
            # Ventana 12:00-13:00 Lima de hoy
            window_start = now_lima.replace(hour=12, minute=0, second=0, microsecond=0)
            window_end = now_lima.replace(hour=13, minute=0, second=0, microsecond=0)
            # Convertir a UTC para consultar
            window_start_utc = window_start.astimezone(timezone.utc)
            window_end_utc = window_end.astimezone(timezone.utc)

            from core.postgres_pool import get_cursor
            with get_cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM food_logs WHERE user_id=%s "
                    "AND meal_type='lunch' AND logged_at BETWEEN %s AND %s",
                    (user_id, window_start_utc, window_end_utc),
                )
                count = cur.fetchone()[0]
                has_lunch = count > 0
        except Exception as exc:
            log.debug("[nutrition.reminder] lunch check error: %s", exc)

    if not has_lunch:
        texto = (
            "Son las 13:00 — hora del almuerzo! No olvidés registrar lo que comés.\n"
            "Un buen almuerzo IR: proteína (pollo, pescado, huevo) + verduras + poquito carbos low-GI (quinua, legumbres).\n"
            "Podés escribir 'hice [comida]' o enviar una foto."
        )
        try:
            await cm.broadcast_text(texto)
        except Exception as exc:
            log.warning("[nutrition.reminder] remind_lunch error: %s", exc)


async def remind_water_if_behind(user_id: str = "admin") -> None:
    """Cada 2h (08-20) — Recordatorio de agua si va atrasado vs pace esperado."""
    cm = _get_channel_manager()
    if cm is None:
        log.warning("[nutrition.reminder] ChannelManager no disponible — remind_water skipped")
        return

    svc = _get_service()
    if svc is None:
        return

    try:
        summary = svc.get_daily_summary(user_id)
        current_water = summary.water_ml
        target_water = summary.targets.get("water_ml", 3000)

        # Calcular pace esperado según hora Lima
        from zoneinfo import ZoneInfo
        now_lima = datetime.now(ZoneInfo("America/Lima"))
        # Distribución lineal entre 8am y 8pm (12 horas activas)
        hours_active = max(0, min(12, now_lima.hour - 8))
        expected_water = int(target_water * hours_active / 12) if hours_active > 0 else 0

        if current_water < expected_water * 0.75:
            deficit = expected_water - current_water
            texto = (
                f"Vas un poco atrasado con el agua. Llevas {current_water}ml de {target_water}ml.\n"
                f"En este momento deberías llevar ~{expected_water}ml. Te faltan {deficit}ml para el ritmo esperado.\n"
                "Tomá un vaso ahora (250ml) — registralo con '/agua 250'."
            )
            await cm.broadcast_text(texto)
    except Exception as exc:
        log.warning("[nutrition.reminder] remind_water error: %s", exc)


async def daily_nutrition_summary(user_id: str = "admin") -> None:
    """20:00 — Resumen del día con kcal, proteína y flags."""
    cm = _get_channel_manager()
    if cm is None:
        log.warning("[nutrition.reminder] ChannelManager no disponible — daily_summary skipped")
        return

    svc = _get_service()
    if svc is None:
        return

    try:
        summary = svc.get_daily_summary(user_id)
        targets = summary.targets

        cal_pct = int(summary.calories / targets["calories"] * 100) if targets.get("calories") else 0
        prot_pct = int(summary.protein_g / targets["protein_g"] * 100) if targets.get("protein_g") else 0
        water_pct = int(summary.water_ml / targets["water_ml"] * 100) if targets.get("water_ml") else 0

        flag_msg = ""
        if summary.insulin_flags_count > 0:
            flag_msg = f"\n⚠️ {summary.insulin_flags_count} alimento(s) con carga glucémica alta hoy."

        texto = (
            f"Resumen del día {summary.status_indicator}\n"
            f"Calorías: {summary.calories:.0f}/{targets['calories']} kcal ({cal_pct}%)\n"
            f"Proteína: {summary.protein_g:.0f}/{targets['protein_g']}g ({prot_pct}%)\n"
            f"Agua: {summary.water_ml}ml/{targets['water_ml']}ml ({water_pct}%)\n"
            f"Adherencia: {summary.adherence_score:.0%}{flag_msg}\n"
            "Buen trabajo hoy. Si te faltó algo, lo retomamos mañana."
        )
        await cm.broadcast_text(texto)
    except Exception as exc:
        log.warning("[nutrition.reminder] daily_summary error: %s", exc)


async def end_of_day_protein_check(user_id: str = "admin") -> None:
    """23:00 — Si proteína o fibra < target, nota accionable para mañana."""
    cm = _get_channel_manager()
    if cm is None:
        log.warning("[nutrition.reminder] ChannelManager no disponible — eod_protein skipped")
        return

    svc = _get_service()
    if svc is None:
        return

    try:
        summary = svc.get_daily_summary(user_id)
        targets = summary.targets
        messages = []

        if targets.get("protein_g") and summary.protein_g < targets["protein_g"] * 0.8:
            deficit_prot = targets["protein_g"] - summary.protein_g
            messages.append(
                f"Te faltaron ~{deficit_prot:.0f}g de proteína. Mañana sumá un huevo extra, "
                "una porción más de pollo o una lata de atún."
            )

        if targets.get("fiber_g") and summary.fiber_g < targets["fiber_g"] * 0.8:
            messages.append(
                "La fibra estuvo baja. Mañana incluí verduras en cada comida y considerá "
                "agregar quinua o lentejas."
            )

        if messages:
            texto = "Antes de dormir — nota para mañana:\n" + "\n".join(f"• {m}" for m in messages)
            await cm.broadcast_text(texto)
    except Exception as exc:
        log.warning("[nutrition.reminder] eod_protein_check error: %s", exc)


async def remind_steps_if_unlogged(user_id: str = "admin") -> None:
    """19:00 — Si no hay step_log de hoy, recordar registrar pasos."""
    cm = _get_channel_manager()
    if cm is None:
        log.warning("[nutrition.reminder] ChannelManager no disponible — remind_steps skipped")
        return

    try:
        from core.postgres_pool import get_cursor
        today = date.today()
        with get_cursor() as cur:
            cur.execute(
                "SELECT steps FROM step_logs WHERE user_id=%s AND log_date=%s",
                (user_id, today),
            )
            row = cur.fetchone()

        if row is None:
            texto = (
                "No registraste tus pasos de hoy. ¿Cuántos pasos caminaste?\n"
                "Escribí '/pasos N' (ej: /pasos 9500). "
                "Meta: 8000 pasos — cada 1000 pasos extra = ~50 kcal bonus."
            )
            await cm.broadcast_text(texto)
    except Exception as exc:
        log.warning("[nutrition.reminder] remind_steps error: %s", exc)


async def insulin_flag_extra_warning(user_id: str = "admin") -> None:
    """Llamado desde NutritionService.log_food cuando flags_count >= 2 ese día."""
    cm = _get_channel_manager()
    if cm is None:
        return

    try:
        texto = (
            "Ya van 2 o más alimentos con carga glucémica alta hoy.\n"
            "Esto genera picos de insulina repetidos — el patrón que más afecta la "
            "resistencia a insulina. Para el resto del día: proteína + verduras, sin carbos refinados.\n"
            "Tu páncreas te lo va a agradecer."
        )
        await cm.broadcast_text(texto)
    except Exception as exc:
        log.warning("[nutrition.reminder] insulin_flag_extra_warning error: %s", exc)
