"""
clarification_gate — Decide cuándo Zephyro debe preguntar de vuelta.

Problema: hoy el router cae a chat genérico cuando no está seguro, sin
preguntar. Este gate introduce la decisión explícita "¿ejecuto, pregunto, o
trato como charla?" basada en una señal de confianza unificada
(``core.confidence``) y el ranking de candidatos del skill registry.

Es un componente PURO (sin I/O, sin LLM): recibe candidatos ya construidos y
devuelve una decisión. Eso lo hace trivial de testear con una tabla de casos.
El orquestador es quien construye los candidatos (desde el registry y el
análisis del router) y quien ejecuta la decisión.

Reglas (umbrales en app_config):
    top >= CLARIFY_HIGH y sin rival dentro de AMBIGUITY_MARGIN → resolve
    2+ candidatos dentro de AMBIGUITY_MARGIN                   → clarify (ambigüedad)
    top en [CLARIFY_LOW, CLARIFY_HIGH) y parece comando        → clarify (confirmar)
    todo < CLARIFY_LOW y NO parece comando                     → chat (no preguntar)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from core.app_config import AUTO_VERBS, CLARIFY_HIGH, CLARIFY_LOW, AMBIGUITY_MARGIN

# Frases naturales por automation_intent, para armar preguntas legibles por voz.
_AUTOMATION_PHRASE: dict[str, str] = {
    "NAVIGATE": "lo abra en el navegador",
    "PLAY_MEDIA": "reproduzca eso",
    "SEARCH": "lo busque en la web",
    "INTERACT": "interactúe con la página",
    "PLAYBACK_CONTROL": "controle la reproducción",
    "BROWSE_AND_EXTRACT": "lea esa página",
    "MULTI_ACTION": "ejecute esas acciones",
}

# Frases naturales por nombre de skill conocida. Fallback: descripción de la skill.
_SKILL_PHRASE: dict[str, str] = {
    "schedule": "lo agende",
    "reminders": "te lo recuerde",
    "fitness": "lo registre en fitness",
    "nutrition": "lo registre en nutrición",
    "knowledge_graph": "lo guarde en tu grafo de conocimiento",
    "file_ops": "trabaje con ese archivo",
    "git_ops": "lo maneje con git",
}


# Palabras que el usuario suele usar para referirse a cada automation_intent,
# para mapear su respuesta a la clarificación ("música" → PLAY_MEDIA).
_AUTOMATION_KEYWORDS: dict[str, list[str]] = {
    "NAVIGATE": ["abrir", "abre", "abrí", "navegador", "página", "web", "sitio"],
    "PLAY_MEDIA": ["música", "musica", "reproducir", "reproduce", "poné", "pon",
                   "canción", "cancion", "tema", "álbum", "album", "video"],
    "SEARCH": ["buscar", "búsqueda", "busqueda", "web", "internet", "google"],
    "INTERACT": ["click", "clic", "escribir", "página"],
    "PLAYBACK_CONTROL": ["pausa", "pausar", "siguiente", "anterior", "silenciar"],
    "BROWSE_AND_EXTRACT": ["leer", "extraer", "página"],
    "MULTI_ACTION": ["acciones"],
}


@dataclass
class Candidate:
    """Una interpretación posible del input, con su confianza."""
    kind: str                       # "skill" | "router"
    score: float
    phrase: str                     # frase de acción legible ("lo agende")
    payload: Any = None             # skill object o analysis dict para ejecutar
    label: str = ""                 # nombre corto ("schedule", "PLAY_MEDIA")
    keywords: list[str] = field(default_factory=list)  # para mapear respuestas


@dataclass
class GateDecision:
    action: str                                  # "resolve" | "clarify" | "chat"
    chosen: Optional[Candidate] = None           # set si action == "resolve"
    options: list[Candidate] = field(default_factory=list)  # set si action == "clarify"
    question: Optional[str] = None               # texto a mostrar si action == "clarify"


def looks_like_command(user_input: str, candidates: list[Candidate]) -> bool:
    """
    Heurística "parece comando": hay un verbo de acción o algún candidato con
    score positivo. Si es charla casual sin candidatos, devuelve False y el
    gate NO preguntará (anti-fricción).
    """
    if any(c.score > 0 for c in candidates):
        return True
    low = user_input.lower()
    return any(re.search(rf"\b{re.escape(v)}\b", low) for v in AUTO_VERBS)


def decide(
    user_input: str,
    candidates: list[Candidate],
    *,
    high: float = CLARIFY_HIGH,
    low: float = CLARIFY_LOW,
    margin: float = AMBIGUITY_MARGIN,
) -> GateDecision:
    """
    Decide la acción para una sub-tarea dada su lista de candidatos.

    ``candidates`` no necesita venir ordenado; se ordena acá por score desc.
    """
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)

    if not ranked:
        # Sin ninguna interpretación: chat genérico.
        return GateDecision(action="chat")

    top = ranked[0]
    rivals_in_margin = [c for c in ranked[1:] if (top.score - c.score) <= margin]

    # Ambigüedad real: 2+ candidatos empatados dentro del margen.
    if rivals_in_margin:
        opts = [top] + rivals_in_margin
        return GateDecision(
            action="clarify",
            options=opts,
            question=_build_question(opts),
        )

    # Top dominante y suficientemente alto → ejecutar.
    if top.score >= high:
        return GateDecision(action="resolve", chosen=top)

    cmd_like = looks_like_command(user_input, ranked)

    # Zona media con pinta de comando → confirmar interpretación única.
    if top.score >= low:
        if cmd_like:
            return GateDecision(
                action="clarify",
                options=[top],
                question=_build_question([top]),
            )
        return GateDecision(action="resolve", chosen=top)

    # Todo bajo el piso. Si parece comando, preguntamos; si es charla, chat.
    if cmd_like:
        return GateDecision(
            action="clarify",
            options=ranked[: max(2, len(rivals_in_margin) + 1)],
            question=_build_question(ranked[:2]),
        )
    return GateDecision(action="chat")


def _build_question(options: list[Candidate]) -> str:
    """Arma una pregunta natural a partir de las frases de los candidatos."""
    phrases = [o.phrase for o in options if o.phrase]
    if not phrases:
        return "No estoy seguro de qué querés que haga. ¿Me lo aclarás?"
    if len(phrases) == 1:
        return f"¿Querés que {phrases[0]}?"
    if len(phrases) == 2:
        return f"¿Querés que {phrases[0]} o que {phrases[1]}?"
    head = ", ".join(phrases[:-1])
    return f"¿Querés que {head}, o que {phrases[-1]}?"


# ── Helpers de construcción de candidatos (usados por el orquestador) ──────

def skill_phrase(skill: Any) -> str:
    """Frase natural para una skill: del map conocido o de su descripción."""
    name = getattr(skill, "name", "") or ""
    if name in _SKILL_PHRASE:
        return _SKILL_PHRASE[name]
    desc = (getattr(skill, "description", "") or "").strip()
    if desc:
        first = desc.split(".")[0].strip().lower()
        return f"use {name} ({first})" if name else first
    return f"use la skill {name}" if name else "lo maneje con una skill"


def router_phrase(analysis: dict) -> str:
    """Frase natural para un análisis de automation del router."""
    ai = analysis.get("automation_intent")
    if ai in _AUTOMATION_PHRASE:
        target = analysis.get("target")
        base = _AUTOMATION_PHRASE[ai]
        if target and ai in ("NAVIGATE", "PLAY_MEDIA", "SEARCH"):
            return f"{base} ({target})"
        return base
    intent = analysis.get("intent", "")
    if intent in ("search", "web_search"):
        return "lo busque en la web"
    if intent == "vision":
        return "analice lo que ves en pantalla"
    return "ejecute esa acción"


def make_skill_candidate(skill: Any, score: float) -> Candidate:
    """Factory de candidato-skill con keywords derivadas del nombre/frase."""
    name = getattr(skill, "name", "") or ""
    phrase = skill_phrase(skill)
    kws = [name] + re.findall(r"\w+", phrase.lower())
    return Candidate(kind="skill", score=score, phrase=phrase,
                     payload=skill, label=name, keywords=kws)


def make_router_candidate(analysis: dict, score: float) -> Candidate:
    """Factory de candidato-router con keywords del automation_intent."""
    ai = analysis.get("automation_intent") or ""
    label = ai or analysis.get("intent", "")
    phrase = router_phrase(analysis)
    kws = list(_AUTOMATION_KEYWORDS.get(ai, []))
    target = analysis.get("target")
    if target:
        kws.append(str(target).lower())
    kws += re.findall(r"\w+", phrase.lower())
    return Candidate(kind="router", score=score, phrase=phrase,
                     payload=analysis, label=label, keywords=kws)
