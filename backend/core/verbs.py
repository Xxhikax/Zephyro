# -*- coding: utf-8 -*-
"""
verbs — Fuente ÚNICA de verbos de acción/comando para Zephyro.

Antes había ~24 listas de verbos dispersas (router, orchestrator, intent_detector,
skills, decomposer), cada una con cobertura de voseo desigual → "whack-a-mole":
agregar un verbo o una forma voseo requería tocar N listas y siempre se olvidaba
alguna. Este módulo centraliza los verbos por DOMINIO de uso; cada consumidor
deriva su lista/regex de acá. Agregar un verbo (o su forma voseo) se hace UNA vez.

Dos estructuras coherentes:
  - DOMAIN_VERBS: dominio → todas las formas (tuteo, voseo, subjuntivo, inglés,
    con clíticos). Para "¿es verbo de acción?", regex de detección, keywords.
  - VOSEO_TO_TUTEO: forma voseo → forma destino (la que matchea triggers/regex
    en tuteo). Para normalizar el input del usuario antes de comparar.

Un test (test_verbs.py) verifica que ambas no divergen.
"""
from __future__ import annotations

import re
import unicodedata

# ── Verbos por dominio de uso ──────────────────────────────────────────────
# Incluye tuteo imperativo, voseo imperativo, subjuntivo, infinitivo, inglés y
# formas con clítico soldado. Las claves de dominio agrupan por intención.
DOMAIN_VERBS: dict[str, set[str]] = {
    # Abrir/cerrar/lanzar apps y browser
    "automation": {
        "abre", "abres", "abren", "abra", "abras", "abran", "abrir", "abrirme",
        "ábreme", "ábrelo", "abrí", "abrime", "open",
        # "cerra"/"cerrame" sin tilde: el STT y el tipeo rápido la pierden, y
        # sin la forma plana "cerra word" no ruteaba como comando (medido el
        # 2026-10-02). "matá/mata/kill" faltaban enteros, así que "matá chrome"
        # tampoco ruteaba.
        "cierra", "cierres", "cierren", "cerrar", "cerrá", "cerra", "close",
        "cerrame", "cerrámelo", "cerralo", "ciérralo", "ciérrame",
        "mata", "matá", "matalo", "mátalo", "matar", "kill",
        "finaliza", "finalizá", "finalizar",
        "lanza", "lances", "lance", "lancen", "lánzalo", "lanzar", "lanzá", "launch",
        "ejecuta", "ejecutes", "ejecute", "ejecuten", "ejecutar", "ejecutá", "run",
        "inicia", "inicies", "inicie", "inicien", "iniciar", "iniciá",
        "arranca", "arrancá", "start",
        "activa", "actives", "active", "activen", "activá",
        "termina", "termines", "terminá", "actualiza", "actualizá", "update",
    },
    # Reproducir media (música/video)
    "media": {
        "reproduce", "reproducir", "reprodúceme", "reproduceme", "reproducí",
        "reproducime", "produce", "producir", "play",
        "pon", "ponme", "ponelo", "ponela", "ponémela", "poner", "poné",
        "toca", "tocá", "escucha", "escuchá", "mira", "mirá",
    },
    # Buscar / investigar
    "search": {
        "busca", "buscar", "busque", "busques", "busquen", "búscame", "buscame",
        "buscá", "search", "find",
        "encuentra", "encontrar", "encontrá",
        "investiga", "investigar", "investigá", "averigua", "averiguá",
    },
    # Dominio skills: agenda / notas / recordatorios
    "schedule": {
        "agenda", "agéndame", "agendame", "agendar", "agendes", "agende",
        "agenden", "agendá",
        "anota", "anótame", "anotame", "anotes", "anote", "anotá",
        "apunta", "apúntame", "apuntá",
        "agrega", "agréguame", "agregá", "añade", "añadí",
        "recuerda", "recuérdame", "recordá", "recordame",
        "programa", "programame", "programes", "programá",
        "reprograma", "reprográmame", "reprogramá",
        "registra", "regístrame", "registrá",
        "crea", "crear", "creá", "creame",
        "mueve", "mover", "mové", "cancela", "cancelar", "cancelá",
        "manda", "mandar", "mandá", "mandame",
    },
    # Navegación web (frases compuestas se mantienen en su lista local)
    "navigation": {
        "navega", "navegar", "navegá", "entra", "entrá", "visita", "visitá",
    },
    # Filesystem / código
    "fs": {
        "lee", "leer", "leé", "read",
        "escribe", "escribir", "escribí", "write",
        "instala", "instalar", "instalá", "install",
        "descarga", "descargar", "descargá", "download",
        "clona", "clonar", "cloná",
    },
    # Misceláneos de acción
    "misc": {
        "muestra", "muéstrame", "mostrame", "mostrá", "show",
        "verifica", "verificar", "verificá",
        "revisa", "revisar", "revisá",
        "analiza", "analizar", "analizá",
        "explica", "explicar", "explicá",
        "configura", "configure", "configurá",
        "envía", "envia", "enviá", "send",
        "guarda", "guardar", "guardá",
        "tomá", "comé",
    },
}

# ── Mapa voseo → forma destino (tuteo / con clítico que matchea los triggers) ──
# La forma destino es la que aparece en triggers de skills y regex tuteo. P.ej.
# el trigger de reminders es "recuérdame ", así que "recordame" → "recuérdame".
VOSEO_TO_TUTEO: dict[str, str] = {
    # automation
    "abrí": "abre", "abrime": "ábreme", "cerrá": "cierra", "lanzá": "lanza",
    # Formas sin tilde: el STT y el tipeo rápido las pierden.
    "cerra": "cierra", "cerrame": "ciérrame", "cerralo": "ciérralo",
    "matá": "mata", "matalo": "mátalo", "finalizá": "finaliza",
    "ejecutá": "ejecuta", "iniciá": "inicia", "arrancá": "arranca",
    "activá": "activa", "terminá": "termina", "actualizá": "actualiza",
    # media
    "poné": "pon", "ponémela": "ponme", "reproducí": "reproduce",
    "reproducime": "reproduceme", "tocá": "toca", "escuchá": "escucha",
    "mirá": "mira",
    # search
    "buscá": "busca", "buscame": "búscame", "encontrá": "encuentra",
    "investigá": "investiga", "averiguá": "averigua",
    # schedule
    "agendá": "agenda", "anotá": "anota", "anotame": "anótame",
    "apuntá": "apunta", "agregá": "agrega", "añadí": "añade",
    "recordá": "recuerda", "recordame": "recuérdame", "programá": "programa",
    "reprogramá": "reprograma", "reprográmame": "reprográmame",
    "registrá": "registra", "creá": "crea", "creame": "créame",
    "mové": "mueve", "cancelá": "cancela", "mandá": "manda", "mandame": "mándame",
    # navigation
    "navegá": "navega", "entrá": "entra", "visitá": "visita",
    # fs
    "leé": "lee", "escribí": "escribe", "instalá": "instala",
    "descargá": "descarga", "cloná": "clona",
    # misc
    "mostrá": "muestra", "verificá": "verifica", "revisá": "revisa",
    "analizá": "analiza", "explicá": "explica", "configurá": "configura",
    "enviá": "envía", "guardá": "guarda", "pasalo": "pásalo", "pasá": "pasa",
    "hacé": "haz", "haceme": "hazme", "tomá": "toma", "comé": "come",
}


# ── Derivados (cada consumidor usa estos) ──────────────────────────────────

def verbs_for(*domains: str, include_voseo: bool = True) -> set[str]:
    """Devuelve el set de formas verbales de los dominios dados."""
    out: set[str] = set()
    for d in domains:
        out |= DOMAIN_VERBS.get(d, set())
    if not include_voseo:
        out -= set(VOSEO_TO_TUTEO)
    return out


def all_action_verbs() -> set[str]:
    """Todas las formas de todos los dominios."""
    out: set[str] = set()
    for forms in DOMAIN_VERBS.values():
        out |= forms
    return out


_ALL = all_action_verbs()


def is_action_verb(word: str) -> bool:
    """True si la palabra (normalizada NFC, lower) es un verbo de acción."""
    return unicodedata.normalize("NFC", word.lower().strip()) in _ALL


def verbs_pattern(*domains: str) -> str:
    """Regex no-capturador `(?:...)` con las formas de los dominios dados,
    ordenadas por longitud desc para greedy match. Sin anclas \\b (el consumidor
    las agrega según necesite)."""
    forms = verbs_for(*domains) if domains else all_action_verbs()
    ordered = sorted((re.escape(v) for v in forms), key=len, reverse=True)
    return "(?:" + "|".join(ordered) + ")"


_VOSEO_RE = re.compile(
    r"\b(" + "|".join(re.escape(v) for v in VOSEO_TO_TUTEO) + r")\b",
    re.IGNORECASE,
)


def normalize_voseo(text: str) -> str:
    """Reemplaza formas voseo imperativas por su equivalente destino, para que
    el matching en tuteo reconozca el habla rioplatense. Normaliza a NFC primero
    (los acentos pueden venir precompuestos o descompuestos según la fuente)."""
    if not text:
        return text
    text = unicodedata.normalize("NFC", text)
    return _VOSEO_RE.sub(lambda m: VOSEO_TO_TUTEO[m.group(1).lower()], text)


# ── Pedidos "table-worthy": comparaciones/organización de datos ─────────────
# Compartido por orchestrator (rich_hint → fuerza cloud + prompt de tabla) y
# router CAPA 2 (guard: un pedido de comparación JAMÁS es automation aunque
# Haiku lo clasifique 'command' por ser imperativo).
TABLE_WORTHY_PATTERN = re.compile(
    r'\b(compar[aá](me|la|lo|los|las)?|comparaci[oó]n|diferencias?\s+entre|versus|vs\.?|'
    r'ventajas\s+y\s+desventajas|pros\s+y\s+contras|cu[aá]l\s+(es\s+mejor|conviene|elegir)|'
    r'(en\s+una\s+)?tabla(\s+(comparativa|de|con))?)\b',
    re.IGNORECASE,
)
