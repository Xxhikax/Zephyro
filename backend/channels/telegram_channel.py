"""
TelegramChannel — Adapter de Telegram para Zephyro usando python-telegram-bot v20+.

Requiere: pip install python-telegram-bot
Env vars:
  TELEGRAM_BOT_TOKEN        — Token del bot (BotFather)
  TELEGRAM_AUTHORIZED_IDS   — IDs autorizados separados por coma (opcional)

Comandos expuestos:
  /start, /help, /commands  — Ayuda y lista de comandos
  /status                   — Estado del sistema
  /mode <local|cloud|auto>  — Cambiar modo LLM
  /provider <nombre>        — Cambiar provider cloud
  /skills                   — Listar habilidades
  /search <query>           — Búsqueda web
  /reflect                  — Reflexión introspectiva
  /bugs                     — Ver bugs reportados
  /specialty [list|set X]   — Ver/cambiar especialidad
  /perfil                   — Ver perfil del usuario
  /resumenes                — Ver resúmenes de conversación
  Cualquier texto           — Procesado por el LLM
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any

_log = logging.getLogger("zephyro.telegram_gate")

from channels.base import BaseChannel, ChannelMessage

try:
    import certifi
    from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        filters,
        ContextTypes,
    )
    from telegram.request import HTTPXRequest
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False


MAX_MESSAGE_LENGTH = 4096

COMMANDS_TEXT = (
    "Comandos de Zephyro:\n\n"
    "Configuración:\n"
    "/mode <local|cloud|auto> — Modo LLM\n"
    "/provider <gemini|anthropic|openai|deepseek> — Provider cloud\n"
    "/model <haiku|sonnet|opus> — Modelo Anthropic\n"
    "/status — Estado del sistema\n"
    "/skills — Habilidades cargadas\n"
    "/specialty — Especialidad activa\n\n"
    "Herramientas:\n"
    "/search <query> — Búsqueda web\n"
    "/reflect — Reflexión introspectiva\n"
    "/bugs — Bugs reportados\n"
    "/diagnose [N] — Diagnóstico automático de errores\n"
    "/living [tier] — Estado interno del sistema\n"
    "/introspect — Por qué fallé / estado interno honesto\n"
    "/perfil — Tu perfil de usuario\n"
    "/resumenes — Resúmenes de conversación\n\n"
    "Agenda y Canvas:\n"
    "/agenda — Ver tu agenda de hoy\n"
    "/canvas — Resumen rápido de Canvas\n\n"
    "Monitoreo del sistema:\n"
    "/system — Reporte completo\n"
    "/ram — Uso de memoria RAM\n"
    "/cpu — Uso de CPU\n"
    "/disk — Espacio en disco\n"
    "/red — Estadísticas de red\n\n"
    "/commands — Esta lista\n"
    "/help — Ayuda completa\n\n"
    "También puedes escribirme cualquier mensaje directamente."
)


def _split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Divide mensajes largos respetando saltos de línea."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


class TelegramChannel(BaseChannel):
    """Adapter de Telegram para Zephyro."""

    channel_type = "telegram"

    def __init__(self, orchestrator: Any):
        super().__init__(orchestrator)
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._app: Any = None
        self._task: asyncio.Task | None = None
        authorized = os.getenv("TELEGRAM_AUTHORIZED_IDS", "")
        self._authorized_ids: set[int] = set()
        if authorized:
            self._authorized_ids = {int(x.strip()) for x in authorized.split(",") if x.strip()}
        # Approval gate storage para DiagnosticAgent
        self._pending_approvals: dict[int, asyncio.Event] = {}
        self._approval_results: dict[int, str] = {}
        # LivingState (inyectado post-creación desde web_app lifespan)
        self._living_state: Any = None
        # Consciousness (inyectado post-creación desde web_app lifespan)
        self._consciousness: Any = None

    def _is_authorized(self, user_id: int) -> bool:
        if not self._authorized_ids:
            return True
        return user_id in self._authorized_ids

    def get_authorized_ids(self) -> list[str]:
        return [f"telegram:{cid}" for cid in self._authorized_ids]

    def _user_id(self, update: Update) -> int:
        return update.effective_user.id

    def _user_name(self, update: Update) -> str:
        return update.effective_user.first_name or ""

    def _channel_user_id(self, update: Update) -> str:
        return f"telegram:{self._user_id(update)}"

    async def _dispatch(self, update: Update, text: str) -> str:
        """Crea ChannelMessage y lo procesa en el orchestrator."""
        msg = ChannelMessage(
            text=text,
            channel_type="telegram",
            channel_user_id=self._channel_user_id(update),
            user_name=self._user_name(update),
        )
        return await self.process_message(msg)

    async def _reply(self, update: Update, text: str) -> None:
        for chunk in _split_message(text):
            await update.message.reply_text(chunk)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        if not HAS_TELEGRAM:
            raise ImportError("python-telegram-bot no instalado. pip install python-telegram-bot")
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN no configurado")

        _request = HTTPXRequest(httpx_kwargs={"verify": certifi.where()})
        self._app = (
            Application.builder()
            .token(self.token)
            .request(_request)
            .get_updates_request(_request)
            .build()
        )

        handlers = [
            ("start",     self._cmd_start),
            ("help",      self._cmd_help),
            ("commands",  self._cmd_commands),
            ("status",    self._cmd_status),
            ("mode",      self._cmd_mode),
            ("provider",  self._cmd_provider),
            ("model",     self._cmd_model),
            ("skills",    self._cmd_skills),
            ("search",    self._cmd_search),
            ("reflect",   self._cmd_reflect),
            ("bugs",      self._cmd_bugs),
            ("specialty", self._cmd_specialty),
            ("perfil",       self._cmd_perfil),
            ("resumenes",    self._cmd_resumenes),
            ("capacidades",  self._cmd_capacidades),
            ("limitaciones", self._cmd_limitaciones),
            ("diagnose",     self._cmd_diagnose),
            ("living",       self._cmd_living),
            ("introspect",   self._cmd_introspect),
            ("agenda",      self._cmd_agenda),
            ("canvas",      self._cmd_canvas),
        ]
        for name, handler in handlers:
            self._app.add_handler(CommandHandler(name, handler))

        # Callback queries del approval gate del DiagnosticAgent
        self._app.add_handler(CallbackQueryHandler(self._on_approval_callback, pattern=r"^diag:"))
        # Callback queries de notificaciones Canvas (missing/pending)
        self._app.add_handler(CallbackQueryHandler(self._on_canvas_callback, pattern=r"^canvas:"))

        # Catch-all: comandos no registrados arriba (e.g. /red /ram /cpu de skills)
        # Debe registrarse DESPUÉS de todos los CommandHandlers específicos.
        self._app.add_handler(MessageHandler(filters.COMMAND, self._on_unknown_command))

        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))

        await self._app.bot.set_my_commands([
            BotCommand("start",     "Iniciar Zephyro"),
            BotCommand("commands",  "Ver todos los comandos"),
            BotCommand("status",    "Estado del sistema"),
            BotCommand("mode",      "Cambiar modo (local/cloud/auto)"),
            BotCommand("provider",  "Cambiar provider cloud"),
            BotCommand("model",     "Modelo Anthropic (haiku/sonnet/opus)"),
            BotCommand("skills",    "Listar habilidades"),
            BotCommand("search",    "Búsqueda web"),
            BotCommand("reflect",   "Reflexión introspectiva"),
            BotCommand("bugs",      "Ver bugs reportados"),
            BotCommand("specialty", "Ver/cambiar especialidad"),
            BotCommand("perfil",    "Tu perfil de usuario"),
            BotCommand("resumenes",    "Resúmenes de conversación"),
            BotCommand("capacidades",  "Ver capacidades actuales"),
            BotCommand("limitaciones", "Ver limitaciones conocidas"),
            BotCommand("diagnose",     "Diagnóstico de errores del sistema"),
            BotCommand("living",       "Estado interno del sistema"),
            BotCommand("introspect",   "Por qué fallé / estado interno honesto"),
            BotCommand("agenda",       "Ver tu agenda de hoy"),
            BotCommand("canvas",       "Resumen de Canvas (tareas, notas, anuncios)"),
            BotCommand("help",         "Ayuda"),
        ])

        await self._app.initialize()
        await self._app.start()
        self._task = asyncio.create_task(self._app.updater.start_polling(drop_pending_updates=True))
        self._running = True

    async def stop(self) -> None:
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                pass
        self._running = False

    async def send(self, channel_user_id: str, text: str, parse_mode: str = None) -> None:
        if not self._app:
            return
        chat_id = int(channel_user_id.replace("telegram:", ""))
        for chunk in _split_message(text):
            await self._app.bot.send_message(
                chat_id=chat_id, text=chunk,
                parse_mode=parse_mode, disable_web_page_preview=True
            )

    # ── Message handler ───────────────────────────────────────────────

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        if not self._is_authorized(self._user_id(update)):
            await update.message.reply_text("No autorizado. Contacta al administrador.")
            return
        await update.message.chat.send_action("typing")
        try:
            response = await self._dispatch(update, update.message.text)
            await self._reply(update, response)
        except Exception as e:
            await update.message.reply_text(f"Error procesando el mensaje: {e}")

    # ── Command handlers ──────────────────────────────────────────────

    async def _on_unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Catch-all para comandos slash no registrados como CommandHandler.
        Permite que skills con triggers tipo /ram, /red, /cpu, etc. lleguen
        al orchestrator → skill_registry.try_handle().
        """
        if not update.message or not update.message.text:
            return
        if not self._is_authorized(self._user_id(update)):
            await update.message.reply_text("No autorizado. Contacta al administrador.")
            return
        await update.message.chat.send_action("typing")
        try:
            response = await self._dispatch(update, update.message.text)
            await self._reply(update, response)
        except Exception as e:
            await update.message.reply_text(f"Error procesando el comando: {e}")

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not self._is_authorized(user.id):
            await update.message.reply_text("No autorizado.")
            return
        await update.message.reply_text(
            f"Hola {user.first_name}, soy Zephyro, tu asistente personal.\n\n"
            + COMMANDS_TEXT
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._cmd_start(update, context)

    async def _cmd_commands(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        await update.message.reply_text(COMMANDS_TEXT)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        try:
            status = self.orchestrator.llm.get_status()
            cloud = status.get("cloud_provider", "no configurado")
            local = status.get("local_available", False)
            text = (
                "Zephyro Status\n"
                f"Local LLM: {'activo' if local else 'inactivo'}\n"
                f"Cloud: {cloud}\n"
                f"Skills: {len(self.orchestrator.skill_registry)} cargadas"
            )
            await update.message.reply_text(text)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def _cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        args = context.args
        if not args:
            await update.message.reply_text("Uso: /mode <local|cloud|auto>")
            return
        mode = args[0].lower()
        if mode not in ("local", "cloud", "auto"):
            await update.message.reply_text("Modos válidos: local, cloud, auto")
            return
        response = await self._dispatch(update, f"/mode {mode}")
        await self._reply(update, response)

    async def _cmd_provider(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        args = context.args
        valid_providers = {"gemini", "anthropic", "openai", "deepseek"}
        if not args or args[0].lower() not in valid_providers:
            current = getattr(self.orchestrator.llm, "cloud_provider", "desconocido")
            await update.message.reply_text(
                f"Provider actual: {current}\n"
                f"Uso: /provider <{' | '.join(sorted(valid_providers))}>"
            )
            return
        provider = args[0].lower()
        try:
            status = self.orchestrator.llm.set_cloud_provider(provider)
            cloud = status.get("cloud_provider", provider)
            local = status.get("local_available", False)
            await update.message.reply_text(
                f"✓ Provider cloud activo: {cloud}\n"
                f"Fallback local (Ollama): {'disponible' if local else 'no disponible'}"
            )
        except Exception as e:
            await update.message.reply_text(f"Error al cambiar provider: {e}")

    async def _cmd_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        from core.model_catalog import ANTHROPIC_ALIASES as aliases
        args = context.args
        if not args or args[0].lower() not in aliases:
            current = getattr(self.orchestrator.llm, "anthropic_model", "desconocido")
            short = current.replace("claude-", "")
            await update.message.reply_text(
                f"Modelo Anthropic actual: {short} ({current})\n"
                f"Uso: /model <haiku | sonnet | opus>"
            )
            return
        choice = args[0].lower()
        model_id = aliases[choice]
        try:
            status = self.orchestrator.llm.set_anthropic_model(model_id)
            await update.message.reply_text(f"✓ Modelo Anthropic: {choice} ({model_id})")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def _cmd_skills(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        try:
            summary = self.orchestrator.skill_registry.get_status_summary()
            await self._reply(update, summary)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def _cmd_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        if not context.args:
            await update.message.reply_text("Uso: /search <consulta>")
            return
        query = " ".join(context.args)
        await update.message.chat.send_action("typing")
        response = await self._dispatch(update, f"buscar {query}")
        await self._reply(update, response)

    async def _cmd_reflect(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        await update.message.chat.send_action("typing")
        response = await self._dispatch(update, "/reflect")
        await self._reply(update, response)

    async def _cmd_bugs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        response = await self._dispatch(update, "/bugs")
        await self._reply(update, response)

    async def _cmd_specialty(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        text = "/specialty " + " ".join(context.args) if context.args else "/specialty list"
        response = await self._dispatch(update, text)
        await self._reply(update, response)

    async def _cmd_perfil(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        response = await self._dispatch(update, "/perfil")
        await self._reply(update, response)

    async def _cmd_resumenes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        response = await self._dispatch(update, "/resumenes")
        await self._reply(update, response)

    async def _cmd_capacidades(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        response = await self._dispatch(update, "/capacidades")
        await self._reply(update, response)

    async def _cmd_limitaciones(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            return
        response = await self._dispatch(update, "/limitaciones")
        await self._reply(update, response)

    # ── DiagnosticAgent: approval gate ────────────────────────────────────────

    async def send_approval_request(self, chat_id: int, text: str) -> int:
        """Envía un mensaje con inline keyboard para el approval gate.

        Returns:
            message_id del mensaje enviado (clave para wait_for_approval).
        """
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Ver diagnóstico", callback_data="diag:view"),
                InlineKeyboardButton("❌ Ignorar",         callback_data="diag:ignore"),
            ],
            [InlineKeyboardButton("✏️ Pedir más contexto", callback_data="diag:more_context")],
        ])
        msg = await self._app.bot.send_message(
            chat_id=chat_id,
            text=text[:4000],
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        self._pending_approvals[msg.message_id] = asyncio.Event()
        return msg.message_id

    async def wait_for_approval(self, message_id: int, timeout: float = 300.0) -> str:
        """Espera la respuesta del usuario al approval gate.

        Returns:
            "view" | "ignore" | "more_context". Retorna "ignore" en timeout.
        """
        event = self._pending_approvals.get(message_id)
        if event is None:
            return "ignore"
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._approval_results.pop(message_id, "ignore")
        except asyncio.TimeoutError:
            return "ignore"
        finally:
            self._pending_approvals.pop(message_id, None)

    async def _on_approval_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handler para callbacks inline del DiagnosticAgent (patrón ^diag:)."""
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        if not self._is_authorized(query.from_user.id):
            return

        action = query.data.split(":", 1)[1]   # "view" | "ignore" | "more_context"
        message_id = query.message.message_id

        self._approval_results[message_id] = action
        event = self._pending_approvals.get(message_id)
        if event:
            event.set()

        # Eliminar los botones del mensaje para evitar doble-click
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

    async def _cmd_diagnose(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/diagnose [N] — Inicia el DiagnosticAgent con las últimas N líneas de log."""
        if not self._is_authorized(self._user_id(update)):
            return
        args = context.args or []
        n_lines = int(args[0]) if args and args[0].isdigit() else 500
        chat_id = self._user_id(update)
        await update.message.reply_text(
            f"🔍 Iniciando diagnóstico (últimas {n_lines} líneas de log)..."
        )
        try:
            import os as _os
            from pathlib import Path as _Path
            from subagents.diagnostic_agent import DiagnosticAgent
            agent = DiagnosticAgent(
                llm_manager=self.orchestrator.llm,
                telegram_channel=self,
                log_file=_os.getenv(
                    "ZEPHYRO_LOG_FILE",
                    str(_Path(__file__).parent.parent.parent / "logs" / "backend.log"),
                ),
                code_root=str(_Path(__file__).parent.parent),
            )
            result = await agent.run(chat_id=chat_id, n_lines=n_lines)
            await self._reply(update, result)
        except Exception as exc:
            await update.message.reply_text(f"Error en diagnóstico: {exc}")

    # ── LivingState ───────────────────────────────────────────────────────────

    def set_living_state(self, living_state: Any) -> None:
        """Inyecta el LivingState para el comando /living."""
        self._living_state = living_state

    async def _cmd_living(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/living [tier] — Muestra el estado interno del sistema (tier 1-3)."""
        if not self._is_authorized(self._user_id(update)):
            return
        ls = self._living_state
        if ls is None:
            await update.message.reply_text("LivingState no disponible.")
            return
        args = context.args or []
        tier = int(args[0]) if args and args[0].isdigit() and 1 <= int(args[0]) <= 3 else 2
        snap = ls.snapshot_for_llm(tier=tier)
        await self._reply(update, f"Estado del sistema (tier {tier}):\n\n{snap}")

    # ── Consciousness ─────────────────────────────────────────────────────────

    def set_consciousness(self, consciousness: Any) -> None:
        """Inyecta el Consciousness para el comando /introspect."""
        self._consciousness = consciousness

    async def _cmd_introspect(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/introspect — Estado interno honesto: issues activos, skills rotas, guard failures."""
        if not self._is_authorized(self._user_id(update)):
            return
        c = self._consciousness
        if c is None:
            await update.message.reply_text("Consciousness no disponible.")
            return
        try:
            report = c.introspect()
            await self._reply(update, report)
        except Exception as exc:
            await update.message.reply_text(f"Error en introspect: {exc}")

    async def _cmd_agenda(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            await update.message.reply_text("⛔ No autorizado.")
            return

        try:
            from modules.schedule.service import get_schedule_service
            svc = get_schedule_service()
            now = time.time()
            end = now + 24 * 3600
            events = svc.get_events_in_range("admin", now, end)
            if not events:
                await update.message.reply_text("📅 No tienes eventos para hoy.")
                return

            lines = ["<b>📅 Agenda de hoy</b>\n"]
            for ev in sorted(events, key=lambda e: e.start_ts_utc):
                ts = datetime.fromtimestamp(ev.start_ts_utc).strftime("%H:%M")
                cat = ev.category or "otro"
                emoji = {"university": "📚", "uni_exam": "📝", "work": "💼"}.get(cat, "📌")
                lines.append(f"• <b>{ts}</b> {emoji} {ev.title}")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Error al obtener agenda: {e}")

    async def _cmd_canvas(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(self._user_id(update)):
            await update.message.reply_text("⛔ No autorizado.")
            return

        try:
            from modules.canvas.client import CanvasClient
            client = CanvasClient()
            grades, missing, announcements = await asyncio.gather(
                client.get_course_grades(),
                client.get_missing_submissions(),
                client.get_announcements(limit=3),
            )
            lines = ["<b>📚 Canvas — Resumen</b>\n"]
            if missing:
                lines.append(f"⚠️ <b>{len(missing)} entregas faltantes</b>")
                for m in missing[:5]:
                    name = m.get("name", "?")
                    course = m.get("course_name", m.get("context_name", ""))
                    due = m.get("due_at", "?")
                    lines.append(f"  • {name} ({course}) — {due}")
            else:
                lines.append("✅ Sin entregas pendientes.")
            if not missing and not grades and not announcements:
                lines.append("No hay datos de Canvas disponibles.")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Error al obtener Canvas: {e}")


    # ── Canvas interactive notifications ──────────────────────────────────────

    async def send_canvas_interactive(
        self, chat_id: int, text: str, keyboard: list
    ) -> None:
        """Send a Canvas notification with an inline keyboard.

        keyboard: list of rows; each row is a list of (label, callback_data) tuples.
        """
        if not self._app:
            return
        tg_keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(label, callback_data=cdata) for label, cdata in row]
                for row in keyboard
            ]
        )
        await self._app.bot.send_message(
            chat_id=chat_id,
            text=text[:4000],
            reply_markup=tg_keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def _on_canvas_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle Canvas inline keyboard responses.

        Callback data formats:
          canvas:missing:{item_id}:si        → user will submit
          canvas:missing:{item_id}:no        → user won't submit / "remind later" → done
          canvas:missing:{item_id}:entregue  → user confirms submitted → done
          canvas:pending:ok                  → OK for 4h reminder cycle
          canvas:pending:ignorar             → silence until tomorrow
        """
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        if not self._is_authorized(query.from_user.id):
            return

        parts = query.data.split(":")
        # Remove the buttons immediately to prevent double-click
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        try:
            from modules.canvas.notification_state import (
                get_canvas_notification_state,
                CanvasNotificationState,
            )
            import time as _time
            ns = get_canvas_notification_state()

            if len(parts) < 3:
                return

            category = parts[1]  # "missing" or "pending"

            if category == "missing" and len(parts) == 4:
                item_id = parts[2]
                action = parts[3]

                if action == "si":
                    from core.proactive_notifier import ProactiveNotifier
                    next_ts = ProactiveNotifier._next_notify_ts(_time.time())
                    ns.set_state(item_id, "missing", "will_submit", next_ts)
                    await query.message.reply_text(
                        "✅ Anotado. Te recuerdo a las 8am o 8pm para que no se te olvide."
                    )
                elif action == "recordar":
                    # "Recuérdame luego" from will_submit reminder — stay in will_submit
                    from core.proactive_notifier import ProactiveNotifier
                    next_ts = ProactiveNotifier._next_notify_ts(_time.time())
                    ns.set_state(item_id, "missing", "will_submit", next_ts)
                    await query.message.reply_text("🔔 Te recuerdo en el próximo ciclo.")
                elif action == "entregue":
                    ns.set_state(item_id, "missing", "done")
                    await query.message.reply_text("✅ Perfecto, marcado como entregado.")
                elif action == "no":
                    ns.set_state(item_id, "missing", "done")
                    await query.message.reply_text(
                        "✅ Entendido, no te molesto más con esa entrega."
                    )

            elif category == "pending":
                action = parts[2] if len(parts) >= 3 else ""

                if action == "ok":
                    next_ts = _time.time() + 4 * 3600
                    ns.set_state("global", "pending", "ok", next_ts)
                    await query.message.reply_text(
                        "✅ Te recuerdo en 4 horas."
                    )
                elif action == "ignorar":
                    next_ts = CanvasNotificationState.tomorrow_midnight()
                    ns.set_state("global", "pending", "ignored", next_ts)
                    await query.message.reply_text(
                        "🔕 Entendido. Te aviso mañana."
                    )

        except Exception as exc:
            _log.warning(f"[telegram] canvas callback error: {exc}")


# ── ApprovalGate adapter ──────────────────────────────────────────────────────

class TelegramApprovalGate:
    """Adapter que expone TelegramChannel como ApprovalGate canal-agnóstico.

    Traduce request_id (str) ↔ message_id (int) para cumplir el Protocol
    sin romper el API existente de TelegramChannel.
    """

    def __init__(self, channel: "TelegramChannel") -> None:
        self._channel = channel

    async def send_approval_request(self, context_id: int, text: str) -> str:
        message_id = await self._channel.send_approval_request(context_id, text)
        return str(message_id)

    async def wait_for_approval(self, request_id: str, timeout: float = 300.0) -> str:
        try:
            message_id = int(request_id)
        except (TypeError, ValueError):
            return "ignore"
        return await self._channel.wait_for_approval(message_id, timeout=timeout)

    async def send_message(self, context_id: int, text: str) -> None:
        try:
            await self._channel.send(f"telegram:{context_id}", text)
        except Exception as exc:
            _log.warning("TelegramApprovalGate.send_message failed for chat_id=%s: %s", context_id, exc)
