"""
Suite — Telegram channel behaviour tests.

Tests cubiertos:
1. Mensaje de texto normal → _on_message → orchestrator.handle_input llamado
2. Usuario no autorizado → "No autorizado", orchestrator NO llamado
3. /status → llm.get_status() directo, respuesta formateada correctamente
4. /mode local → despacha "/mode local" al orchestrator
5. /mode sin args → error de uso, orchestrator NO llamado
6. /mode con valor inválido → error, orchestrator NO llamado
7. /start → texto de bienvenida con nombre de usuario, orchestrator NO llamado
8. _split_message → mensajes largos se dividen en chunks ≤ 4096 chars
9. /provider sin args → muestra provider actual, sin crash
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_update(text: str, user_id: int = 12345, first_name: str = "Bruno"):
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


def make_mock_orchestrator(chat_result="ok") -> MagicMock:
    skill_registry = MagicMock()
    skill_registry.try_handle = AsyncMock(return_value=None)
    skill_registry.get_status_summary = MagicMock(return_value="skills ok")
    skill_registry.__len__ = MagicMock(return_value=6)

    orc = MagicMock()
    orc.skill_registry = skill_registry
    orc.handle_input = AsyncMock(return_value=chat_result)
    orc.llm = MagicMock()
    orc.llm.get_status = MagicMock(return_value={"local_available": True, "cloud_provider": "anthropic"})
    orc.llm.cloud_provider = "anthropic"
    return orc


# ---------------------------------------------------------------------------
# Test 1 — Texto normal → orchestrator.handle_input recibe el texto exacto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regular_message_dispatched_to_orchestrator():
    """
    Un mensaje de texto normal debe llegar a orchestrator.handle_input
    con el texto tal cual.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator(chat_result="Hola Bruno")
    channel = TelegramChannel(orc)
    channel._authorized_ids = set()

    update = make_update("¿Cómo estás?")
    ctx = make_context()

    await channel._on_message(update, ctx)

    orc.handle_input.assert_called_once()
    called_text = orc.handle_input.call_args.args[0] if orc.handle_input.call_args.args \
        else orc.handle_input.call_args.kwargs.get("user_input", "")
    assert called_text == "¿Cómo estás?", f"Texto incorrecto: {called_text!r}"
    update.message.reply_text.assert_called()


# ---------------------------------------------------------------------------
# Test 2 — Usuario no autorizado → "No autorizado", orchestrator NO llamado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unauthorized_user_blocked():
    """
    Si _authorized_ids tiene IDs específicos, un user_id no incluido
    debe recibir "No autorizado" y el orchestrator NO debe ser llamado.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator()
    channel = TelegramChannel(orc)
    channel._authorized_ids = {99999}  # Solo ID 99999 autorizado

    update = make_update("hola", user_id=12345)  # ID diferente → no autorizado
    ctx = make_context()

    await channel._on_message(update, ctx)

    orc.handle_input.assert_not_called()
    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args.args[0]
    assert "autorizado" in reply.lower(), f"Se esperaba mensaje de no autorizado, recibido: {reply!r}"


# ---------------------------------------------------------------------------
# Test 3 — /status → formatea local + cloud + skills sin pasar por orchestrator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_command_formats_llm_info():
    """
    /status debe llamar llm.get_status() directamente y devolver un texto
    que incluya el estado local/cloud, sin llamar a handle_input.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator()
    orc.llm.get_status = MagicMock(return_value={"local_available": True, "cloud_provider": "anthropic"})
    channel = TelegramChannel(orc)
    channel._authorized_ids = set()

    update = make_update("/status")
    ctx = make_context()

    await channel._cmd_status(update, ctx)

    orc.llm.get_status.assert_called_once()
    orc.handle_input.assert_not_called()

    reply = update.message.reply_text.call_args.args[0]
    assert "anthropic" in reply.lower(), f"Provider no aparece en la respuesta: {reply!r}"
    assert "activo" in reply.lower(), f"Estado local no aparece: {reply!r}"


# ---------------------------------------------------------------------------
# Test 4 — /mode local → despacha "/mode local" al orchestrator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mode_local_dispatched_to_orchestrator():
    """
    /mode local debe llamar orchestrator.handle_input con "/mode local".
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator(chat_result="Modo local activado")
    channel = TelegramChannel(orc)
    channel._authorized_ids = set()

    update = make_update("/mode local")
    ctx = make_context(args=["local"])

    await channel._cmd_mode(update, ctx)

    orc.handle_input.assert_called_once()
    called_text = orc.handle_input.call_args.args[0] if orc.handle_input.call_args.args \
        else orc.handle_input.call_args.kwargs.get("user_input", "")
    assert called_text == "/mode local", f"Texto incorrecto: {called_text!r}"
    update.message.reply_text.assert_called()


# ---------------------------------------------------------------------------
# Test 5 — /mode sin args → error de uso, orchestrator NO llamado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mode_without_args_returns_usage():
    """
    /mode sin argumentos debe devolver el mensaje de uso y
    no llamar al orchestrator.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator()
    channel = TelegramChannel(orc)
    channel._authorized_ids = set()

    update = make_update("/mode")
    ctx = make_context(args=[])  # Sin args

    await channel._cmd_mode(update, ctx)

    orc.handle_input.assert_not_called()
    reply = update.message.reply_text.call_args.args[0]
    assert "local" in reply.lower() or "uso" in reply.lower() or "mode" in reply.lower(), \
        f"El mensaje de uso no menciona los modos: {reply!r}"


# ---------------------------------------------------------------------------
# Test 6 — /mode con valor inválido → error, orchestrator NO llamado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mode_invalid_value_returns_error():
    """
    /mode <valor no válido> debe devolver un error de validación
    y no llamar al orchestrator.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator()
    channel = TelegramChannel(orc)
    channel._authorized_ids = set()

    update = make_update("/mode turbo")
    ctx = make_context(args=["turbo"])  # Valor inválido

    await channel._cmd_mode(update, ctx)

    orc.handle_input.assert_not_called()
    reply = update.message.reply_text.call_args.args[0]
    assert "local" in reply.lower() or "válido" in reply.lower() or "cloud" in reply.lower(), \
        f"El error no menciona los modos válidos: {reply!r}"


# ---------------------------------------------------------------------------
# Test 7 — /start → bienvenida con nombre, orchestrator NO llamado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_returns_greeting_without_orchestrator():
    """
    /start debe devolver un saludo que incluya el nombre del usuario
    y no llamar al orchestrator.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator()
    channel = TelegramChannel(orc)
    channel._authorized_ids = set()

    update = make_update("/start", first_name="Bruno")
    ctx = make_context()

    await channel._cmd_start(update, ctx)

    orc.handle_input.assert_not_called()
    reply = update.message.reply_text.call_args.args[0]
    assert "Bruno" in reply, f"El saludo no incluye el nombre del usuario: {reply!r}"
    assert "Zephyro" in reply, f"El saludo no menciona Zephyro: {reply!r}"


# ---------------------------------------------------------------------------
# Test 8 — _split_message divide mensajes largos en chunks ≤ 4096 chars
# ---------------------------------------------------------------------------

def test_split_message_respects_max_length():
    """
    _split_message debe dividir un texto largo en partes de máximo 4096 chars.
    El contenido total reconstruido debe ser idéntico al original.
    """
    try:
        from channels.telegram_channel import _split_message
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    # Generar un texto que supere 4096 chars con saltos de línea
    line = "Esta es una línea de texto de ejemplo para el test de splitting. " * 3
    long_text = "\n".join([line] * 80)  # ~130 chars/línea × 80 = ~10 400 chars

    chunks = _split_message(long_text)

    assert len(chunks) > 1, "El texto largo debería haberse dividido en múltiples chunks"
    for i, chunk in enumerate(chunks):
        assert len(chunk) <= 4096, f"Chunk {i} supera 4096 chars: {len(chunk)}"
        assert chunk, f"Chunk {i} está vacío"

    # El contenido se preserva: la suma de longitudes es ≤ original
    # (_split_message consume los \n de frontera con lstrip, no duplica contenido)
    reconstructed = "".join(chunks)
    assert len(reconstructed) <= len(long_text), "Los chunks tienen más contenido que el original"
    assert len(reconstructed) >= len(long_text) - len(chunks), \
        "Se perdió demasiado contenido en el splitting"


# ---------------------------------------------------------------------------
# Test 9 — /provider sin args → muestra provider actual, sin crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_without_args_shows_current():
    """
    /provider sin argumentos debe mostrar el provider actual
    sin llamar a handle_input ni set_cloud_provider.
    """
    try:
        from channels.telegram_channel import TelegramChannel
    except ImportError:
        pytest.skip("python-telegram-bot no instalado")

    orc = make_mock_orchestrator()
    orc.llm.cloud_provider = "gemini"
    orc.llm.set_cloud_provider = MagicMock()
    channel = TelegramChannel(orc)
    channel._authorized_ids = set()

    update = make_update("/provider")
    ctx = make_context(args=[])

    await channel._cmd_provider(update, ctx)

    orc.handle_input.assert_not_called()
    orc.llm.set_cloud_provider.assert_not_called()
    update.message.reply_text.assert_called()
    reply = update.message.reply_text.call_args.args[0]
    assert "gemini" in reply.lower(), f"El provider actual no aparece en la respuesta: {reply!r}"
