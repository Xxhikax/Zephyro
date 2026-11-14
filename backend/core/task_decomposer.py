"""
task_decomposer — Parte una entrada en sub-instrucciones independientes.

Una entrada como "agendá cálculo el lunes y después poné música en YT Music"
contiene DOS tareas de dominios distintos (skill schedule + automation media).
Este módulo las separa para que el orquestador las rutee y ejecute en orden.

El riesgo es partir de más: "ponme música tranquila y relajante" es UNA acción,
y "agendá cálculo y álgebra el lunes" es UN evento con dos materias. La regla
que evita esto: un segmento solo cuenta como tarea independiente si tiene su
PROPIO verbo de acción (o trigger de skill). Si el segmento de la derecha del
conector no tiene verbo, no se parte.

Fast-path heurístico (sin LLM, determinista, testeable). Para casos difíciles
(conector secuencial explícito que el heurístico no pudo separar limpio) se
expone ``needs_llm_fallback`` + ``llm_decompose`` que el orquestador puede usar.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from core import verbs as _verbs

# Conectores que pueden separar tareas. Los secuenciales explícitos
# ("y luego", "y después", "luego", "después") son señal fuerte de secuencia;
# "y", ",", ";" son débiles y solo separan si ambos lados tienen verbo.
_STRONG_CONNECTORS = r"y\s+luego|y\s+despu[eé]s|luego|despu[eé]s"
_WEAK_CONNECTORS = r"y|,|;"
_SPLIT_RE = re.compile(rf"\s*(?:{_STRONG_CONNECTORS}|{_WEAK_CONNECTORS})\s+", re.IGNORECASE)
_STRONG_RE = re.compile(rf"\b(?:{_STRONG_CONNECTORS})\b", re.IGNORECASE)

# Todas las formas verbales (automation + media + search + skills + nav + fs)
# vienen de la fuente única core/verbs.py — incluye voseo automáticamente.
_AUTO_VERBS_RE = re.compile(r"\b" + _verbs.verbs_pattern() + r"\b", re.IGNORECASE)


def _has_action_verb(segment: str) -> bool:
    """True si el segmento contiene un verbo de automatización propio."""
    return bool(_AUTO_VERBS_RE.search(segment))


def decompose(
    user_input: str,
    *,
    has_action: Optional[Callable[[str], bool]] = None,
) -> list[str]:
    """
    Devuelve la lista de sub-instrucciones. 1 elemento = tarea única.

    ``has_action`` permite al orquestador inyectar un checker que también
    reconozca triggers de skill (no solo AUTO_VERBS). Default: solo AUTO_VERBS.
    """
    text = (user_input or "").strip()
    if not text:
        return [text]

    segments = [s.strip() for s in _SPLIT_RE.split(text) if s.strip()]
    if len(segments) <= 1:
        return [text]

    checker = has_action or _has_action_verb
    # Solo separamos si CADA segmento tiene su propio verbo/trigger. Si alguno
    # no lo tiene, es casi seguro un falso positivo ("X y Y" como un solo objeto).
    if all(checker(seg) for seg in segments):
        return segments

    return [text]


def needs_llm_fallback(
    user_input: str,
    *,
    has_action: Optional[Callable[[str], bool]] = None,
) -> bool:
    """
    True cuando hay un conector secuencial EXPLÍCITO ("y luego"/"y después")
    pero el heurístico no logró separar limpio (algún segmento sin verbo). En
    ese caso un LLM puede desambiguar mejor. Para conectores débiles no vale la
    pena el costo del LLM: nos quedamos con "tarea única".
    """
    text = (user_input or "").strip()
    if not _STRONG_RE.search(text):
        return False
    # Si el heurístico ya separó en >1, no hace falta el LLM.
    return len(decompose(text, has_action=has_action)) == 1


async def llm_decompose(
    user_input: str,
    llm_caller: Callable[[str], "object"],
) -> list[str]:
    """
    Fallback con LLM: devuelve la lista de tareas. ``llm_caller`` es un callable
    async que recibe un prompt y devuelve string. Ante cualquier error de
    parseo, cae a ``[user_input]`` (tarea única) — nunca rompe el flujo.
    """
    prompt = _LLM_PROMPT.replace("{input}", user_input)
    try:
        raw = await llm_caller(prompt)
    except Exception:
        return [user_input.strip()]

    tasks = _parse_llm_tasks(raw)
    return tasks or [user_input.strip()]


_LLM_PROMPT = """Separá la siguiente instrucción del usuario en tareas INDEPENDIENTES.
Una instrucción puede contener varias acciones distintas ("agendá X y después poné música")
o ser una sola ("ponme música tranquila y relajante" es UNA sola).

Reglas:
- Solo separá si son acciones genuinamente distintas, cada una con su propio verbo.
- NO separes objetos de una misma acción ("cálculo y álgebra" en un mismo evento es UNO).
- Mantené el texto de cada tarea tal como lo diría el usuario.

Instrucción: {input}

Devolvé SOLO JSON: {"tasks": ["tarea 1", "tarea 2", ...]}
"""


def _parse_llm_tasks(raw: str) -> list[str]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        return []
    return [str(t).strip() for t in tasks if str(t).strip()]
