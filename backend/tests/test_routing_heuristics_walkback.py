"""Las tres heurísticas de routing de la Fase A eran demasiado gruesas.

Codex las encontró revisando el diff y las verifiqué midiendo. Cada una
arreglaba un falso positivo real y se llevaba puesto el caso legítimo:

    "toca Despacito"                        música → NADA
    "mira Bohemian Rhapsody"                música → NADA
    "abre la terminal de Windows"           cmd    → Edge en blanco
    "abre Word con el documento PowerPoint" word   → powerpoint

La lección: un test de falso positivo sin su contraparte legítima no vale
nada. Las tres regresiones pasaron porque la Fase A tenía lo primero y no lo
segundo para estos casos.
"""
import asyncio

import pytest

from core.app_config import NATIVE_APPS
from core.router import IntentRouter, _extract_media_entities, _resolve_app_target


def _route(phrase):
    return asyncio.run(IntentRouter(None).classify_and_parse(phrase, skip_llm=True))


def _media(phrase):
    out = _extract_media_entities(phrase)
    return out.get("automation_intent") if out else None


# ── (a) Un titulo sin plataforma ni objeto de media sigue siendo musica ──
# La regla "verbo debil sin señal de media no es media" convertia *ambiguo*
# en *no es media*. Ningun regex puede saber que "Despacito" es una cancion,
# y un titulo nuevo no esta en ninguna lista: descartarlo era peor que el
# falso positivo que se queria evitar.
@pytest.mark.parametrize("phrase", [
    "toca Despacito", "tocá Despacito",
    "mira Bohemian Rhapsody", "mirá Bohemian Rhapsody",
    "escucha Bad Bunny", "reproduce Despacito",
    "pon musica de los Redondos",
])
def test_a_bare_title_is_still_a_music_request(phrase):
    assert _media(phrase) == "PLAY_MEDIA", f"{phrase!r} dejo de pedir musica"


# El override que reemplaza el descarte duro: un objeto de UI explicito gana.
@pytest.mark.parametrize("phrase", [
    "toca el boton de enviar",
    "toca en el enlace",
    "mira el campo de texto",
    "toca la casilla",
    "mira el menu desplegable",
    "toca la tecla enter",
])
def test_an_explicit_ui_object_is_never_music(phrase):
    assert _media(phrase) is None, f"{phrase!r} se fue a reproducir musica"


# Y los falsos positivos originales siguen cerrados.
@pytest.mark.parametrize("phrase", [
    "busca el teorema de Bayes",
    "buscame el apunte de estructuras",
    "pon la fecha del parcial en el informe",
    "encuentra el error en el stacktrace",
])
def test_the_original_false_positives_stay_closed(phrase):
    assert _media(phrase) is None


# ── (b) Alias COMPUESTOS: nombres de producto que contienen "de" ─────────
# La regla "alias generico + de/del = modificador" los descartaba. `de`
# introduce tanto contenido ("codigo DEL tp2") como un calificador de app
# ("terminal DE Windows"), asi que hace falta reconocer el compuesto primero.
@pytest.mark.parametrize("phrase,target", [
    ("abre la terminal de Windows", "wt"),
    ("abre el bloc de notas", "notepad"),
    ("abre visual studio code", "code"),
    ("abre el administrador de tareas", "taskmgr"),
    ("abre la linea de comandos", "cmd"),
    ("abre el editor de codigo", "code"),
    # Se prueba el resolvedor directo: CAPA 1 nav-subpath puede interceptar
    # una frase con forma "<algo> de <palabra>" antes de llegar acá.
    ("abre el explorador de archivos", "explorer"),
])
def test_compound_app_names_resolve(phrase, target):
    # `_resolve_app_target` recibe el texto YA en minusculas: asi lo llama
    # el router (el parametro se llama `user_input_lower`).
    assert _resolve_app_target(phrase.lower(), NATIVE_APPS) == target


# ── (c) El objeto directo del verbo, no el match mas largo de la frase ───
@pytest.mark.parametrize("phrase,target", [
    ("abre word con el documento powerpoint", "word"),
    ("abre chrome con una busqueda sobre powerpoint", "chrome"),
    ("abre discord para hablar con el equipo", "discord"),
    ("abre word", "word"),
    ("abre powerpoint", "powerpoint"),
])
def test_the_verbs_object_wins_over_the_longest_match(phrase, target):
    a = _route(phrase)
    assert a.get("target") == target, f"{phrase!r} -> {a.get('target')!r}"


# Y los hazards que motivaron la regla del modificador siguen cerrados.
@pytest.mark.parametrize("phrase,must_not", [
    ("abre el codigo del tp2", "code"),
    ("abre los archivos del tp", "explorer"),
    ("activa el monitor de red", "taskmgr"),
    ("abre el password de la wifi", "word"),
])
def test_generic_alias_as_modifier_stays_closed(phrase, must_not):
    assert _route(phrase).get("target") != must_not
