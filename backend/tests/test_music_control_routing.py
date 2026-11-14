"""
Tests del control de reproducción (pausa/reanuda) y de no abrir basura como app.

Bug origen (sesión call-mode 2026-06-12): con música sonando, la STT escuchaba
"pausa" como "pase/posa/vamos a la música". Esas frases:
  - no se reconocían como PLAYBACK_CONTROL pause,
  - caían a CAPA 1 y app_launcher las "abría" como app vía PowerShell
    ("He abierto música", "He abierto stop music"),
  - y el pause real devolvía "Reproducción alternada" (ni dice qué hizo, y como
    era un toggle, decir "pausa" dos veces reanudaba).

Fixes verificados acá:
  1. Variantes STT de "pausa la música" → PLAYBACK_CONTROL pause.
  2. pause/resume determinísticos con mensajes claros.
  3. app_launcher no abre strings basura como app.
  4. "reproduce el album" (sin nombre) → no PLAY_MEDIA basura.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── 1. Variantes STT de pausa en el router ────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", [
    "pausa la música",
    "pausa la musica",
    "pause la música",
    "posa la música",
    "pausá la música",
    "pauza la canción",
    "pase la música",
])
async def test_pause_variants_route_to_playback_pause(phrase):
    from core.router import IntentRouter

    router = IntentRouter(MagicMock(), context_manager=None)
    r = await router.classify_and_parse(phrase)
    assert r.get("automation_intent") == "PLAYBACK_CONTROL", f"{phrase!r} -> {r}"
    assert r.get("control_action") == "pause", f"{phrase!r} -> {r}"


@pytest.mark.asyncio
async def test_next_command_not_swallowed_by_pause():
    """Regresión: 'pasa a la siguiente canción' sigue siendo next, no pause."""
    from core.router import IntentRouter

    router = IntentRouter(MagicMock(), context_manager=None)
    r = await router.classify_and_parse("pasa a la siguiente canción")
    assert r.get("automation_intent") == "PLAYBACK_CONTROL"
    assert r.get("control_action") == "next"


@pytest.mark.asyncio
async def test_resume_command_routes_to_resume():
    from core.router import IntentRouter

    router = IntentRouter(MagicMock(), context_manager=None)
    r = await router.classify_and_parse("vuelve a reproducir la música")
    assert r.get("automation_intent") == "PLAYBACK_CONTROL"
    assert r.get("control_action") == "resume"


# ── 1.b Un verbo de reproducción necesita objeto de media ─────────────────────
# Bug real (2026-09-30): consultando notas de Canvas, "En la evaluacion continua 1"
# disparó resume y el asistente contestó "Reproduciendo música" — además de tipearle
# una 'k' a la página abierta. La causa: `contin[uú]a[rs]?` sin ancla al objeto, y
# CAPA 0.6 hace `return` incondicional ~370 líneas antes de los guards de CAPA 1.
# Mismo defecto tenían `pausa[rs]?` y `silencia[rs]?`.

@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", [
    "En la evaluacion continua 1",
    "la nota de la evaluacion continua",
    "cuanto me falta en la evaluacion continua 2",
    "las evaluaciones continuas de canvas",
    "continua con el ejercicio",
    "continuá con el ejercicio",
    "quiero continuar con el tp",
    "seguí con la tarea 2",
    "reanudar el tramite",
    "pausa para pensar",
    "silencia esa duda",
])
async def test_playback_verbs_without_media_object_are_not_commands(phrase):
    from core.router import IntentRouter

    router = IntentRouter(MagicMock(), context_manager=None)
    r = await router.classify_and_parse(phrase)
    assert r.get("automation_intent") != "PLAYBACK_CONTROL", (
        f"{phrase!r} no es un comando de música: {r.get('control_action')}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase,action", [
    ("pausa la musica", "pause"),
    ("pausa el video", "pause"),
    ("para la musica", "pause"),
    ("ponla en pausa", "pause"),
    ("continua la musica", "resume"),
    ("continúa la música", "resume"),
    ("reanuda la reproduccion", "resume"),
    ("vuelve a reproducir", "resume"),
    ("silencia la musica", "mute"),
    # Voseo rioplatense, que es como habla el usuario
    ("pausá la música", "pause"),
    ("continuá la música", "resume"),
    ("seguí la música", "resume"),
    ("reanudá la música", "resume"),
    # Verbo pelado: el mensaje ES el comando
    ("pausa", "pause"),
    ("pausá", "pause"),
    ("continua", "resume"),
    ("silencia", "mute"),
    ("pausá por favor", "pause"),
])
async def test_real_playback_commands_still_route(phrase, action):
    from core.router import IntentRouter

    router = IntentRouter(MagicMock(), context_manager=None)
    r = await router.classify_and_parse(phrase)
    assert r.get("automation_intent") == "PLAYBACK_CONTROL", f"{phrase!r} dejó de ser comando"
    assert r.get("control_action") == action


# ── 2. pause/resume determinístico (browser_controller) ───────────────────────

def _make_controller():
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig
    return BrowserController(BrowserConfig())


@pytest.mark.asyncio
async def test_pause_returns_clear_message():
    bc = _make_controller()
    bc.page = MagicMock()
    bc.page.evaluate = AsyncMock(return_value="paused")
    msg = await bc._pause_media()
    assert msg == "Música pausada"
    # Pidió pausar de forma determinística (no toggle)
    assert bc.page.evaluate.await_args.args[1] is True


@pytest.mark.asyncio
async def test_resume_returns_clear_message():
    bc = _make_controller()
    bc.page = MagicMock()
    bc.page.evaluate = AsyncMock(return_value="playing")
    msg = await bc._resume_media()
    assert msg == "Reproduciendo música"
    assert bc.page.evaluate.await_args.args[1] is False


@pytest.mark.asyncio
async def test_pause_no_page_is_honest():
    bc = _make_controller()
    bc.page = None
    assert await bc._pause_media() == "No hay reproducción activa"


@pytest.mark.asyncio
async def test_pause_no_video_falls_back_to_keyboard():
    bc = _make_controller()
    bc.page = MagicMock()
    bc.page.evaluate = AsyncMock(return_value="no_video")
    bc.page.keyboard = MagicMock()
    bc.page.keyboard.press = AsyncMock()
    msg = await bc._pause_media()
    bc.page.keyboard.press.assert_awaited_once_with("k")
    assert msg == "Música pausada"


# ── 3. Dispatch pause/resume en AppController ─────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_pause_and_resume_use_distinct_methods():
    from modules.automation.app_controller import AppController

    ac = AppController.__new__(AppController)
    ac._browser = MagicMock()
    ac._browser.pause_music = AsyncMock(return_value="Música pausada")
    ac._browser.resume_music = AsyncMock(return_value="Reproduciendo música")

    pause_res = await ac._execute_inner(
        {"automation_intent": "PLAYBACK_CONTROL", "control_action": "pause"}
    )
    resume_res = await ac._execute_inner(
        {"automation_intent": "PLAYBACK_CONTROL", "control_action": "resume"}
    )

    ac._browser.pause_music.assert_awaited_once()
    ac._browser.resume_music.assert_awaited_once()
    assert pause_res == "Música pausada"
    assert resume_res == "Reproduciendo música"


# ── 4. app_launcher no abre basura como app ───────────────────────────────────

@pytest.mark.parametrize("junk", [
    "música",
    "musica",
    "reproducir música",
    "stop music",
    "pausa",
])
def test_open_app_rejects_non_app_strings(junk):
    from modules.automation.app_launcher import AppLauncher

    launcher = AppLauncher()
    result = launcher.open_app(junk)
    assert result.startswith("✗ No reconozco"), f"{junk!r} -> {result!r}"


# ── 5. "reproduce el album" (sin nombre) no genera PLAY_MEDIA basura ──────────

@pytest.mark.parametrize("phrase", [
    "reproduce el album",
    "abre youtube music y reproduce el album",
    "pon una cancion",
])
def test_empty_album_content_returns_none(phrase):
    from core.router import _extract_media_entities
    assert _extract_media_entities(phrase) is None


def test_named_album_still_works_after_empty_guard():
    from core.router import _extract_media_entities
    r = _extract_media_entities("reproduce el album el camino de the black keys")
    assert r is not None
    assert r["album"] == "el camino" and r["artist"] == "the black keys"


def test_real_song_still_works_after_empty_guard():
    from core.router import _extract_media_entities
    r = _extract_media_entities("reproduce despacito")
    assert r is not None
    assert r["search_query"] == "despacito"
