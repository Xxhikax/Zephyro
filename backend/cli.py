#!/usr/bin/env python3
"""
jarvis-terminal — Textual TUI + integración profunda con el SO Windows.

Proceso completamente independiente del backend: se comunica SOLO via WebSocket.
No importa nada de backend/ — el contrato es el protocolo WS en localhost:8000.

Modos:
  TUI interactiva (default):     python cli.py
  Pipe (sin TUI):                git diff | python cli.py --pipe "explica este diff"
  Skill directo (sin NLP):       python cli.py --skill git-ops "status"
  One-shot (sin TUI):            python cli.py --once "cuánta RAM tengo"

Integración SO:
  - Clipboard (pyperclip): Ctrl+V injerta lo que tienes copiado como contexto
  - Ventana activa (pygetwindow): F3 captura el título de la app en foco
  - Pipes: stdin no-TTY → modo headless automático
  - Salida a archivo: python cli.py --once "..." --output result.txt
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Textual imports ───────────────────────────────────────────────────────────
try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical, VerticalScroll
    from textual.widgets import Footer, Input, Label, RichLog, Static
    from textual.widget import Widget
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False

# ── Websockets ────────────────────────────────────────────────────────────────
try:
    import websockets
    import websockets.exceptions
    _HAS_WS = True
except ImportError:
    _HAS_WS = False

# ── OS integration (optional) ─────────────────────────────────────────────────
try:
    import pyperclip
    _HAS_CLIPBOARD = True
except ImportError:
    _HAS_CLIPBOARD = False

try:
    import pygetwindow as gw
    _HAS_GETWINDOW = True
except ImportError:
    _HAS_GETWINDOW = False

# ── Config ────────────────────────────────────────────────────────────────────
# Cargar .env del backend si está disponible (permite auto-auth sin configurar nada)
_ENV_FILE = Path(__file__).parent / ".env"
try:
    from dotenv import load_dotenv
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE, override=False)
except ImportError:
    pass

# 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 ::1 first and uvicorn only
# listens on IPv4 — Python clients pay a ~2 s fallback on EVERY connection.
WS_URL         = os.getenv("ZEPHYRO_WS_URL", "ws://127.0.0.1:8000/api/ws")
HTTP_BASE      = os.getenv("ZEPHYRO_HTTP_URL", "http://127.0.0.1:8000")
TOKEN_FILE     = Path.home() / ".zephyro" / "cli_token.txt"
HISTORY_FILE   = Path.home() / ".zephyro" / "terminal_history.jsonl"
CLIENT_TYPE    = "terminal"  # → recibe eventos verbosos del backend (tier, skill, verification)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket client — shared between TUI and headless modes
# ─────────────────────────────────────────────────────────────────────────────

class ZephyroWsClient:
    """Cliente WebSocket ligero. Maneja auth, envío y recepción de mensajes."""

    def __init__(self, token: str):
        self.token = token
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False

    async def connect(self) -> bool:
        try:
            self.ws = await websockets.connect(
                WS_URL,
                open_timeout=5,
                ping_interval=30,
                ping_timeout=10,
            )
            self._connected = True
            # Anunciar client_type para recibir verbose events
            await self.ws.send(json.dumps({
                "type": "hello",
                "token": self.token,
                "client_type": CLIENT_TYPE,
            }))
            return True
        except Exception as exc:
            self._connected = False
            return False

    async def send_chat(
        self,
        message: str,
        mode: str = "auto",
        skill: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> str:
        """Envía mensaje y devuelve request_id."""
        request_id = str(uuid.uuid4())[:8]
        payload: dict = {
            "token": self.token,
            "message": message,
            "mode": mode,
            "request_id": request_id,
            "speak": False,
            "client_type": CLIENT_TYPE,
        }
        if skill:
            payload["direct_skill"] = skill
        if context:
            payload["context"] = context
        await self.ws.send(json.dumps(payload))
        return request_id

    async def receive(self) -> Optional[dict]:
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=120)
            return json.loads(raw)
        except asyncio.TimeoutError:
            return None
        except websockets.exceptions.ConnectionClosed:
            self._connected = False
            return None

    async def close(self):
        if self.ws:
            await self.ws.close()


def _load_token() -> Optional[str]:
    """Lee token guardado o lo pide via HTTP login."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return None


async def _fetch_token(password: str = "") -> Optional[str]:
    """POST /api/auth/login y guarda el token."""
    try:
        import urllib.request
        data = json.dumps({"password": password}).encode("utf-8")
        req = urllib.request.Request(
            f"{HTTP_BASE}/api/auth/login",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
            token = body.get("token") or body.get("session_token")
            if token:
                TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
                TOKEN_FILE.write_text(token)
                return token
    except Exception:
        pass
    return None


def _get_clipboard_context() -> str:
    """Lee el clipboard del SO."""
    if not _HAS_CLIPBOARD:
        return ""
    try:
        text = pyperclip.paste()
        return text[:500] if text else ""
    except Exception:
        return ""


def _get_active_window_title() -> str:
    """Retorna el título de la ventana activa (Windows)."""
    if not _HAS_GETWINDOW:
        return ""
    try:
        win = gw.getActiveWindow()
        return win.title if win else ""
    except Exception:
        return ""


def _append_history(user_input: str, response: str, meta: dict):
    """Guarda interacción al historial local JSONL."""
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(),
            "input": user_input,
            "response": response[:800],
            **meta,
        }
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Headless mode (pipe / --once / --skill)
# ─────────────────────────────────────────────────────────────────────────────

async def run_headless(
    message: str,
    mode: str = "auto",
    skill: Optional[str] = None,
    output_file: Optional[str] = None,
) -> int:
    """Envía un mensaje, espera la respuesta, imprime y sale. Para pipes y scripts."""
    token = _load_token()
    if not token:
        admin_pw = os.getenv("ZEPHYRO_ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD", "")
        token = await _fetch_token(admin_pw)
    if not token:
        print("[ERROR] No hay token. Ejecuta 'python cli.py' para autenticarte.", file=sys.stderr)
        return 1

    client = ZephyroWsClient(token)
    ok = await client.connect()
    if not ok:
        print(f"[ERROR] No se pudo conectar a {WS_URL}. ¿Está el backend corriendo?", file=sys.stderr)
        return 1

    context = {}
    clipboard = _get_clipboard_context()
    if clipboard:
        context["clipboard"] = clipboard

    request_id = await client.send_chat(message, mode=mode, skill=skill, context=context or None)

    result_lines = []
    meta = {}
    while True:
        msg = await client.receive()
        if msg is None:
            break
        mtype = msg.get("type", "")

        if mtype == "response" and msg.get("request_id") == request_id:
            response = msg.get("response", "")
            result_lines.append(response)
            meta = {
                "mode_used": msg.get("mode_used"),
                "tier": msg.get("tier"),
                "elapsed": msg.get("elapsed_time"),
                "skill": msg.get("skill_used"),
            }
            break
        elif mtype == "error" and msg.get("request_id") == request_id:
            result_lines.append(f"[ERROR] {msg.get('error', '?')}")
            break
        elif mtype == "chunk" and msg.get("request_id") == request_id:
            sys.stdout.write(msg.get("content", ""))
            sys.stdout.flush()

    await client.close()

    final = "\n".join(result_lines)
    if final and not result_lines[0].startswith("chunk"):
        print(final)

    if output_file:
        Path(output_file).write_text(final, encoding="utf-8")

    if meta:
        tier_str = f"Tier {meta['tier']}" if meta.get("tier") else meta.get("mode_used", "")
        skill_str = f" | skill: {meta['skill']}" if meta.get("skill") else ""
        elapsed_str = f" | {meta['elapsed']:.2f}s" if meta.get("elapsed") else ""
        print(f"\n[{tier_str}{skill_str}{elapsed_str}]", file=sys.stderr)

    _append_history(message, final, meta)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Textual TUI
# ─────────────────────────────────────────────────────────────────────────────

if _HAS_TEXTUAL:

    class TopBar(Static):
        """Barra superior: ● ZEPHYRO · model · session · uptime · ws"""
        DEFAULT_CSS = """
        TopBar {
            height: 1;
            background: #0d1310;
            color: #7a8a82;
            padding: 0 2;
        }
        """

    class StatusBar(Static):
        """Status strip: chips de propuestas/alertas + cpu/mem a la derecha"""
        DEFAULT_CSS = """
        StatusBar {
            height: 1;
            background: #0b0f0e;
            color: #7a8a82;
            padding: 0 2;
        }
        """

    class BottomBar(Static):
        """Footer custom: ● ws://... · ↑↓ throughput · hotkeys · version"""
        DEFAULT_CSS = """
        BottomBar {
            height: 1;
            background: #0d1310;
            color: #46544c;
            padding: 0 2;
        }
        """

    class SystemMessage(Static):
        """Línea genérica del chat (help, status, errors, etc.)."""
        DEFAULT_CSS = """
        SystemMessage {
            height: auto;
            padding: 0;
            margin: 0;
            color: #d4dcd6;
        }
        """

    class GreetingCard(Static):
        """Tarjeta de bienvenida con border real (no Unicode)."""
        DEFAULT_CSS = """
        GreetingCard {
            height: auto;
            background: #0d1310;
            border: solid #1a2820;
            padding: 1 2;
            margin: 1 0 2 0;
            color: #d4dcd6;
        }
        """

    class UserMessage(Static):
        """Mensaje del usuario — border-left amber real."""
        DEFAULT_CSS = """
        UserMessage {
            height: auto;
            border-left: thick #f8e4a8;
            padding: 0 0 0 2;
            margin: 1 0 0 0;
            color: #d4dcd6;
        }
        """

    class ZephyroMessage(Static):
        """Mensaje de Zephyro — border-left verde real."""
        DEFAULT_CSS = """
        ZephyroMessage {
            height: auto;
            border-left: thick #2a8a48;
            padding: 0 0 0 2;
            margin: 1 0 0 0;
            color: #d4dcd6;
        }
        """

    class ThinkingWidget(Static):
        """Indicador de espera mientras Zephyro procesa — desaparece al llegar la respuesta."""
        DEFAULT_CSS = """
        ThinkingWidget {
            height: 1;
            border-left: thick #1a2820;
            padding: 0 0 0 2;
            margin: 1 0 0 0;
            color: #46544c;
        }
        """

    _THINKING_FRAMES = ["···", "·· ", "·  ", "   "]

    class ChatScroll(VerticalScroll):
        """Scroll del chat — implementa .write() para mantener compat con código previo (log.write)."""
        DEFAULT_CSS = """
        ChatScroll {
            height: 1fr;
            background: #07090a;
            padding: 1 2;
            scrollbar-background: #07090a;
            scrollbar-color: #1a2820;
            scrollbar-color-hover: #2a8a48;
        }
        """

        def write(self, content=None, **kwargs):
            try:
                if isinstance(content, Widget):
                    self.mount(content)
                elif content is None or content == "":
                    self.mount(SystemMessage(""))
                else:
                    self.mount(SystemMessage(str(content), markup=True))
                self.scroll_end(animate=False)
            except Exception:
                pass

        def clear(self):
            for w in list(self.children):
                try:
                    w.remove()
                except Exception:
                    pass

    class Sidebar(Vertical):
        """Sidebar derecha estilo dashboard Tauri."""
        DEFAULT_CSS = """
        Sidebar {
            width: 36;
            background: #0b0f0e;
            border-left: solid #1a2820;
            padding: 1 2;
            color: #7a8a82;
        }
        Sidebar > Static {
            height: auto;
            margin: 0 0 1 0;
        }
        """

    class JarvisTerminalApp(App):
        """jarvis-terminal — TUI principal."""

        TITLE = "ZEPHYRO"
        CSS = """
        Screen {
            background: #07090a;
            layers: base overlay;
        }
        TopBar {
            height: 1;
            background: #0d1310;
            color: #7a8a82;
            padding: 0 2;
            border-bottom: solid #1a2820;
        }
        #main {
            height: 1fr;
        }
        #chat-log {
            width: 1fr;
        }
        #sb-section {
            color: #5af78e;
            text-style: bold;
            padding: 0 0 0 0;
        }
        #sb-status, #sb-skills-list, #sb-events {
            color: #d4dcd6;
        }
        #verify-log {
            height: 6;
            background: #0b0f0e;
            color: #d4dcd6;
            border-top: solid #1a2820;
            padding: 0 2;
            display: none;
        }
        #verify-log.visible {
            display: block;
        }
        #info-panel {
            height: 14;
            background: #0d1310;
            color: #d4dcd6;
            border-top: solid #1a2820;
            padding: 0 2;
            display: none;
        }
        #info-panel.visible {
            display: block;
        }
        StatusBar {
            height: 1;
            background: #0b0f0e;
            color: #7a8a82;
            padding: 0 2;
            border-top: dashed #1a2820;
        }
        #input-wrap {
            height: auto;
            background: #0b0f0e;
            border: solid #1a2820;
            padding: 0 1;
            margin: 1 2;
        }
        #input-wrap:focus-within {
            border: solid #2a8a48;
        }
        #input-row {
            height: 1;
        }
        #input-hints {
            height: 1;
            background: #0b0f0e;
            color: #46544c;
            padding: 0 1;
        }
        Input {
            width: 1fr;
            background: #0b0f0e;
            color: #d4dcd6;
            border: none;
            padding: 0 0;
        }
        Input:focus {
            border: none;
        }
        #prompt-badge {
            width: 3;
            content-align: center middle;
            background: #0b0f0e;
            color: #5af78e;
            text-style: bold;
        }
        BottomBar {
            height: 1;
            background: #0d1310;
            color: #46544c;
            padding: 0 2;
            border-top: solid #1a2820;
        }
        Footer {
            display: none;
            height: 0;
        }
        ThinkingWidget {
            height: 1;
            border-left: thick #1a2820;
            padding: 0 0 0 2;
            margin: 1 0 0 0;
            color: #46544c;
        }
        Sidebar.collapsed {
            display: none;
        }
        #sb-pane-skills, #sb-pane-events {
            display: none;
        }
        #sb-pane-system, #sb-pane-skills, #sb-pane-events {
            height: auto;
        }
        #sb-pane-system.active, #sb-pane-skills.active, #sb-pane-events.active {
            display: block;
        }
        #sb-tab-header {
            height: 1;
            color: #5af78e;
            text-align: center;
            margin: 0 0 1 0;
        }
        """

        BINDINGS = [
            Binding("ctrl+l", "clear_log", "Limpiar", show=True),
            Binding("ctrl+v", "paste_clipboard", "Clipboard", show=True),
            Binding("f1", "toggle_events", "Eventos", show=True),
            Binding("f2", "toggle_verify", "Verify", show=True),
            Binding("f3", "capture_window", "Ventana", show=True),
            Binding("f4", "toggle_info", "Info", show=True),
            Binding("]", "toggle_sidebar", "Panel", show=True),
            Binding("[", "cycle_sidebar_tab", "Tab◀", show=False),
            Binding("\\", "cycle_sidebar_tab_rev", "Tab▶", show=False),
            Binding("ctrl+q", "quit", "Salir", show=True),
        ]

        def __init__(self, token: str):
            super().__init__()
            self.token = token
            self.client: Optional[ZephyroWsClient] = None
            self._show_all_events = False
            self._show_verify = False
            self._show_info = False
            self._pending_context: dict = {}
            self._current_mode = "auto"
            self._req_count = 0
            self._status_cache: dict = {}
            self._start_time = time.time()
            self._session_id = str(uuid.uuid4())[:8]
            self._recent_events: list[str] = []
            # Input history (↑/↓ navigation)
            self._input_history: list[str] = []
            self._history_pos: int = -1
            # Thinking indicator
            self._thinking_widget: Optional["JarvisTerminalApp.ThinkingWidget"] = None
            self._thinking_tick: int = 0
            # Sidebar state
            self._sidebar_visible: bool = True
            self._sidebar_tab: int = 0  # 0=system 1=skills 2=events
            # Browser tail subscription
            self._browser_tailing: bool = False
            # Last context snapshot (from context:update events)
            self._context_cache: dict = {}

        def compose(self) -> ComposeResult:
            yield TopBar("● ZEPHYRO  ·  …  ·  iniciando", id="top-bar")
            with Horizontal(id="main"):
                yield ChatScroll(id="chat-log")
                with Sidebar(id="sidebar"):
                    yield Static(
                        "[#46544c]◀[/#46544c] [bold #5af78e]SYSTEM[/bold #5af78e] [#46544c]▶[/#46544c]",
                        id="sb-tab-header", markup=True,
                    )
                    with Vertical(id="sb-pane-system"):
                        yield Static("[bold #5af78e]── SYSTEM[/bold #5af78e]", markup=True)
                        yield Static("[#46544c]cargando…[/#46544c]", id="sb-status", markup=True)
                    with Vertical(id="sb-pane-skills"):
                        yield Static("[bold #5af78e]── SKILLS[/bold #5af78e]", markup=True)
                        yield Static("[#46544c]cargando…[/#46544c]", id="sb-skills-list", markup=True)
                    with Vertical(id="sb-pane-events"):
                        yield Static("[bold #5af78e]── EVENTS[/bold #5af78e]", markup=True)
                        yield Static("[#46544c](sin eventos)[/#46544c]", id="sb-events", markup=True)
            yield RichLog(id="verify-log", markup=True)
            yield RichLog(id="info-panel", markup=True)
            yield StatusBar("", id="status-bar")
            with Vertical(id="input-wrap"):
                with Horizontal(id="input-row"):
                    yield Static(">", id="prompt-badge")
                    yield Input(placeholder="escribe, pega o usa / para comandos…")
                yield Static(
                    "[#46544c]↵ enviar  ·  / comandos  ·  ↑ historial  ·  Ctrl+V clipboard  ·  F1 eventos  F2 verify  F3 ventana  F4 info[/#46544c]",
                    id="input-hints",
                    markup=True,
                )
            yield BottomBar("", id="bottom-bar")
            yield Footer()

        async def on_mount(self) -> None:
            self.client = ZephyroWsClient(self.token)
            ok = await self.client.connect()
            log = self.query_one("#chat-log", ChatScroll)

            # GreetingCard widget — border real CSS, no Unicode
            if ok:
                greeting = GreetingCard(
                    f"[bold #5af78e]ZEPHYRO · v0.1.0[/bold #5af78e]\n"
                    f"[#d4dcd6]Hola, Bruno. jarvis-terminal listo.[/#d4dcd6]\n"
                    f"[#7a8a82]usa [#5af78e]/help[/#5af78e] para comandos · [#5af78e]Ctrl+V[/#5af78e] clipboard · [#5af78e]F4[/#5af78e] info panel[/#7a8a82]\n"
                    f"\n"
                    f"[#5af78e]●[/#5af78e] [#d4dcd6]backend[/#d4dcd6]  [#1a2820]·[/#1a2820]  "
                    f"[#5af78e]●[/#5af78e] [#d4dcd6]ws[/#d4dcd6]  [#1a2820]·[/#1a2820]  "
                    f"[#46544c]session #{self._session_id}[/#46544c]  [#1a2820]·[/#1a2820]  "
                    f"[#5af78e]all subsystems nominal[/#5af78e]",
                    markup=True,
                )
            else:
                greeting = GreetingCard(
                    f"[bold #5af78e]ZEPHYRO · v0.1.0[/bold #5af78e]\n"
                    f"[#ff7a7a]● sin conexión a {WS_URL}[/#ff7a7a]\n"
                    f"[#46544c]asegúrate de que el backend está corriendo[/#46544c]",
                    markup=True,
                )
            log.mount(greeting)

            if ok:
                await self._refresh_status_bar()
            else:
                self._update_status_display()

            asyncio.create_task(self._ws_receive_loop())
            self.set_interval(1, self._update_uptime)
            self.set_interval(0.4, self._tick_thinking)
            self.set_interval(8, self._refresh_sidebar)
            self._update_bottom_bar()
            asyncio.create_task(self._refresh_sidebar())
            # Activate first sidebar pane
            self.query_one("#sb-pane-system").set_class(True, "active")

        def _push_event(self, line: str) -> None:
            """Añade un evento al sidebar (mantén últimos 8)."""
            self._recent_events.insert(0, line)
            self._recent_events = self._recent_events[:8]
            try:
                self.query_one("#sb-events", Static).update("\n".join(self._recent_events) or "[#46544c](sin eventos)[/#46544c]")
            except Exception:
                pass

        async def _refresh_sidebar(self) -> None:
            """Refresca el sidebar derecho con status del backend + skills."""
            data = await asyncio.to_thread(self._sync_api_get, "/status")
            try:
                sb_status = self.query_one("#sb-status", Static)
            except Exception:
                return
            if "error" in data:
                sb_status.update(f"[#ff7a7a]{data['error'][:32]}[/#ff7a7a]")
            else:
                self._status_cache = data
                models = data.get("models", data.get("llm", {}))
                role = data.get("user", {}).get("role", "?")
                role_color = "#5af78e" if role == "admin" else "#f5c842"
                provider = models.get("cloud_provider") or "N/A"
                model = (models.get("anthropic_model") or "N/A").replace("claude-", "")
                stt = data.get("stt_engine", "?")
                tts = data.get("tts_engine", "?")
                online_dot = "[#5af78e]●[/#5af78e]" if models.get("is_online") else "[#ff7a7a]●[/#ff7a7a]"
                ws_dot = "[#5af78e]●[/#5af78e]" if (self.client and self.client._connected) else "[#ff7a7a]●[/#ff7a7a]"
                sb_status.update(
                    f"[#7a8a82]role    [/#7a8a82] [{role_color}]{role}[/{role_color}]\n"
                    f"[#7a8a82]net     [/#7a8a82] {online_dot}\n"
                    f"[#7a8a82]ws      [/#7a8a82] {ws_dot}\n"
                    f"[#7a8a82]provider[/#7a8a82] [#d4dcd6]{provider}[/#d4dcd6]\n"
                    f"[#7a8a82]model   [/#7a8a82] [#d4dcd6]{model}[/#d4dcd6]\n"
                    f"[#7a8a82]stt     [/#7a8a82] [#d4dcd6]{stt}[/#d4dcd6]\n"
                    f"[#7a8a82]tts     [/#7a8a82] [#d4dcd6]{tts}[/#d4dcd6]\n"
                    f"[#7a8a82]mode    [/#7a8a82] [#d4dcd6]{self._current_mode}[/#d4dcd6]\n"
                    f"[#7a8a82]req     [/#7a8a82] [#d4dcd6]{self._req_count}[/#d4dcd6]"
                )

            sdata = await asyncio.to_thread(self._sync_api_get, "/skills")
            try:
                sb_skills = self.query_one("#sb-skills-list", Static)
            except Exception:
                return
            if "error" in sdata:
                sb_skills.update(f"[#46544c]error[/#46544c]")
            else:
                skills = sdata if isinstance(sdata, list) else sdata.get("skills", [])
                lines = []
                enabled = [s.get("name", "?") for s in skills if s.get("enabled", True)]
                disabled = [s.get("name", "?") for s in skills if not s.get("enabled", True)]
                for n in enabled[:12]:
                    lines.append(f"[#5af78e]●[/#5af78e] [#d4dcd6]{n}[/#d4dcd6]")
                for n in disabled[:4]:
                    lines.append(f"[#46544c]○ {n}[/#46544c]")
                sb_skills.update("\n".join(lines) or "[#46544c]none[/#46544c]")

        def _update_bottom_bar(self) -> None:
            connected = self.client and self.client._connected
            dot = "[#5af78e]●[/#5af78e]" if connected else "[#ff7a7a]●[/#ff7a7a]"
            sb_icon = "[#5af78e]▐[/#5af78e]" if self._sidebar_visible else "[#46544c]▐[/#46544c]"
            sep = "  [#1a2820]│[/#1a2820]  "
            try:
                self.query_one("#bottom-bar", BottomBar).update(
                    f"{dot} [#7a8a82]{WS_URL}[/#7a8a82]"
                    f"   "
                    f"{sep}[#7a8a82]F1[/#7a8a82] [#46544c]eventos[/#46544c]"
                    f"{sep}[#7a8a82]F2[/#7a8a82] [#46544c]verify[/#46544c]"
                    f"{sep}[#7a8a82]F4[/#7a8a82] [#46544c]info[/#46544c]"
                    f"{sep}[#7a8a82]][/#7a8a82] {sb_icon}[#46544c]panel[/#46544c]"
                    f"{sep}[#7a8a82][[/#7a8a82] [#46544c]tab[/#46544c]"
                    f"{sep}[#7a8a82]↑↓[/#7a8a82] [#46544c]historial[/#46544c]"
                    f"{sep}[#7a8a82]^V[/#7a8a82] [#46544c]clip[/#46544c]"
                    f"{sep}[#7a8a82]^Q[/#7a8a82] [#46544c]salir[/#46544c]"
                    f"{sep}[#46544c]v0.1.0[/#46544c]"
                )
            except Exception:
                pass

        def _update_uptime(self) -> None:
            elapsed = int(time.time() - self._start_time)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            uptime = f"{h:02d}:{m:02d}:{s:02d}"
            models = self._status_cache.get("models", {})
            model = (models.get("anthropic_model") or "auto").replace("claude-", "")
            provider = models.get("cloud_provider") or "auto"
            connected = self.client and self.client._connected
            dot_color = "#5af78e" if connected else "#ff7a7a"
            ws_label = "ws" if connected else "offline"
            try:
                self.query_one("#top-bar", TopBar).update(
                    f"[{dot_color}]●[/{dot_color}]  "
                    f"[bold #5af78e]ZEPHYRO[/bold #5af78e]"
                    f"  [#2a8a48]·[/#2a8a48]  "
                    f"[#7a8a82]model[/#7a8a82] [#d4dcd6]{model}[/#d4dcd6]"
                    f"  [#2a8a48]·[/#2a8a48]  "
                    f"[#7a8a82]provider[/#7a8a82] [#d4dcd6]{provider}[/#d4dcd6]"
                    f"  [#2a8a48]·[/#2a8a48]  "
                    f"[#7a8a82]session[/#7a8a82] [#d4dcd6]#{self._session_id}[/#d4dcd6]"
                    f"  [#2a8a48]·[/#2a8a48]  "
                    f"[#7a8a82]uptime[/#7a8a82] [#d4dcd6]{uptime}[/#d4dcd6]"
                    f"  [#2a8a48]·[/#2a8a48]  "
                    f"[{dot_color}]●[/{dot_color}] [#d4dcd6]{ws_label}[/#d4dcd6]"
                )
            except Exception:
                pass
            self._update_bottom_bar()

        async def _ws_receive_loop(self) -> None:
            """Escucha TODOS los eventos del backend, no solo respuestas propias."""
            log = self.query_one("#chat-log", ChatScroll)
            verify_log = self.query_one("#verify-log", RichLog)
            while True:
                if not self.client or not self.client._connected:
                    await asyncio.sleep(2)
                    ok = await self.client.connect()
                    if ok:
                        log.write("[#5af78e]  ↩ reconectado[/#5af78e]")
                    continue

                msg = await self.client.receive()
                if msg is None:
                    await asyncio.sleep(0.1)
                    continue

                mtype = msg.get("type", "")

                if mtype == "response":
                    if self._thinking_widget:
                        try:
                            self._thinking_widget.remove()
                        except Exception:
                            pass
                        self._thinking_widget = None
                    response = msg.get("response", "")
                    elapsed = msg.get("elapsed_time", 0)
                    tier    = msg.get("tier")
                    skill   = msg.get("skill_used", "")
                    mode    = msg.get("mode_used", "")
                    ts      = datetime.now().strftime("%H:%M:%S")

                    tier_str = f"T{tier}" if tier else (mode or "auto")
                    meta_parts = [tier_str]
                    if skill:
                        meta_parts.append(skill)
                    if elapsed:
                        meta_parts.append(f"{elapsed:.2f}s")
                    meta_str = " · ".join(meta_parts)

                    content = (
                        f"[bold #5af78e]⟨ zephyro ⟩[/bold #5af78e]"
                        f"  [#46544c]{ts}[/#46544c]"
                        f"  [#46544c]{meta_str}[/#46544c]\n"
                        f"[#d4dcd6]{response}[/#d4dcd6]"
                    )
                    log.mount(ZephyroMessage(content, markup=True))
                    log.scroll_end(animate=False)
                    self._req_count += 1
                    self._update_status_display()
                    # Eventos recientes en sidebar
                    self._push_event(f"[#5af78e]●[/#5af78e] [#7a8a82]{ts}[/#7a8a82] {tier_str}" + (f" · {skill}" if skill else ""))

                elif mtype == "chunk":
                    log.write(msg.get("content", ""), end="")

                elif mtype == "typing":
                    if self._show_all_events:
                        log.write(f"[#46544c]  … {msg.get('message', '...')}[/#46544c]")

                elif mtype == "status":
                    if self._show_all_events:
                        log.write(f"[#46544c]  → {msg.get('action', '')} {msg.get('message', '')}[/#46544c]")

                elif mtype == "verification":
                    verdict = msg.get("verdict", "")
                    skill_v = msg.get("skill", "")
                    verify_log.write(f"[#5af78e]verify[/#5af78e] [#46544c]{skill_v}[/#46544c]  {verdict}")
                    if self._show_verify:
                        log.write(f"[#46544c]  verify [{skill_v}]: {verdict}[/#46544c]")

                elif mtype == "error":
                    if self._thinking_widget:
                        try:
                            self._thinking_widget.remove()
                        except Exception:
                            pass
                        self._thinking_widget = None
                    log.write(f"[#ff7a7a]  error: {msg.get('error', '?')}[/#ff7a7a]")

                elif mtype == "context:update":
                    self._context_cache = msg
                    proj = msg.get("active_project") or "?"
                    win = (msg.get("active_window") or "")[:40]
                    origin = msg.get("detected_by", "auto")
                    ts_now = datetime.now().strftime("%H:%M:%S")
                    badge = "[#5af78e]auto[/#5af78e]" if origin == "auto" else "[#f5c842]manual[/#f5c842]"
                    self._push_event(f"[#5af78e]●[/#5af78e] [#7a8a82]{ts_now}[/#7a8a82] ctx [{badge}] [#d4dcd6]{proj}[/#d4dcd6]")

                elif mtype == "app:launched":
                    app = msg.get("app", "?")
                    verdict = msg.get("verdict", "unknown")
                    pid = msg.get("pid")
                    already = msg.get("already_running", False)
                    if verdict == "ok":
                        dot = "[#5af78e]●[/#5af78e]"
                        color = "#5af78e"
                    elif verdict == "fail":
                        dot = "[#ff7a7a]●[/#ff7a7a]"
                        color = "#ff7a7a"
                    else:
                        dot = "[#f5c842]●[/#f5c842]"
                        color = "#f5c842"
                    pid_str = f" pid={pid}" if pid else ""
                    suffix = " (ya abierto)" if already else ""
                    ts_now = datetime.now().strftime("%H:%M:%S")
                    self._push_event(f"{dot} [#7a8a82]{ts_now}[/#7a8a82] [{color}]{app}[/{color}] {verdict}{pid_str}{suffix}")

                elif mtype == "browser:log":
                    line = msg.get("line", "")
                    safe = line.replace("[", "\\[").replace("]", "\\]")
                    log.write(f"[#46544c]│[/#46544c] [#7a8a82]{safe[:200]}[/#7a8a82]")

                elif mtype == "browser:status":
                    healthy = msg.get("healthy", False)
                    state = msg.get("state", "?")
                    dot = "[#5af78e]●[/#5af78e]" if healthy else "[#ff7a7a]●[/#ff7a7a]"
                    self._push_event(f"{dot} [#7a8a82]{datetime.now().strftime('%H:%M:%S')}[/#7a8a82] browser {state}")

                elif mtype in ("browser:subscribed", "browser:unsubscribed"):
                    pass  # confirmaciones silenciosas

                elif mtype in ("diag:approval_request", "diag:hypothesis"):
                    hyp = msg.get("hypothesis", {})
                    log.write(f"\n[bold #f5c842]DiagnosticAgent[/bold #f5c842]")
                    log.write(f"  [#d4dcd6]Causa: {hyp.get('causa', '?')}[/#d4dcd6]")
                    log.write(f"  [#46544c]Confianza: {hyp.get('confianza', '?')}%[/#46544c]")
                    log.write(f"  [#46544c]Responde en Telegram o usa el dashboard[/#46544c]")

                elif self._show_all_events and mtype not in ("ack",):
                    log.write(f"[#46544c]  {mtype}: {str(msg)[:120]}[/#46544c]")

        # ── HTTP helpers (síncronos → usar con asyncio.to_thread) ────────────

        def _sync_api_get(self, path: str, **params) -> dict:
            import urllib.request
            import urllib.parse
            import urllib.error
            p = {"token": self.token}
            p.update(params)
            url = f"{HTTP_BASE}/api{path}?" + urllib.parse.urlencode(p)
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    return {"error": "403 forbidden — necesita sesión admin (borra ~/.zephyro/cli_token.txt y reinicia)"}
                if e.code == 401:
                    return {"error": "401 unauthorized — token expirado o inválido"}
                return {"error": f"HTTP {e.code}: {e.reason}"}
            except Exception as e:
                return {"error": str(e)}

        def _sync_api_post(self, path: str, params: dict | None = None) -> dict:
            import urllib.request
            import urllib.parse
            import urllib.error
            p = {"token": self.token}
            if params:
                p.update(params)
            url = f"{HTTP_BASE}/api{path}?" + urllib.parse.urlencode(p)
            req = urllib.request.Request(
                url, data=b"", method="POST",
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    return {"error": "403 forbidden — necesita sesión admin (borra ~/.zephyro/cli_token.txt y reinicia)"}
                if e.code == 401:
                    return {"error": "401 unauthorized — token expirado o inválido"}
                try:
                    body = json.loads(e.read())
                    return {"error": body.get("detail", f"HTTP {e.code}")}
                except Exception:
                    return {"error": f"HTTP {e.code}: {e.reason}"}
            except Exception as e:
                return {"error": str(e)}

        # ── Status bar ────────────────────────────────────────────────────────

        def _render_status_text(self) -> str:
            connected = self.client and self.client._connected
            ws_dot = "[#5af78e]●[/#5af78e]" if connected else "[#ff7a7a]●[/#ff7a7a]"
            ws_label = "[#d4dcd6]ws ok[/#d4dcd6]" if connected else "[#ff7a7a]offline[/#ff7a7a]"
            mode_color = {"local": "#5af78e", "cloud": "#7ad7ff", "auto": "#b3ffc9"}.get(self._current_mode, "#5af78e")
            mode_chip = f"[#2a8a48]●[/#2a8a48] [#7a8a82]mode[/#7a8a82] [{mode_color}]{self._current_mode}[/{mode_color}]"
            sep = "  [#1a2820]·[/#1a2820]  "

            left_parts = [f"{ws_dot} {ws_label}", mode_chip]
            if self._status_cache:
                stt = self._status_cache.get("stt_engine", "local")
                tts = self._status_cache.get("tts_engine", "local")
                left_parts.append(f"[#7a8a82]stt[/#7a8a82] [#d4dcd6]{stt}[/#d4dcd6]  [#7a8a82]tts[/#7a8a82] [#d4dcd6]{tts}[/#d4dcd6]")
            left = sep.join(left_parts)

            right = f"[#7a8a82]req[/#7a8a82] [#d4dcd6]{self._req_count}[/#d4dcd6]"
            # spacer to push right side toward terminal edge (Textual centers if too short)
            return f" {left}     {right}"

        def _update_status_display(self) -> None:
            try:
                self.query_one("#status-bar", StatusBar).update(self._render_status_text())
            except Exception:
                pass

        async def _refresh_status_bar(self) -> None:
            """Llama a GET /status, actualiza cache y refresca la barra."""
            data = await asyncio.to_thread(self._sync_api_get, "/status")
            if "error" not in data:
                self._status_cache = data
            self._update_status_display()

        # ── Info panel (F4) ───────────────────────────────────────────────────

        async def _populate_info_panel(self) -> None:
            panel = self.query_one("#info-panel", RichLog)
            panel.clear()
            panel.write(f"[bold #5af78e]─── providers[/bold #5af78e]")
            pdata = await asyncio.to_thread(self._sync_api_get, "/providers")
            if "error" in pdata:
                panel.write(f"[#ff7a7a]{pdata['error']}[/#ff7a7a]")
            else:
                task_assign = pdata.get("task_assignment", {})
                chat_provider = task_assign.get("chat", {}).get("provider", "?")
                for p in pdata.get("providers", []):
                    dot = "[#5af78e]●[/#5af78e]" if p.get("enabled") else "[#46544c]○[/#46544c]"
                    name = p.get("name", "")
                    model = (p.get("default_model") or "").replace("claude-", "")
                    tag = "  [#7ad7ff]← chat[/#7ad7ff]" if name == chat_provider else ""
                    ok_mark = " [#5af78e]ok[/#5af78e]" if p.get("last_test_ok") else ""
                    panel.write(f"  {dot}  [#7a8a82]{name:<14}[/#7a8a82]  [#46544c]{model}[/#46544c]{ok_mark}{tag}")

            panel.write(f"[bold #5af78e]─── engines[/bold #5af78e]")
            st = self._status_cache
            if st:
                panel.write(f"  [#46544c]stt[/#46544c]  [#7a8a82]{st.get('stt_engine', '?')}[/#7a8a82]   [#46544c]tts[/#46544c]  [#7a8a82]{st.get('tts_engine', '?')}[/#7a8a82]")
                panel.write(f"  [#46544c]/stt local|groq   /tts local|fishaudio[/#46544c]")
            else:
                panel.write("  [#46544c](ejecuta /status primero)[/#46544c]")

            panel.write(f"[bold #5af78e]─── skills[/bold #5af78e]")
            sdata = await asyncio.to_thread(self._sync_api_get, "/skills")
            if "error" in sdata:
                panel.write(f"[#ff7a7a]{sdata['error']}[/#ff7a7a]")
            else:
                skills = sdata if isinstance(sdata, list) else sdata.get("skills", [])
                enabled = [s for s in skills if s.get("enabled", True)]
                disabled = [s for s in skills if not s.get("enabled", True)]
                names = "  " + "  ".join(f"[#5af78e]{s.get('name','?')}[/#5af78e]" for s in enabled)
                panel.write(names or "  [#46544c](ninguno)[/#46544c]")
                if disabled:
                    dnames = "  ".join(f"[#46544c]{s.get('name','?')}[/#46544c]" for s in disabled)
                    panel.write(f"  [#46544c]off: {dnames}[/#46544c]")

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            if not text:
                return
            event.input.value = ""

            # Track input history
            if not self._input_history or self._input_history[0] != text:
                self._input_history.insert(0, text)
                self._input_history = self._input_history[:50]
            self._history_pos = -1

            log = self.query_one("#chat-log", ChatScroll)

            # Comandos internos del CLI
            if text.startswith("/"):
                await self._handle_cli_command(text, log)
                return

            # Añadir contexto pendiente (clipboard, ventana)
            context = dict(self._pending_context)
            self._pending_context.clear()

            ts = datetime.now().strftime("%H:%M:%S")
            content = (
                f"[bold #f8e4a8]> bruno[/bold #f8e4a8]  [#46544c]{ts}[/#46544c]\n"
                f"[#d4dcd6]{text}[/#d4dcd6]"
            )
            if context:
                ctx_preview = ", ".join(f"{k}: {str(v)[:50]}" for k, v in context.items())
                content += f"\n[#46544c]+ {ctx_preview}[/#46544c]"
            log.mount(UserMessage(content, markup=True))
            log.scroll_end(animate=False)

            if not self.client or not self.client._connected:
                log.write("[red]✗ Sin conexión al backend[/red]")
                return

            await self.client.send_chat(text, mode=self._current_mode, context=context or None)
            _append_history(text, "", {"mode": self._current_mode})

            # Mount thinking indicator
            tw = JarvisTerminalApp.ThinkingWidget(
                "[bold #5af78e]⟨ zephyro ⟩[/bold #5af78e]  [#46544c]pensando ···[/#46544c]",
                markup=True,
            )
            self._thinking_widget = tw
            self._thinking_tick = 0
            log.mount(tw)
            log.scroll_end(animate=False)

        async def _handle_cli_command(self, cmd: str, log: RichLog) -> None:
            parts = cmd.split()
            command = parts[0].lower()

            if command == "/help":
                log.write(f"[bold #5af78e]─── chat[/bold #5af78e]")
                log.write(f"  [#7a8a82]/mode local|cloud|auto[/#7a8a82]  [#46544c]cambiar modo LLM[/#46544c]")
                log.write(f"  [#7a8a82]/clear[/#7a8a82]                 [#46544c]limpiar pantalla[/#46544c]")
                log.write(f"  [#7a8a82]/history [N][/#7a8a82]           [#46544c]últimas N interacciones[/#46544c]")
                log.write(f"[bold #5af78e]─── config[/bold #5af78e]")
                log.write(f"  [#7a8a82]/provider [name][/#7a8a82]       [#46544c]ver/cambiar cloud provider[/#46544c]")
                log.write(f"  [#7a8a82]/model [name][/#7a8a82]          [#46544c]ver/cambiar modelo  haiku|sonnet|opus[/#46544c]")
                log.write(f"  [#7a8a82]/providers[/#7a8a82]             [#46544c]listar todos los providers[/#46544c]")
                log.write(f"  [#7a8a82]/tts local|fishaudio[/#7a8a82]   [#46544c]cambiar engine TTS[/#46544c]")
                log.write(f"  [#7a8a82]/stt local|groq[/#7a8a82]        [#46544c]cambiar engine STT[/#46544c]")
                log.write(f"[bold #5af78e]─── sistema[/bold #5af78e]")
                log.write(f"  [#7a8a82]/status[/#7a8a82]                [#46544c]estado del backend[/#46544c]")
                log.write(f"  [#7a8a82]/introspect[/#7a8a82]            [#46544c]problemas activos[/#46544c]")
                log.write(f"  [#7a8a82]/skills[/#7a8a82]                [#46544c]listar skills[/#46544c]")
                log.write(f"  [#7a8a82]/skill enable|disable NAME[/#7a8a82]")
                log.write(f"  [#7a8a82]/browser [status|kill|restart|logs N|tail|untail][/#7a8a82]   [#46544c]control playwright[/#46544c]")
                log.write(f"  [#7a8a82]/context [workspace NAME][/#7a8a82]   [#46544c]contexto detectado / override[/#46544c]")
                log.write(f"[bold #5af78e]─── herramientas (estilo telegram)[/bold #5af78e]")
                log.write(f"  [#7a8a82]/search QUERY[/#7a8a82]      [#46544c]búsqueda web (tavily/duckduckgo)[/#46544c]")
                log.write(f"  [#7a8a82]/reflect[/#7a8a82]           [#46544c]reflexión introspectiva[/#46544c]")
                log.write(f"  [#7a8a82]/bugs[/#7a8a82]              [#46544c]bugs reportados[/#46544c]")
                log.write(f"  [#7a8a82]/diagnose [N][/#7a8a82]      [#46544c]diagnóstico automático de errores[/#46544c]")
                log.write(f"  [#7a8a82]/living [tier][/#7a8a82]     [#46544c]estado interno del sistema[/#46544c]")
                log.write(f"  [#7a8a82]/specialty[/#7a8a82]         [#46544c]ver/cambiar especialidad activa[/#46544c]")
                log.write(f"  [#7a8a82]/perfil[/#7a8a82]            [#46544c]tu perfil de usuario[/#46544c]")
                log.write(f"  [#7a8a82]/resumenes[/#7a8a82]         [#46544c]resúmenes de conversación[/#46544c]")
                log.write(f"  [#7a8a82]/capacidades[/#7a8a82]       [#46544c]capacidades actuales[/#46544c]")
                log.write(f"  [#7a8a82]/limitaciones[/#7a8a82]      [#46544c]limitaciones conocidas[/#46544c]")
                log.write(f"  [#7a8a82]/system /ram /cpu /disk /red[/#7a8a82]  [#46544c]monitoreo[/#46544c]")
                log.write(f"  [#7a8a82]/hoy /agenda /semana /manana[/#7a8a82]   [#46544c]agenda[/#46544c]")
                log.write(f"[bold #5af78e]─── ui[/bold #5af78e]")
                log.write(f"  [#7a8a82]F1[/#7a8a82]  [#46544c]eventos  [/#46544c]  [#7a8a82]F2[/#7a8a82]  [#46544c]verify  [/#46544c]  [#7a8a82]F3[/#7a8a82]  [#46544c]ventana  [/#46544c]  [#7a8a82]F4[/#7a8a82]  [#46544c]info[/#46544c]")
                log.write(f"  [#7a8a82]Ctrl+V[/#7a8a82]  [#46544c]clipboard  [/#46544c]  [#7a8a82]Ctrl+L[/#7a8a82]  [#46544c]limpiar  [/#46544c]  [#7a8a82]Ctrl+Q[/#7a8a82]  [#46544c]salir[/#46544c]")

            elif command == "/mode":
                if len(parts) > 1 and parts[1] in ("local", "cloud", "auto"):
                    self._current_mode = parts[1]
                    self._update_status_display()
                    log.write(f"[#5af78e]modo: {parts[1]}[/#5af78e]")
                else:
                    log.write(f"[#7a8a82]modo actual: [bold]{self._current_mode}[/bold]  (opciones: local|cloud|auto)[/#7a8a82]")

            elif command == "/status":
                data = await asyncio.to_thread(self._sync_api_get, "/status")
                if "error" in data:
                    log.write(f"[#ff7a7a]error: {data['error']}[/#ff7a7a]")
                else:
                    self._status_cache = data
                    models = data.get("models", data.get("llm", {}))
                    role = data.get("user", {}).get("role", "?")
                    role_color = "#5af78e" if role == "admin" else "#f5c842"
                    online = "[#5af78e]sí[/#5af78e]" if models.get("is_online") else "[#ff7a7a]no[/#ff7a7a]"
                    log.write(f"[#5af78e]─── backend status[/#5af78e]")
                    log.write(f"  [#46544c]sesión  [/#46544c][{role_color}]{role}[/{role_color}]")
                    log.write(f"  [#46544c]online  [/#46544c]{online}")
                    log.write(f"  [#46544c]provider[/#46544c]  [#7a8a82]{models.get('cloud_provider') or 'N/A'}[/#7a8a82]")
                    log.write(f"  [#46544c]modelo  [/#46544c]  [#7a8a82]{models.get('anthropic_model') or 'N/A'}[/#7a8a82]")
                    log.write(f"  [#46544c]stt     [/#46544c]  [#7a8a82]{data.get('stt_engine', '?')}[/#7a8a82]")
                    log.write(f"  [#46544c]tts     [/#46544c]  [#7a8a82]{data.get('tts_engine', '?')}[/#7a8a82]")
                    if role != "admin":
                        log.write(f"[#f5c842]  sesión guest — reinicia zephyro e ingresa la contraseña admin[/#f5c842]")
                    self._update_status_display()

            elif command == "/skills":
                data = await asyncio.to_thread(self._sync_api_get, "/skills")
                if "error" in data:
                    log.write(f"[#ff7a7a]error: {data['error']}[/#ff7a7a]")
                else:
                    skills = data if isinstance(data, list) else data.get("skills", [])
                    log.write(f"[#5af78e]─── skills ({len(skills)})[/#5af78e]")
                    for s in skills:
                        dot = "[#5af78e]●[/#5af78e]" if s.get("enabled", True) else "[#46544c]○[/#46544c]"
                        log.write(f"  {dot}  [#7a8a82]{s.get('name','?'):<18}[/#7a8a82]  [#46544c]{s.get('description','')[:50]}[/#46544c]")

            # ── Nuevos comandos de configuración ─────────────────────────

            elif command == "/provider":
                if len(parts) > 1:
                    name = parts[1]
                    result = await asyncio.to_thread(self._sync_api_post, "/settings/provider", {"provider": name})
                    if result.get("success"):
                        log.write(f"[#5af78e]provider → {name}[/#5af78e]")
                        await self._refresh_status_bar()
                    else:
                        log.write(f"[#ff7a7a]{result.get('detail', result.get('error', '?'))}[/#ff7a7a]")
                        log.write("[#46544c]  opciones: gemini, openai, anthropic, deepseek, auto[/#46544c]")
                else:
                    await self._refresh_status_bar()
                    models = self._status_cache.get("models", self._status_cache.get("llm", {}))
                    log.write(f"[#7a8a82]provider actual: {models.get('cloud_provider', '?')} · {models.get('anthropic_model', '?')}[/#7a8a82]")
                    log.write("[#46544c]  cambiar: /provider [gemini|openai|anthropic|deepseek|auto][/#46544c]")

            elif command == "/model":
                from core.model_catalog import ANTHROPIC_ALIASES as _MODELS
                if len(parts) > 1:
                    name = _MODELS.get(parts[1], parts[1])
                    result = await asyncio.to_thread(self._sync_api_post, "/settings/anthropic-model", {"model": name})
                    if result.get("success"):
                        log.write(f"[#5af78e]modelo → {name}[/#5af78e]")
                        await self._refresh_status_bar()
                    else:
                        log.write(f"[#ff7a7a]{result.get('detail', result.get('error', '?'))}[/#ff7a7a]")
                else:
                    log.write(f"[#5af78e]─── modelos anthropic[/#5af78e]")
                    log.write(f"  [#7a8a82]haiku [/#7a8a82]  [#46544c]{_MODELS['haiku'].replace('claude-', ''):<13} rápido, económico[/#46544c]")
                    log.write(f"  [#7a8a82]sonnet[/#7a8a82]  [#46544c]{_MODELS['sonnet'].replace('claude-', ''):<13} balanceado, default[/#46544c]")
                    log.write(f"  [#7a8a82]opus  [/#7a8a82]  [#46544c]{_MODELS['opus'].replace('claude-', ''):<13} máxima capacidad[/#46544c]")
                    log.write(f"  [#46544c]usar: /model haiku | /model sonnet | /model opus[/#46544c]")

            elif command == "/providers":
                data = await asyncio.to_thread(self._sync_api_get, "/providers")
                if "error" in data:
                    log.write(f"[#ff7a7a]error: {data['error']}[/#ff7a7a]")
                else:
                    task_assign = data.get("task_assignment", {})
                    chat_prov = task_assign.get("chat", {}).get("provider", "?")
                    log.write(f"[#5af78e]─── providers[/#5af78e]  [#46544c]chat→{chat_prov}[/#46544c]")
                    for p in data.get("providers", []):
                        dot = "[#5af78e]●[/#5af78e]" if p.get("enabled") else "[#46544c]○[/#46544c]"
                        model = (p.get("default_model") or "").replace("claude-", "")
                        ok = " [#5af78e]ok[/#5af78e]" if p.get("last_test_ok") else (" [#ff7a7a]err[/#ff7a7a]" if p.get("last_test_ok") is False else "")
                        tag = "  [#7ad7ff]← chat[/#7ad7ff]" if p.get("name") == chat_prov else ""
                        log.write(f"  {dot}  [#7a8a82]{p.get('name',''):<14}[/#7a8a82]  [#46544c]{model}[/#46544c]{ok}{tag}")

            elif command == "/tts":
                if len(parts) > 1 and parts[1] in ("local", "fishaudio"):
                    result = await asyncio.to_thread(self._sync_api_post, "/settings/tts-engine", {"engine": parts[1]})
                    if result.get("success"):
                        log.write(f"[#5af78e]tts → {parts[1]}[/#5af78e]")
                        await self._refresh_status_bar()
                    else:
                        log.write(f"[#ff7a7a]{result.get('detail', result.get('error', '?'))}[/#ff7a7a]")
                else:
                    tts = self._status_cache.get("tts_engine", "?")
                    log.write(f"[#7a8a82]tts actual: {tts}[/#7a8a82]  [#46544c]→ /tts local | /tts fishaudio[/#46544c]")

            elif command == "/stt":
                if len(parts) > 1 and parts[1] in ("local", "groq"):
                    result = await asyncio.to_thread(self._sync_api_post, "/settings/stt-engine", {"engine": parts[1]})
                    if result.get("success"):
                        log.write(f"[#5af78e]stt → {parts[1]}[/#5af78e]")
                        await self._refresh_status_bar()
                    else:
                        log.write(f"[#ff7a7a]{result.get('detail', result.get('error', '?'))}[/#ff7a7a]")
                else:
                    stt = self._status_cache.get("stt_engine", "?")
                    log.write(f"[#7a8a82]stt actual: {stt}[/#7a8a82]  [#46544c]→ /stt local | /stt groq[/#46544c]")

            elif command == "/introspect":
                data = await asyncio.to_thread(self._sync_api_get, "/self/introspect")
                if "error" in data:
                    log.write(f"[#ff7a7a]error: {data['error']}[/#ff7a7a]")
                else:
                    issues = data.get("issues", data.get("active_issues", []))
                    broken = data.get("broken_skills", [])
                    if not issues and not broken:
                        log.write("[#5af78e]  ● sistema sin problemas detectados[/#5af78e]")
                    else:
                        if issues:
                            log.write(f"[#f5c842]─── problemas activos ({len(issues)})[/#f5c842]")
                            for issue in issues:
                                log.write(f"  [#ff7a7a]●[/#ff7a7a]  [#d4dcd6]{issue}[/#d4dcd6]")
                        if broken:
                            log.write(f"[#ff7a7a]─── skills con errores[/#ff7a7a]")
                            for sk in broken:
                                log.write(f"  [#ff7a7a]○[/#ff7a7a]  [#d4dcd6]{sk}[/#d4dcd6]")

            elif command == "/skill":
                if len(parts) >= 3 and parts[1] in ("enable", "disable"):
                    action = parts[1]
                    name = parts[2]
                    result = await asyncio.to_thread(self._sync_api_post, f"/skills/{name}/{action}")
                    if "error" not in result:
                        log.write(f"[#5af78e]skill {name} → {action}d[/#5af78e]")
                    else:
                        log.write(f"[#ff7a7a]{result.get('error', result.get('detail', '?'))}[/#ff7a7a]")
                else:
                    log.write(f"[#7a8a82]/skill enable NAME[/#7a8a82]   [#46544c]habilitar skill[/#46544c]")
                    log.write(f"[#7a8a82]/skill disable NAME[/#7a8a82]  [#46544c]deshabilitar skill[/#46544c]")
                    log.write(f"[#7a8a82]/skills[/#7a8a82]              [#46544c]ver lista completa[/#46544c]")

            elif command == "/context":
                if len(parts) >= 3 and parts[1] == "workspace":
                    ws_name = " ".join(parts[2:])
                    result = await asyncio.to_thread(
                        self._sync_api_post, "/context/workspace", {"name": ws_name}
                    )
                    if result.get("success"):
                        ctx = result.get("context", {})
                        self._context_cache = ctx
                        log.write(f"[#5af78e]workspace → {ctx.get('active_project', ws_name)}[/#5af78e]  [#46544c](manual, 10min)[/#46544c]")
                    else:
                        log.write(f"[#ff7a7a]{result.get('detail', result.get('error', '?'))}[/#ff7a7a]")
                else:
                    data = await asyncio.to_thread(self._sync_api_get, "/context/current")
                    if "error" in data:
                        log.write(f"[#ff7a7a]error: {data['error']}[/#ff7a7a]")
                    else:
                        self._context_cache = data
                        win = data.get("active_window") or "(ninguna)"
                        proj = data.get("active_project") or "(sin proyecto)"
                        path = data.get("active_path") or "(sin path)"
                        url = data.get("current_url") or "(sin url)"
                        origin = data.get("detected_by", "?")
                        last = data.get("last_updated", "?")
                        log.write(f"[#5af78e]─── contexto[/#5af78e]")
                        log.write(f"  [#46544c]ventana   [/#46544c][#d4dcd6]{win[:60]}[/#d4dcd6]")
                        log.write(f"  [#46544c]proyecto  [/#46544c][#d4dcd6]{proj}[/#d4dcd6]")
                        log.write(f"  [#46544c]path      [/#46544c][#d4dcd6]{path}[/#d4dcd6]")
                        log.write(f"  [#46544c]url       [/#46544c][#d4dcd6]{url[:60]}[/#d4dcd6]")
                        log.write(f"  [#46544c]origen    [/#46544c][#d4dcd6]{origin}[/#d4dcd6]  [#46544c]{last[:19]}[/#46544c]")
                        log.write(f"  [#46544c]/context workspace NAME[/#46544c]  [#46544c]→ override manual[/#46544c]")

            elif command == "/browser":
                sub = parts[1] if len(parts) > 1 else "status"
                if sub == "status":
                    data = await asyncio.to_thread(self._sync_api_get, "/browser/status")
                    if "error" in data:
                        log.write(f"[#ff7a7a]error: {data['error']}[/#ff7a7a]")
                    elif not data.get("initialized"):
                        log.write("[#46544c]─── browser[/#46544c]")
                        log.write("  [#46544c]●  no inicializado todavía[/#46544c]")
                    else:
                        healthy = data.get("healthy", False)
                        dot = "[#5af78e]●[/#5af78e]" if healthy else "[#ff7a7a]●[/#ff7a7a]"
                        state = data.get("state", "?")
                        url = data.get("current_url") or "(sin pestaña activa)"
                        queue = data.get("queue", {})
                        log.write(f"[#5af78e]─── browser[/#5af78e]")
                        log.write(f"  [#46544c]status[/#46544c]   {dot} [#d4dcd6]{state}[/#d4dcd6]")
                        log.write(f"  [#46544c]url   [/#46544c]   [#d4dcd6]{url}[/#d4dcd6]")
                        log.write(f"  [#46544c]queue [/#46544c]   [#d4dcd6]{queue.get('queue_size', 0)} pendientes[/#d4dcd6]")
                elif sub == "kill":
                    result = await asyncio.to_thread(self._sync_api_post, "/browser/kill")
                    if "error" in result:
                        log.write(f"[#ff7a7a]{result.get('error')}[/#ff7a7a]")
                    else:
                        drained = result.get("drained", 0)
                        log.write(f"[#5af78e]browser cerrado[/#5af78e]  [#46544c]({drained} en cola descartados)[/#46544c]")
                elif sub == "restart":
                    log.write("[#46544c]reiniciando browser…[/#46544c]")
                    result = await asyncio.to_thread(self._sync_api_post, "/browser/restart")
                    if "error" in result:
                        log.write(f"[#ff7a7a]{result.get('error')}[/#ff7a7a]")
                    else:
                        sid = result.get("session_id", "?")
                        log.write(f"[#5af78e]browser reiniciado[/#5af78e]  [#46544c]session #{sid}[/#46544c]")
                elif sub == "logs":
                    n = 20
                    if len(parts) > 2:
                        try:
                            n = max(1, min(int(parts[2]), 500))
                        except ValueError:
                            pass
                    data = await asyncio.to_thread(self._sync_api_get, "/browser/logs", tail=n)
                    if "error" in data:
                        log.write(f"[#ff7a7a]error: {data['error']}[/#ff7a7a]")
                    else:
                        lines = data.get("lines", [])
                        log.write(f"[#5af78e]─── playwright.log (últimas {len(lines)})[/#5af78e]")
                        for line in lines:
                            # Escapar markup de Rich y truncar
                            safe = line.replace("[", "\\[").replace("]", "\\]")
                            log.write(f"  [#7a8a82]{safe[:200]}[/#7a8a82]")
                elif sub == "tail":
                    if self._browser_tailing:
                        log.write("[#46544c]ya estás suscrito al stream — usa /browser untail para parar[/#46544c]")
                    elif not self.client or not self.client._connected:
                        log.write("[#ff7a7a]sin conexión al backend[/#ff7a7a]")
                    else:
                        await self.client.ws.send(json.dumps({"type": "browser:subscribe", "token": self.token}))
                        self._browser_tailing = True
                        log.write("[#5af78e]● suscrito al stream de playwright.log[/#5af78e]  [#46544c]→ /browser untail para parar[/#46544c]")
                elif sub == "untail":
                    if not self._browser_tailing:
                        log.write("[#46544c]no estabas suscrito[/#46544c]")
                    elif not self.client or not self.client._connected:
                        log.write("[#ff7a7a]sin conexión al backend[/#ff7a7a]")
                    else:
                        await self.client.ws.send(json.dumps({"type": "browser:unsubscribe", "token": self.token}))
                        self._browser_tailing = False
                        log.write("[#46544c]● desuscrito del stream[/#46544c]")
                else:
                    log.write(f"[#5af78e]─── /browser[/#5af78e]")
                    log.write(f"  [#7a8a82]/browser[/#7a8a82]               [#46544c]status (default)[/#46544c]")
                    log.write(f"  [#7a8a82]/browser kill[/#7a8a82]          [#46544c]cerrar browser (admin)[/#46544c]")
                    log.write(f"  [#7a8a82]/browser restart[/#7a8a82]       [#46544c]reiniciar (admin)[/#46544c]")
                    log.write(f"  [#7a8a82]/browser logs [N][/#7a8a82]      [#46544c]últimas N líneas (default 20)[/#46544c]")
                    log.write(f"  [#7a8a82]/browser tail[/#7a8a82]          [#46544c]stream live de playwright.log[/#46544c]")
                    log.write(f"  [#7a8a82]/browser untail[/#7a8a82]        [#46544c]parar el stream[/#46544c]")

            # ── Comandos UI ───────────────────────────────────────────────

            elif command == "/verify":
                self._show_verify = not self._show_verify
                vl = self.query_one("#verify-log")
                vl.set_class(self._show_verify, "visible")
                state = "[#5af78e]on[/#5af78e]" if self._show_verify else "[#46544c]off[/#46544c]"
                log.write(f"[#46544c]verify panel: {state}[/#46544c]")

            elif command == "/events":
                self._show_all_events = not self._show_all_events
                state = "[#5af78e]on[/#5af78e]" if self._show_all_events else "[#46544c]off[/#46544c]"
                log.write(f"[#46544c]eventos verbosos: {state}[/#46544c]")

            elif command == "/context":
                clipboard = _get_clipboard_context()
                window = _get_active_window_title()
                cwd = os.getcwd()
                log.write(f"[#5af78e]─── contexto so[/#5af78e]")
                log.write(f"  [#46544c]clipboard  [/#46544c][#7a8a82]{clipboard[:100] or '(vacío)'}[/#7a8a82]")
                log.write(f"  [#46544c]ventana    [/#46544c][#7a8a82]{window or '(no disponible)'}[/#7a8a82]")
                log.write(f"  [#46544c]directorio [/#46544c][#7a8a82]{cwd}[/#7a8a82]")

            elif command == "/history":
                n = int(parts[1]) if len(parts) > 1 else 10
                try:
                    lines = HISTORY_FILE.read_text().strip().splitlines()
                    for line in lines[-n:]:
                        entry = json.loads(line)
                        ts = entry.get("ts", "")[:16]
                        inp = entry.get("input", "")[:60]
                        tier = entry.get("tier", entry.get("mode_used", "?"))
                        log.write(f"[#46544c]{ts}[/#46544c]  [#2a8a48]{tier}[/#2a8a48]  [#7a8a82]{inp}[/#7a8a82]")
                except FileNotFoundError:
                    log.write("[#46544c]sin historial todavía[/#46544c]")

            elif command == "/clear":
                log.clear()

            # ── Comandos pass-through al orchestrator (estilo Telegram) ───
            #   Estos se mandan tal cual al backend vía WebSocket — el router/skills
            #   los interpretan igual que en Telegram.
            elif command in (
                "/search", "/reflect", "/bugs", "/specialty", "/perfil",
                "/resumenes", "/capacidades", "/limitaciones", "/diagnose",
                "/living", "/system", "/ram", "/cpu", "/disk", "/red",
                "/hoy", "/agenda", "/semana", "/manana"
            ):
                if not self.client or not self.client._connected:
                    log.write("[#ff7a7a]sin conexión al backend[/#ff7a7a]")
                else:
                    # Reconstruye el texto: si el usuario tipeó "/search foo bar" se manda tal cual
                    msg_text = cmd
                    ts = datetime.now().strftime("%H:%M:%S")
                    log.write(f"[#f8e4a8]┤[/#f8e4a8] [bold #f8e4a8]bruno[/bold #f8e4a8]  [#46544c]{ts}[/#46544c]")
                    log.write(f"[#f8e4a8]{msg_text}[/#f8e4a8]")
                    log.write("")
                    await self.client.send_chat(msg_text, mode=self._current_mode)
                    _append_history(msg_text, "", {"mode": self._current_mode, "telegram_style": True})

            else:
                log.write(f"[#ff7a7a]comando desconocido: {command}  →  /help[/#ff7a7a]")

        async def action_clear_log(self) -> None:
            self.query_one("#chat-log", ChatScroll).clear()

        async def action_paste_clipboard(self) -> None:
            text = _get_clipboard_context()
            if text:
                self._pending_context["clipboard"] = text
                log = self.query_one("#chat-log", ChatScroll)
                log.write(f"[#46544c]  clipboard añadido como contexto ({len(text)} chars)[/#46544c]")
            else:
                self.query_one("#chat-log", ChatScroll).write("[#f5c842]  clipboard vacío[/#f5c842]")

        async def action_toggle_events(self) -> None:
            self._show_all_events = not self._show_all_events
            state = "[#5af78e]on[/#5af78e]" if self._show_all_events else "[#46544c]off[/#46544c]"
            self.query_one("#chat-log", ChatScroll).write(f"[#46544c]eventos verbosos: {state}[/#46544c]")

        async def action_toggle_verify(self) -> None:
            self._show_verify = not self._show_verify
            vl = self.query_one("#verify-log")
            vl.set_class(self._show_verify, "visible")

        async def action_capture_window(self) -> None:
            title = _get_active_window_title()
            if title:
                self._pending_context["active_window"] = title
                self.query_one("#chat-log", ChatScroll).write(
                    f"[#46544c]  ventana '{title}' añadida como contexto[/#46544c]"
                )
            else:
                self.query_one("#chat-log", ChatScroll).write("[#f5c842]  no se pudo capturar la ventana activa[/#f5c842]")

        async def action_toggle_info(self) -> None:
            self._show_info = not self._show_info
            panel = self.query_one("#info-panel")
            panel.set_class(self._show_info, "visible")
            if self._show_info:
                await self._populate_info_panel()

        # ── New actions: sidebar collapse + tabs ──────────────────────────────

        async def action_toggle_sidebar(self) -> None:
            self._sidebar_visible = not self._sidebar_visible
            self.query_one("#sidebar", Sidebar).set_class(not self._sidebar_visible, "collapsed")
            self._update_bottom_bar()

        _SIDEBAR_TABS = ["sb-pane-system", "sb-pane-skills", "sb-pane-events"]
        _SIDEBAR_TAB_LABELS = ["SYSTEM", "SKILLS", "EVENTS"]

        def _set_sidebar_tab(self, idx: int) -> None:
            old_id = self._SIDEBAR_TABS[self._sidebar_tab]
            self._sidebar_tab = idx % 3
            new_id = self._SIDEBAR_TABS[self._sidebar_tab]
            try:
                self.query_one(f"#{old_id}").set_class(False, "active")
                self.query_one(f"#{new_id}").set_class(True, "active")
                label = self._SIDEBAR_TAB_LABELS[self._sidebar_tab]
                self.query_one("#sb-tab-header", Static).update(
                    f"[#46544c]◀[/#46544c] [bold #5af78e]{label}[/bold #5af78e] [#46544c]▶[/#46544c]"
                )
            except Exception:
                pass

        async def action_cycle_sidebar_tab(self) -> None:
            self._set_sidebar_tab(self._sidebar_tab + 1)

        async def action_cycle_sidebar_tab_rev(self) -> None:
            self._set_sidebar_tab(self._sidebar_tab - 1)

        # ── Input history (↑/↓ in the Input widget) ──────────────────────────

        async def on_key(self, event) -> None:
            try:
                inp = self.query_one(Input)
            except Exception:
                return
            if not inp.has_focus:
                return
            if event.key == "up":
                if self._input_history:
                    self._history_pos = min(
                        self._history_pos + 1, len(self._input_history) - 1
                    )
                    inp.value = self._input_history[self._history_pos]
                    inp.cursor_position = len(inp.value)
                    event.prevent_default()
                    event.stop()
            elif event.key == "down":
                if self._history_pos > 0:
                    self._history_pos -= 1
                    inp.value = self._input_history[self._history_pos]
                    inp.cursor_position = len(inp.value)
                    event.prevent_default()
                    event.stop()
                elif self._history_pos == 0:
                    self._history_pos = -1
                    inp.value = ""
                    event.prevent_default()
                    event.stop()

        # ── ThinkingWidget animation ──────────────────────────────────────────

        def _tick_thinking(self) -> None:
            if self._thinking_widget is None:
                return
            self._thinking_tick = (self._thinking_tick + 1) % 4
            dots = _THINKING_FRAMES[self._thinking_tick]
            try:
                self._thinking_widget.update(
                    f"[bold #5af78e]⟨ zephyro ⟩[/bold #5af78e]  [#46544c]pensando {dots}[/#46544c]"
                )
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not _HAS_WS:
        print("ERROR: Falta 'websockets'. Instalar: pip install websockets", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="jarvis-terminal — CLI para Zephyro AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python cli.py                          # TUI interactiva
  python cli.py --once "qué hora es"     # one-shot, imprime respuesta y sale
  python cli.py --skill git-ops "status" # invoca skill directamente
  git diff | python cli.py --pipe "explica este diff"
  python cli.py --once "resume esto" --output resumen.txt
        """,
    )
    parser.add_argument("--version", action="version", version="zephyro 0.1.0")
    parser.add_argument("--once", metavar="MSG",   help="Envía un mensaje y sale")
    parser.add_argument("--pipe", metavar="PROMPT",help="Lee stdin y lo adjunta como contexto")
    parser.add_argument("--skill", metavar="NAME", help="Invocar skill directamente (con --once)")
    parser.add_argument("--mode", default="auto",  help="Modo LLM: local|cloud|auto (default: auto)")
    parser.add_argument("--output", metavar="FILE",help="Guardar respuesta a archivo")
    parser.add_argument("--token", metavar="TOKEN",help="Token de sesión (o se lee de ~/.zephyro/cli_token.txt)")
    args = parser.parse_args()

    # Pipe mode: si stdin no es un TTY → leer input y añadirlo al prompt
    stdin_text = ""
    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()

    # Obtener token
    token = args.token or _load_token() or os.getenv("ZEPHYRO_TOKEN", "")

    # Headless modes
    if args.once or args.pipe or stdin_text:
        if not token:
            admin_pw = os.getenv("ZEPHYRO_ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD", "")
            token = asyncio.run(_fetch_token(admin_pw))
        if not token:
            print("ERROR: No hay token. Exporta ZEPHYRO_TOKEN o ejecuta 'python cli.py' para autenticarte.", file=sys.stderr)
            sys.exit(1)

        message = args.once or args.pipe or ""
        if stdin_text:
            if message:
                message = f"{message}\n\n---\n{stdin_text}"
            else:
                message = stdin_text

        exit_code = asyncio.run(run_headless(
            message,
            mode=args.mode,
            skill=args.skill,
            output_file=args.output,
        ))
        sys.exit(exit_code)

    # TUI mode
    if not _HAS_TEXTUAL:
        print("ERROR: Falta 'textual'. Instalar: pip install textual pyperclip pygetwindow", file=sys.stderr)
        print("O usa el modo headless: python cli.py --once 'tu mensaje'", file=sys.stderr)
        sys.exit(1)

    if not token:
        # Intentar obtener token sin contraseña (si el backend no tiene auth activado)
        token = asyncio.run(_fetch_token(""))
        if not token:
            print("No se pudo obtener token automáticamente.")
            password = input("Contraseña del backend (o Enter si no hay): ")
            token = asyncio.run(_fetch_token(password))
        if not token:
            print("ERROR: No se pudo autenticar con el backend.", file=sys.stderr)
            sys.exit(1)
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token)

    app = JarvisTerminalApp(token=token)
    app.run()


if __name__ == "__main__":
    main()
