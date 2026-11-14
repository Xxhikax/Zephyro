"""Recommender — genera recomendaciones contextuales basadas en eventos de agenda.

Usa Haiku para generar sugerencias personalizadas según categoría y duración.
"""
from __future__ import annotations

from typing import Optional

_RECOMMENDATION_PROMPT = """Eres un asistente de productividad y bienestar. El usuario tiene agendado el siguiente evento:

- Título: "{title}"
- Categoría: {category}
- Duración: {duration_min} minutos
- Hora: {start_time}

Basado en la categoría, genera UNA recomendación breve y accionable (1-2 líneas, en español):

- university: recordatorio de material, sugerencia de estudio previo, o tarea pendiente
- exercise: tipo de ejercicio, hidratación, playlist, o estiramiento previo
- personal_project: sugerencia de enfoque, división de tareas, o pausa activa
- client_project: priorización, preparación de entregables, o seguimiento
- course_study: técnica de estudio, descansos, o recurso complementario
- other: recomendación genérica de organización

Si la duración es > 90 minutos, SIEMPRE sugiere una pausa o descanso activo.

Responde SOLO con la recomendación, sin prefijos ni explicaciones."""


async def generate_recommendation(
    title: str,
    category: str,
    duration_min: int,
    start_time: str = "",
    llm_manager: Optional[object] = None,
) -> Optional[str]:
    """Genera una recomendación contextual usando Haiku."""
    if llm_manager is None or not hasattr(llm_manager, 'generate'):
        return _fallback_recommendation(category, duration_min)

    import asyncio
    prompt = _RECOMMENDATION_PROMPT.format(
        title=title,
        category=category,
        duration_min=duration_min,
        start_time=start_time or "(no especificada)",
    )

    try:
        full = "Eres un coach de productividad. Responde en español, 1-2 líneas.\n\n" + prompt
        result = await asyncio.to_thread(
            lambda: llm_manager.generate(
                full,
                force_cloud=True,
                force_local=False,
            )
        )
        return result.strip()
    except Exception:
        return _fallback_recommendation(category, duration_min)


def _fallback_recommendation(category: str, duration_min: int) -> str:
    """Recomendación por defecto sin LLM."""
    base = {
        "university": "Repasá los temas de la clase anterior y prepará tus dudas.",
        "course_study": "Usá la técnica Pomodoro: 25 min estudio, 5 min descanso.",
        "personal_project": "Dividí la sesión en objetivos pequeños y alcanzables.",
        "client_project": "Revisá los requerimientos y priorizá las tareas del día.",
        "exercise": "Empezá con 5 min de calentamiento y mantenete hidratado.",
        "other": "Organizá tus notas y definí objetivos claros para esta sesión.",
    }.get(category, "Definí un objetivo claro para esta sesión.")

    if duration_min > 90:
        base += " No olvides tomar una pausa activa de 5 min (estiramientos, agua, caminar)."

    return base


def get_active_break(available_minutes: int = 5) -> str:
    """Sugiere una mini-rutina de ejercicios para pausas activas."""
    exercises = [
        "Rotación de cuello (30s cada lado)",
        "Estiramiento de brazos sobre la cabeza (30s)",
        "Flexión de muñecas (30s cada mano)",
        "Sentadillas suaves (10 repeticiones)",
        "Caminar en el lugar (1 min)",
        "Respiración profunda (5 ciclos: inhalar 4s, exhalar 4s)",
    ]
    count = min(len(exercises), max(2, available_minutes // 2))
    import random
    selected = random.sample(exercises, count)
    return "💪 Pausa activa:\n" + "\n".join(f"  • {e}" for e in selected)
