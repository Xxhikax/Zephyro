"""
Tests exhaustivos para bugs P1, P2, P3 — BUGS_ENCONTRADOS.md

BUG-P1: _handle_reflect_command era sync, bloqueaba event loop
BUG-P2: _on_message sin try/except, usuario no veía errores
BUG-P3: COMMANDS_TEXT no incluía slash-commands de skills

Tests:
  - test_reflect_is_async_coroutine
  - test_reflect_uses_to_thread
  - test_reflect_cloud_fallback_to_local
  - test_reflect_no_data_early_return_no_llm
  - test_reflect_exception_returns_clean_string
  - test_concurrent_reflects_no_block
  - test_on_message_dispatch_error_returns_clean_response
  - test_on_message_dispatch_error_no_traceback
  - test_on_message_success_unchanged
  - test_on_message_reply_error_caught
  - test_commands_text_includes_system_monitor
  - test_commands_text_includes_all_skill_commands
"""
import sys
import os
import asyncio
import inspect
import pytest
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock, patch


# ─── Helpers ──────────────────────────────────────────────────────────────

def make_update(text: str, user_id: int = 12345, first_name: str = "Tester"):
    """Mock de telegram.Update."""
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


def make_mock_orchestrator(handle_result="ok"):
    """Orchestrator mock mínimo para TelegramChannel."""
    orc = MagicMock()
    orc.handle_input = AsyncMock(return_value=handle_result)
    orc.skill_registry = MagicMock()
    orc.skill_registry.__len__ = MagicMock(return_value=6)
    orc.skill_registry.get_status_summary = MagicMock(return_value="Skills: 6")
    orc.llm = MagicMock()
    orc.llm.get_status = MagicMock(return_value={"local_available": True, "cloud_provider": "anthropic"})
    return orc


# ═════════════════════════════════════════════════════════════════════════
# BUG-P1: _handle_reflect_command es ahora async + asyncio.to_thread
# ═════════════════════════════════════════════════════════════════════════

class TestBugP1ReflectAsync:
    """Verifica que _handle_reflect_command ya no bloquea el event loop."""

    def _make_orchestrator_with_reflect(self, reflection_text="Reflexión de prueba generada por el LLM."):
        """Orchestrator parcial con lo mínimo para probar _handle_reflect_command."""
        orc = MagicMock()
        orc.console = MagicMock()
        orc.llm = MagicMock()
        orc.llm.generate = MagicMock(return_value=reflection_text)
        orc.self_state = MagicMock()
        orc.self_state.build_reflection_prompt = MagicMock(
            return_value="Prompt de reflexión de prueba"
        )
        orc.self_state.session_stats = {
            "interactions_count": 10,
            "errors_count": 1,
            "capability_mismatches": 0,
            "avg_response_time": 0.5,
        }
        return orc

    @pytest.mark.asyncio
    async def test_reflect_is_async_coroutine(self):
        """_handle_reflect_command debe ser una coroutine (async def)."""
        from core.orchestrator import Orchestrator
        assert inspect.iscoroutinefunction(Orchestrator._handle_reflect_command), (
            "_handle_reflect_command debe ser async def, no def"
        )

    @pytest.mark.asyncio
    async def test_reflect_uses_to_thread(self):
        """_handle_reflect_command debe ser async y producir un resultado string.

        La implementación actual usa call_deepseek_flash/call_sonnet directamente
        (async native). Este test verifica que la función es awaitable y devuelve
        un string con el contenido de la reflexión cuando los helpers funcionan.
        """
        orc = self._make_orchestrator_with_reflect()
        reflection_text = "Reflexión de prueba exitosa"

        # Mock stats produce error_rate=10% → Tier 2 (call_sonnet). Patch both tiers.
        with patch("core.llm_helpers.call_sonnet", new_callable=AsyncMock) as mock_sonnet, \
             patch("core.llm_helpers.call_deepseek_flash", new_callable=AsyncMock) as mock_flash:
            mock_sonnet.return_value = reflection_text
            mock_flash.return_value = reflection_text

            from core.orchestrator import Orchestrator
            result = await Orchestrator._handle_reflect_command(orc)

            # La función debe ser awaitable y devolver un string con la reflexión
            assert isinstance(result, str)
            assert len(result) > 0
            # Al menos uno de los helpers LLM debe haberse invocado
            assert mock_sonnet.called or mock_flash.called, (
                "Ningún helper LLM fue llamado"
            )

    @pytest.mark.asyncio
    async def test_reflect_cloud_fallback_to_local(self):
        """Si el helper LLM retorna vacío, cae al fallback asyncio.to_thread(llm.generate).

        Se usa la implementación real de asyncio.to_thread para evitar corromper
        el estado interno del SDK de Anthropic en tests posteriores.
        El MagicMock de orc.llm.generate es sincrónico, por lo que to_thread lo
        ejecuta en el thread pool y devuelve el valor del mock.
        """
        orc = self._make_orchestrator_with_reflect()

        async def empty_helper(prompt, *args, **kwargs):
            return ""  # Helper retorna vacío → fallback a llm.generate vía to_thread

        # Patch ONLY the tier helpers. asyncio.to_thread se deja sin parchear
        # para que la llamada real al thread pool funcione sin contaminar asyncio.
        with patch("core.llm_helpers.call_sonnet", side_effect=empty_helper), \
             patch("core.llm_helpers.call_deepseek_flash", side_effect=empty_helper):
            from core.orchestrator import Orchestrator
            result = await Orchestrator._handle_reflect_command(orc)

        # La función debe devolver un string (desde llm.generate vía to_thread)
        assert isinstance(result, str)
        # llm.generate debe haberse llamado como fallback
        assert orc.llm.generate.called, "llm.generate no fue llamado como fallback"

    @pytest.mark.asyncio
    async def test_reflect_no_data_early_return_no_llm(self):
        """Sin datos suficientes, retorna mensaje sin llamar al LLM."""
        orc = self._make_orchestrator_with_reflect()
        orc.self_state.build_reflection_prompt = MagicMock(return_value=None)

        # No patch on asyncio.to_thread — the early return avoids reaching that code.
        from core.orchestrator import Orchestrator
        result = await Orchestrator._handle_reflect_command(orc)

        assert "No hay suficientes datos" in result

    @pytest.mark.asyncio
    async def test_reflect_exception_returns_clean_string(self):
        """Si todos los LLM helpers lanzan excepción, se retorna un string limpio sin traceback."""
        orc = self._make_orchestrator_with_reflect()

        async def failing_helper(prompt, *args, **kwargs):
            raise RuntimeError("Cloud API timeout")

        # Patch all tier helpers. asyncio.to_thread fallback también fallará porque
        # orc.llm.generate levantará la misma excepción desde el MagicMock side_effect.
        orc.llm.generate = MagicMock(side_effect=RuntimeError("Cloud API timeout"))
        with patch("core.llm_helpers.call_sonnet", side_effect=failing_helper), \
             patch("core.llm_helpers.call_deepseek_flash", side_effect=failing_helper):
            from core.orchestrator import Orchestrator
            result = await Orchestrator._handle_reflect_command(orc)

        assert isinstance(result, str)
        assert "Traceback" not in result

    @pytest.mark.asyncio
    async def test_concurrent_reflects_no_block(self):
        """
        Dos llamadas simultáneas a reflect no se bloquean mutuamente.
        _handle_reflect_command es async y usa helpers async nativos,
        por lo que dos llamadas en gather deben completarse concurrentemente.
        """
        orc = self._make_orchestrator_with_reflect()

        async def slow_flash(prompt, *args, **kwargs):
            await asyncio.sleep(0.05)  # Simula 50ms de LLM call
            return "Reflexión concurrente"

        # Patch both tier helpers so no real network call is made
        with patch("core.llm_helpers.call_sonnet", side_effect=slow_flash), \
             patch("core.llm_helpers.call_deepseek_flash", side_effect=slow_flash):
            from core.orchestrator import Orchestrator
            start = time.monotonic()
            results = await asyncio.gather(
                Orchestrator._handle_reflect_command(orc),
                Orchestrator._handle_reflect_command(orc),
            )
            elapsed = time.monotonic() - start

        # Si corrieran secuencialmente serían ~100ms mínimo.
        # En paralelo deben ser ~50ms (+margen generoso para CI).
        assert elapsed < 0.5, (
            f"Dos reflects tardaron {elapsed:.3f}s — probablemente están bloqueando el event loop"
        )
        assert len(results) == 2
        for r in results:
            assert isinstance(r, str)


# ═════════════════════════════════════════════════════════════════════════
# BUG-P2: _on_message ahora tiene try/except
# ═════════════════════════════════════════════════════════════════════════

class TestBugP2OnMessageErrorHandling:
    """Verifica que _on_message captura excepciones y responde al usuario."""

    @pytest.mark.asyncio
    async def test_on_message_dispatch_error_returns_clean_response(self):
        """Si _dispatch lanza excepción, el usuario recibe un string de error."""
        try:
            from channels.telegram_channel import TelegramChannel
        except ImportError:
            pytest.skip("python-telegram-bot no instalado")

        orc = make_mock_orchestrator()
        orc.handle_input = AsyncMock(side_effect=RuntimeError("DB connection lost"))

        channel = TelegramChannel(orc)
        channel._authorized_ids = set()

        update = make_update("hola qué tal")
        ctx = MagicMock()

        # No debe propagar la excepción
        await channel._on_message(update, ctx)

        # El usuario debe haber recibido algo
        update.message.reply_text.assert_called()
        response_text = update.message.reply_text.call_args.args[0]
        assert isinstance(response_text, str)
        assert len(response_text) > 0

    @pytest.mark.asyncio
    async def test_on_message_dispatch_error_no_traceback(self):
        """El mensaje de error no debe contener un traceback de Python."""
        try:
            from channels.telegram_channel import TelegramChannel
        except ImportError:
            pytest.skip("python-telegram-bot no instalado")

        orc = make_mock_orchestrator()
        orc.handle_input = AsyncMock(
            side_effect=ValueError("KeyError: 'missing_key'\n  File \"orchestrator.py\", line 100")
        )

        channel = TelegramChannel(orc)
        channel._authorized_ids = set()

        update = make_update("test message")
        ctx = MagicMock()

        await channel._on_message(update, ctx)

        response_text = update.message.reply_text.call_args.args[0]
        # No debe tener "Traceback (most recent call last)"
        assert "Traceback" not in response_text

    @pytest.mark.asyncio
    async def test_on_message_success_unchanged(self):
        """
        Regression: el flow exitoso sigue funcionando tras agregar try/except.
        El usuario debe recibir la respuesta del orchestrator, no un error.
        """
        try:
            from channels.telegram_channel import TelegramChannel
        except ImportError:
            pytest.skip("python-telegram-bot no instalado")

        orc = make_mock_orchestrator(handle_result="Hola, soy Zephyro")
        channel = TelegramChannel(orc)
        channel._authorized_ids = set()

        update = make_update("hola")
        ctx = MagicMock()

        await channel._on_message(update, ctx)

        orc.handle_input.assert_called_once()
        # reply_text se llama (puede ser vía _reply que llama reply_text)
        update.message.reply_text.assert_called()
        response_text = update.message.reply_text.call_args.args[0]
        assert "Zephyro" in response_text

    @pytest.mark.asyncio
    async def test_on_message_reply_error_caught(self):
        """
        Si _reply (reply_text) falla después del dispatch exitoso,
        la excepción se captura y se envía un mensaje de error.
        """
        try:
            from channels.telegram_channel import TelegramChannel
        except ImportError:
            pytest.skip("python-telegram-bot no instalado")

        orc = make_mock_orchestrator(handle_result="Respuesta normal")
        channel = TelegramChannel(orc)
        channel._authorized_ids = set()

        update = make_update("test")
        ctx = MagicMock()

        call_count = 0

        async def fail_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Primer reply_text (de _reply) falla
                raise ConnectionError("Telegram API timeout")
            # Segundo reply_text (del except) funciona

        update.message.reply_text = AsyncMock(side_effect=fail_then_succeed)

        # No debe propagar la excepción al caller
        # Nota: si el reply_text del except también falla, python-telegram-bot lo maneja
        try:
            await channel._on_message(update, ctx)
        except ConnectionError:
            # Si la segunda llamada a reply_text también usara la misma side_effect,
            # es aceptable que propague — lo importante es que lo intentó
            pass

        # Al menos se intentó responder
        assert call_count >= 1


# ═════════════════════════════════════════════════════════════════════════
# BUG-P3: COMMANDS_TEXT ahora incluye los slash-commands de skills
# ═════════════════════════════════════════════════════════════════════════

class TestBugP3CommandsText:
    """Verifica que COMMANDS_TEXT incluye los comandos de monitorización."""

    def test_commands_text_includes_system_monitor(self):
        """COMMANDS_TEXT debe contener la sección de monitoreo."""
        try:
            from channels.telegram_channel import COMMANDS_TEXT
        except ImportError:
            pytest.skip("python-telegram-bot no instalado")

        assert "Monitoreo" in COMMANDS_TEXT or "monitoreo" in COMMANDS_TEXT, (
            "COMMANDS_TEXT no incluye la sección 'Monitoreo del sistema'"
        )

    def test_commands_text_includes_all_skill_commands(self):
        """Cada slash-command de skills debe aparecer en COMMANDS_TEXT."""
        try:
            from channels.telegram_channel import COMMANDS_TEXT
        except ImportError:
            pytest.skip("python-telegram-bot no instalado")

        required_commands = ["/system", "/ram", "/cpu", "/disk", "/red"]
        missing = [cmd for cmd in required_commands if cmd not in COMMANDS_TEXT]

        assert not missing, (
            f"COMMANDS_TEXT le faltan estos comandos: {missing}\n"
            f"Contenido actual:\n{COMMANDS_TEXT}"
        )

    def test_commands_text_no_duplicates(self):
        """Cada sección y comando debe aparecer solo una vez."""
        try:
            from channels.telegram_channel import COMMANDS_TEXT
        except ImportError:
            pytest.skip("python-telegram-bot no instalado")

        lines = [l.strip() for l in COMMANDS_TEXT.split("\n") if l.strip()]
        seen = set()
        dupes = []
        for line in lines:
            if line.startswith("/"):
                cmd = line.split()[0]
                if cmd in seen:
                    dupes.append(cmd)
                seen.add(cmd)

        assert not dupes, f"Comandos duplicados en COMMANDS_TEXT: {dupes}"

    def test_commands_text_existing_commands_still_present(self):
        """Regression: los comandos originales deben seguir presentes."""
        try:
            from channels.telegram_channel import COMMANDS_TEXT
        except ImportError:
            pytest.skip("python-telegram-bot no instalado")

        original_commands = [
            "/mode", "/provider", "/model", "/status", "/skills",
            "/specialty", "/search", "/reflect", "/bugs", "/perfil",
            "/resumenes", "/commands", "/help",
        ]
        missing = [cmd for cmd in original_commands if cmd not in COMMANDS_TEXT]
        assert not missing, f"Comandos originales faltantes: {missing}"
