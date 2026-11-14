"""Vocabulario común de Bruno que NO debe disparar acciones.

Cubre la clase de bug que ya costó dos incidentes reales:

- "en la evaluación continua 1" → "Reproduciendo música" (fix ea22754), que
  además le tipeó una `k` a la pestaña de Canvas que estaba abierta.
- "abre discord" (ya abierto → camino de focus) → la PC entera dejó de
  responder a los clicks (fix 891a44a).

Las frases de acá salen de una auditoría del 2026-10-01 y están MEDIDAS: las
once primeras se verificaron fallando contra el router real antes del fix.
Bruno es estudiante de ingeniería, así que "código", "captura", "monitoreo",
"archivos" y "notas" son vocabulario de todos los días — que cada una abra un
programa hace al asistente imprevisible.

El hueco que dejó pasar todo esto: `test_auto_verbs.py` prueba el camino de
búsqueda con SUBJUNTIVOS (`busque`/`busques`/`busquen`), que no están en
`_MEDIA_ACTION_KEYWORDS`. El imperativo real, `busca`, nunca se testeó.
"""
import asyncio

import pytest

from core.router import IntentRouter


def _route(phrase: str) -> dict:
    return asyncio.run(IntentRouter(None).classify_and_parse(phrase, skip_llm=True))


# ── Verbos de búsqueda: NO son un pedido de música ──────────────────────
# El default viejo de "busca <cualquier cosa>" era YouTube Music: la regla
# pedía una señal POSITIVA de info web para NO tratarlo como música.
@pytest.mark.parametrize("phrase", [
    "busca el teorema de Bayes",
    "busca el enunciado del TP2",
    "buscame el apunte de estructuras",
    "buscá la formula de Shannon",
    "encuentra el error en el stacktrace",
    "busca mis notas de la ec1",
    "busca la fecha del parcial",
])
def test_search_verbs_are_not_music(phrase):
    a = _route(phrase)
    assert a.get("automation_intent") != "PLAY_MEDIA", (
        f"{phrase!r} reproduce música en vez de buscar"
    )


# ── "pon X en Y" es colocación, no playback ─────────────────────────────
@pytest.mark.parametrize("phrase", [
    "pon la fecha del parcial en el informe",
    "ponme el promedio en la planilla",
    "pon el resultado en la tabla",
])
def test_placement_is_not_music(phrase):
    assert _route(phrase).get("automation_intent") != "PLAY_MEDIA"


# ── Visión: un verbo pelado no es un pedido de captura ──────────────────
# `_RE_VISION_KW` era `\b(pantalla|viendo|ves|mira|analiza este|captura
# |screenshot)\b` con un `return` INCONDICIONAL. Para alguien que cursa
# redes, "captura" es vocabulario diario.
@pytest.mark.parametrize("phrase", [
    "ves por que me da error",
    "mira el ejercicio 3",
    "la captura de paquetes de Wireshark",
    "necesito la captura de la topologia",
    "estoy viendo la clase grabada",
    "no ves que el tipo no coincide",
    "mira si el test pasa",
])
def test_perception_verbs_without_screen_reference(phrase):
    assert _route(phrase).get("intent") not in ("vision", "screen_query"), (
        f"{phrase!r} dispara una captura de pantalla"
    )


# ── Colisiones por substring en las whitelists de apps ──────────────────
# El gate de `app_hit` ya usaba `\b`, pero el resolvedor del target usaba
# `app in user_input_lower` — substring puro. El portero y el resolvedor
# miraban universos distintos.
@pytest.mark.parametrize("phrase,must_not_open", [
    ("abre el password de la wifi", "word"),        # word ⊂ password
    ("activa el monitoreo de la red", "taskmgr"),   # monitor ⊂ monitoreo
    ("abre el knowledge graph de gitnexus", "edge"),  # edge ⊂ knowledge
    ("mostrame el bloque de codigo", "notepad"),    # bloc ⊂ bloque
])
def test_app_names_hidden_inside_spanish_words(phrase, must_not_open):
    a = _route(phrase)
    assert a.get("target") != must_not_open, (
        f"{phrase!r} abre {must_not_open}"
    )


# ── Aliases que son palabras españolas, usados como modificador ─────────
# "codigo"/"archivos"/"monitor"/"terminal" son entradas REALES de la
# whitelist. Seguidas de "de"/"del" modifican a otro sustantivo y no son la
# app que el usuario quiere abrir.
@pytest.mark.parametrize("phrase,must_not_open", [
    ("abre el codigo del tp2", "code"),
    ("abre los archivos del tp", "explorer"),
    ("activa el monitor de red", "taskmgr"),
    ("abre la terminal de pagos", "cmd"),
])
def test_generic_alias_followed_by_de_is_a_modifier(phrase, must_not_open):
    assert _route(phrase).get("target") != must_not_open


# ── Lo legítimo tiene que seguir funcionando ────────────────────────────
# Sin estos, los tests de arriba se "arreglan" rompiendo el asistente.
@pytest.mark.parametrize("phrase", [
    "busca Bad Bunny en youtube",
    "pon musica de los Redondos",
    "reproduce Bad Bunny",
    "poné un tema de Charly",
])
def test_real_music_requests_still_work(phrase):
    assert _route(phrase).get("automation_intent") == "PLAY_MEDIA", (
        f"{phrase!r} dejo de pedir música"
    )


@pytest.mark.parametrize("phrase", [
    "que ves en mi pantalla",
    "analiza esta captura de pantalla",
    "que hay en mi pantalla",
    "mira esto",
    "captura la pantalla",
])
def test_real_screen_requests_still_work(phrase):
    assert _route(phrase).get("intent") in ("vision", "screen_query"), (
        f"{phrase!r} dejo de mirar la pantalla"
    )


@pytest.mark.parametrize("phrase,target", [
    ("abre discord", "discord"),
    ("abre word", "word"),
    ("abre youtube", "youtube"),
    ("abre codigo", "code"),
    ("abre el bloc de notas", "notepad"),
    ("abre terminal", "cmd"),
    ("abre archivos", "explorer"),
    ("abre calculadora", "calc"),
])
def test_real_app_opens_still_work(phrase, target):
    a = _route(phrase)
    assert a.get("target") == target, f"{phrase!r} → {a.get('target')!r}"
