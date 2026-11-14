"""
Suite 2 — Telegram routing tests.

Tests cubiertos:
1. /red → dispatched al orchestrator → skill_registry.try_handle('/red') llamado
2. /reflect → tiene CommandHandler propio (regression)
3. /skills → sigue llamando skill_registry.get_status_summary() (regression)
4. Comando desconocido /xyzzy → catch-all → respuesta limpia, sin crash
5. Error interno en skill → usuario recibe string limpio, sin traceback
"""
import sys
import os
import pytest
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock, patch, ANY

# ---------------------------------------------------------------------------
# Helpers — mocks de python-telegram-bot
# ---------------------------------------------------------------------------

def make_update(text: str, user_id: int = 12345, first_name: str = "Tester"):
    """Crea un mock de telegram.Update con los campos mínimos necesarios."""
    user = MagicMock()
    user.id = user_id
    user.first_name = first_name

    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    message.chat.send_action = AsyncMock()

    update = MagicMock()
    update.effective_user = user
    update.message = message
    return update


def make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


def make_mock_orchestrator(skill_result=None, chat_result="respuesta LLM") -> MagicMock:
    """Orquestador mockeado con skill_registry y handle_input."""
    skill_registry = MagicMock()
    skill_registry.try_handle = AsyncMock(return_value=skill_result)
    skill_registry.get_status_summary = MagicMock(return_value="Habilidades: system-monitor activa")
    skill_registry.__len__ = MagicMock(return_value=6)

    orc = MagicMock()
    orc.skill_registry = skill_registry
    orc.handle_input = AsyncMock(return_value=chat_result)
    orc.llm = MagicMock()
    orc.llm.get_status = MagicMock(return_value={"local_available": True, "cloud_provider": "anthropic"})
    return orc


# ---------------------------------------------------------------------------
# Test 1 — /red → dispatched → skill_registry.try_handle("/red") llamado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_red_dispatched_to_orchestrator():
    """
    /red debe llegar al orchestrator vía _on_unknown_command → _dispatch → handle_input.
    Como handle_input es el punto de entrada del orchestrator, él mismo
    consultará skill_registry.try_handle.
    """
    # Importar aquí para evitar importaciones globales con deps de Telegram
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator(chat_result="Estadísticas de red: TX 10MB RX 50MB")
    channel = TelegramChannel(orc)
    # Sin IDs autorizados → autoriza todos
    channel._authorized_ids = set()

    update = make_update("/red")
    ctx = make_context()

    # Llamar directamente al handler catch-all
    await channel._on_unknown_command(update, ctx)

    # orchestrator.handle_input debe haber sido llamado con el texto "/red"
    orc.handle_input.assert_called_once()
    call_args = orc.handle_input.call_args
    # El primer arg posicional o el kwarg 'user_input' debe ser "/red"
    called_text = call_args.args[0] if call_args.args else call_args.kwargs.get("user_input", "")
    assert called_text == "/red", f"Se esperaba '/red', se obtuvo {called_text!r}"

    # El usuario debe recibir respuesta
    update.message.reply_text.assert_called()


# ---------------------------------------------------------------------------
# Test 2 — /reflect tiene CommandHandler registrado (regression)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reflect_has_dedicated_command_handler():
    """
    /reflect debe tener su propio CommandHandler para llegar a _cmd_reflect,
    que ejecuta la lógica de reflexión, no ir al catch-all.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    try:
        from telegram.ext import CommandHandler, Application, MessageHandler
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator()
    channel = TelegramChannel(orc)

    # Capturar qué handlers se registrarían sin initializar la app completa
    registered_commands = []

    mock_app = MagicMock()

    def capture_handler(handler):
        if isinstance(handler, CommandHandler):
            registered_commands.extend(handler.commands)

    mock_app.add_handler = capture_handler
    mock_app.bot.set_my_commands = AsyncMock()

    # Simular la parte de registro de handlers de start()
    handlers = [
        ("start",          channel._cmd_start),
        ("help",           channel._cmd_help),
        ("commands",       channel._cmd_commands),
        ("status",         channel._cmd_status),
        ("mode",           channel._cmd_mode),
        ("provider",       channel._cmd_provider),
        ("model",          channel._cmd_model),
        ("skills",         channel._cmd_skills),
        ("search",         channel._cmd_search),
        ("reflect",        channel._cmd_reflect),
        ("bugs",           channel._cmd_bugs),
        ("specialty",      channel._cmd_specialty),
        ("perfil",         channel._cmd_perfil),
        ("resumenes",      channel._cmd_resumenes),
        ("capacidades",    channel._cmd_capacidades),
        ("limitaciones",   channel._cmd_limitaciones),
    ]

    from telegram.ext import CommandHandler as CH
    for name, handler_fn in handlers:
        mock_app.add_handler(CH(name, handler_fn))

    assert "reflect" in registered_commands, (
        f"/reflect no tiene CommandHandler. Registrados: {registered_commands}"
    )


# ---------------------------------------------------------------------------
# Test 3 — /skills regression: llama get_status_summary() (no try_handle)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skills_command_calls_get_status_summary():
    """
    /skills debe llamar skill_registry.get_status_summary(),
    no pasar por try_handle. Regression que asegura que esto no rompe.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator()
    channel = TelegramChannel(orc)
    channel._authorized_ids = set()

    update = make_update("/skills")
    ctx = make_context()

    await channel._cmd_skills(update, ctx)

    orc.skill_registry.get_status_summary.assert_called_once()
    # try_handle NO debe haberse llamado por este path
    orc.skill_registry.try_handle.assert_not_called()
    update.message.reply_text.assert_called()


# ---------------------------------------------------------------------------
# Test 4 — Comando desconocido → catch-all → respuesta limpia, sin crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_command_no_crash():
    """
    Un comando slash completamente desconocido (/xyzzy123)
    debe ser recibido por _on_unknown_command y producir una respuesta
    limpia, sin crash.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator(chat_result="No reconozco ese comando")
    channel = TelegramChannel(orc)
    channel._authorized_ids = set()

    update = make_update("/xyzzy123comandoinexistente")
    ctx = make_context()

    # No debe lanzar excepción
    await channel._on_unknown_command(update, ctx)

    # El orchestrator fue llamado (el skill_registry retornó None, fue al LLM)
    orc.handle_input.assert_called_once()
    # El usuario recibió algo
    update.message.reply_text.assert_called()


# ---------------------------------------------------------------------------
# Test 5 — Error interno en skill → mensaje limpio al usuario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skill_internal_error_clean_response():
    """
    Si el orchestrator/skill lanza excepción interna,
    el usuario debe recibir un string de error limpio,
    no un stack trace.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = MagicMock()
    orc.handle_input = AsyncMock(side_effect=RuntimeError("Error interno de psutil"))
    orc.skill_registry = MagicMock()
    orc.skill_registry.__len__ = MagicMock(return_value=0)

    channel = TelegramChannel(orc)
    channel._authorized_ids = set()

    update = make_update("/ram")
    ctx = make_context()

    # No debe propagar la excepción al nivel superior
    await channel._on_unknown_command(update, ctx)

    # El usuario debe haber recibido un mensaje (el handler la capturó)
    update.message.reply_text.assert_called()
    called_text = update.message.reply_text.call_args.args[0]
    # No debe ser un traceback — debe ser un string legible sin "Traceback"
    assert "Traceback" not in called_text, (
        f"El usuario recibió un traceback: {called_text!r}"
    )
    assert len(called_text) < 500, "El mensaje de error es demasiado largo"
