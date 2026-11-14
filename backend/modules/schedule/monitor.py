"""FocusMonitor — monitoreo de cumplimiento, modo enfoque, Pomodoro y detección de distracciones.

Tres modos independientes:
1. No invasivo (default): check-in diario preguntando al usuario que completo
2. Modo enfoque (activacion manual): trackea tiempo por app, sugiere breaks
3. Pomodoro (activacion por voz): timer configurable con fases work/break
4. Distraction detection: monitoreo de apps/URLs no permitidas durante Pomodoro o modo estudio
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ── Distraction detection — constantes de módulo ──────────────────────────────

_WHITELIST_PATH = (
    Path(__file__).resolve().parent.parent.parent / "storage" / "focus_whitelist.json"
)

# Snapshot of the live Pomodoro session. Lets a backend restart detect and clean
# an orphaned timer (an asyncio task never survives a process restart).
_ACTIVE_SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "storage" / "pomodoro_active.json"
)

_BASE_WHITELIST: frozenset[str] = frozenset({
    "obsidian", "visual studio code", "cursor", "code",
    "kitty", "alacritty", "windows powershell", "cmd", "powershell",
    "zephyro", "figma", "adobe", "pdf", "word", "libreoffice", "acrobat",
    "notion", "anki", "calibre",
})

_BASE_WHITELIST_URLS: frozenset[str] = frozenset({
    "music.youtube.com",
    "open.spotify.com",
    # Work / study domains — explicitly safe during focus and Pomodoro work.
    "github.com",
    "github.io",
    "gitlab.com",
    "gist.github.com",
    "stackoverflow.com",
    "stackexchange.com",
    "canvaslms.com",          # canvas.<institution>.canvaslms.com
    "instructure.com",        # canvas alias
    "developer.mozilla.org",
    "docs.python.org",
    "anthropic.com",
    "platform.openai.com",
    "huggingface.co",
})

_DISTRACTION_URLS: frozenset[str] = frozenset({
    "youtube.com/watch",
    "youtube.com/shorts",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "twitch.tv",
    "reddit.com",
    "facebook.com",
})

# Sitios distractores reconocidos por el TÍTULO de la ventana del navegador.
#
# `_DISTRACTION_URLS` solo sirve si hay URL, y la URL la trae Playwright, que NO puede
# leer el Edge/Chrome real del usuario salvo que se haya lanzado con
# `--remote-debugging-port=9222` (ver `backend/core/CLAUDE.md`, "por qué las páginas se
# abren en otra ventana de Edge"). En la práctica ese puerto está cerrado, así que
# `current_url` llega vacío.
#
# Consecuencia medida el 2026-10-04: WhatsApp SÍ se detectaba y YouTube NO, en la misma
# ventana y la misma sesión — porque "whatsapp" vive en `_MESSAGING_APPS`, que se
# compara contra el título, mientras YouTube vivía solo en el set de URLs. Esta tabla
# cierra esa asimetría. Título real observado:
#   "YouTube y 9 páginas más - Personal: Microsoft Edge"
#
# Trade-off aceptado: es substring sobre el título, así que un artículo titulado
# "Cómo bloquear YouTube" cuenta como distracción. Preferible a no ver nada: el usuario
# tiene `agregá X a la whitelist` para los falsos positivos, pero no tiene forma de
# enterarse de un falso NEGATIVO.
_DISTRACTION_SITE_TITLES: frozenset[str] = frozenset({
    "youtube", "instagram", "tiktok", "twitch", "reddit", "facebook",
    "twitter", "netflix", "disney+", "primevideo", "hbo max", "crunchyroll",
})

# Nombres de navegador en el título. Sin este gate, "youtube" matchearía una ventana
# del editor con un archivo `youtube.py` abierto.
_BROWSER_TITLE_HINTS: frozenset[str] = frozenset({
    "firefox", "chrome", "edge", "chromium", "opera", "brave", "navegador",
})

_DISTRACTION_APPS: frozenset[str] = frozenset({
    "discord",
    "steam",
    "epic games",
})

# ── Juegos: detección por PROCESO, no por título de ventana ──────────────────
# Lo que había antes eran los substrings "steam" y "epic games" en el título de la
# ventana activa, así que un juego corriendo en pantalla completa cuyo título no
# dijera eso era INVISIBLE. La resolución por proceso es la misma regla que
# `_focus_window_windows` aprendió a los golpes: el título no dice de quién es la
# ventana.
#
# Dos sets con semántica distinta, y la distinción importa:
#   * un LAUNCHER en la bandeja no es jugar — Steam abierto mientras programás es
#     normal. Solo cuenta como distracción si es la ventana en foco.
#   * un EJECUTABLE DE JUEGO corriendo ya es señal, esté o no en foco.
#
# Nada ambiguo entra acá. `javaw.exe` quedó AFUERA a propósito: es cualquier app
# Java, no Minecraft, y castigar a alguien por tener un proceso Java abierto sería
# peor que no detectar nada.
_GAME_LAUNCHERS: frozenset[str] = frozenset({
    "steam.exe", "steamwebhelper.exe",
    "epicgameslauncher.exe", "battle.net.exe", "riotclientservices.exe",
    "ubisoftconnect.exe", "upc.exe", "origin.exe", "eadesktop.exe",
    "goggalaxy.exe", "rockstarservice.exe", "launcher.exe",
})

_GAME_PROCESSES: frozenset[str] = frozenset({
    "valorant.exe", "valorant-win64-shipping.exe",
    "leagueoflegends.exe", "league of legends.exe",
    "cs2.exe", "csgo.exe", "dota2.exe", "hl2.exe",
    "fortniteclient-win64-shipping.exe",
    "robloxplayerbeta.exe", "genshinimpact.exe", "starrail.exe",
    "r5apex.exe", "overwatch.exe", "destiny2.exe", "gta5.exe", "gtav.exe",
    "rainbowsix.exe", "rocketleague.exe", "minecraft.exe",
    "eldenring.exe", "witcher3.exe", "cyberpunk2077.exe",
    "forzahorizon5.exe", "palworld-win64-shipping.exe",
})

_MUSIC_APPS: frozenset[str] = frozenset({
    "youtube music",
    "music.youtube.com",
    "spotify",
    "open.spotify.com",
})

_MESSAGING_APPS: frozenset[str] = frozenset({
    "whatsapp",
    "whatsapp web",
})

_WORD_CYCLE_MAP: dict[str, int] = {
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
}


class FocusMonitor:
    """Monitorea cumplimiento de agenda, modo enfoque, Pomodoro y distracciones."""

    POMODORO_WORK_MIN = int(os.getenv("POMODORO_WORK_MIN", "25"))
    POMODORO_SHORT_BREAK_MIN = int(os.getenv("POMODORO_SHORT_BREAK_MIN", "5"))
    POMODORO_LONG_BREAK_MIN = int(os.getenv("POMODORO_LONG_BREAK_MIN", "15"))

    # ── Distraction detection thresholds ──────────────────────────────────────
    POLL_INTERVAL_SEC: int = 3
    VISION_INTERVAL_SEC: int = int(os.getenv("FOCUS_VISION_INTERVAL_SEC", "60"))
    GRACE_PERIODS: dict[str, int] = {
        # Claves EXPLÍCITAS por categoría. Antes "browser", "app" y "game" no estaban y
        # caían a `default_distraction` por omisión, no por decisión. Y había una clave
        # "music" que era código muerto: `_classify_distraction` nunca devuelve esa
        # categoría porque los dominios de música están whitelisteados de entrada.
        "game": 0,       # un juego en foco durante trabajo no tiene margen
        "browser": 0,
        "app": 0,
        "messaging": 30,
        # Relaxed grace applied to messaging/voice-chat apps (Discord, WhatsApp)
        # while a JARVIS call is active. The user is likely coordinating
        # something verbal — 3 min before we start nagging.
        "messaging_during_call": 180,
        "default_distraction": 0,
    }
    ALERT_THRESHOLDS: dict[str, int] = {
        "level1": 0,    # inmediato al expirar grace
        "level2": 120,  # +2 min
        "level3": 240,  # +4 min → cuenta regresiva 30s
    }
    ENFORCEMENT_COUNTDOWN: int = 30  # segundos antes de cerrar tabs

    def __init__(self):
        # ── Modo enfoque básico ────────────────────────────────────────────────
        self.active = False
        self._last_check = 0.0
        self._app_times: dict[str, float] = {}
        self._current_app: str = ""
        self._app_start: float = 0.0
        self._last_break_suggestion: float = 0.0
        self._check_interval = 300
        self._break_cooldown = 1800

        # ── Pomodoro ───────────────────────────────────────────────────────────
        self._pomodoro_task: Optional[asyncio.Task] = None
        self._pomodoro_count: int = 0
        self._pomodoro_task_name: str = ""
        self._pomodoro_event_id: Optional[int] = None
        self._on_pomodoro_done: Optional[Callable] = None
        self._pomodoro_duration_min: int = 0
        self._pomodoro_start_ts: float = 0.0
        self._total_cycles: int = 4
        self._cycles_completed: int = 0
        self._pomodoro_phase: str = "work"
        self._short_break_min: int = self.POMODORO_SHORT_BREAK_MIN
        self._long_break_min: int = self.POMODORO_LONG_BREAK_MIN

        # ── Pause / Extend / Skip ──────────────────────────────────────────────
        self._pomodoro_paused: bool = False
        self._pomodoro_pause_event: Optional[asyncio.Event] = None  # lazy-init
        self._pomodoro_total_pauses_sec: float = 0.0
        self._pomodoro_pause_started_at: float = 0.0  # ts when the current pause began
        self._pomodoro_extend_pending: int = 0  # minutes to add to current phase
        self._pomodoro_skip_current: bool = False  # signal to end current phase early
        # Seconds added to the CURRENT phase via extend. Reset at each phase start.
        # Kept separate from _pomodoro_duration_min so extending a break never
        # contaminates the work-phase base duration of future cycles.
        self._phase_extend_sec: float = 0.0

        # ── Contabilidad de foco: tramos de trabajo EFECTIVO de la sesión ───────
        # Lista de [inicio, fin] (fin None mientras está abierto). Hace falta la lista
        # entera, no un total: el % de foco se calcula intersectando cada episodio de
        # distracción con estos tramos.
        #
        # Derivar el tiempo trabajado de `_pomodoro_start_ts` NO sirve: resume_pomodoro
        # lo empuja hacia adelante por la duración de la pausa, así que restarle además
        # `_pomodoro_total_pauses_sec` descuenta las pausas DOS veces. Y sumar las
        # duraciones de los ciclos completados tampoco: esas son el presupuesto de la
        # fase, no lo que realmente se trabajó (ni cubren una sesión cortada a mitad
        # del primer ciclo, donde daría cero).
        self._work_intervals: list[list[Optional[float]]] = []

        # ── Vision on-task tracking (for end-of-session report) ────────────────
        self._on_task_count: int = 0
        self._off_task_count: int = 0

        # ── JARVIS call mode signal ────────────────────────────────────────────
        # Set externally by the frontend (POST /focus/jarvis-call-mode) when the
        # user enters/exits a call with Zephyro. Used to relax Discord/WhatsApp
        # grace because the user may be coordinating verbally.
        self._jarvis_call_active: bool = False

        # ── WebSocket registry ─────────────────────────────────────────────────
        self._ws_registry = None

        # ── Distraction detection ──────────────────────────────────────────────
        self._study_mode_active: bool = False
        # Strict focus gates the (costly) vision checks. Off by default so a normal
        # Pomodoro never calls Haiku Vision; app/URL distraction detection still runs.
        self._strict_focus: bool = False
        self._vision_task: Optional[asyncio.Task] = None
        self._session_whitelist: set[str] = set()
        self._permanent_additions: set[str] = set()
        self._distraction_state: dict = self._empty_distraction_state()
        self._distraction_log: list[dict] = []
        # ── Juegos ─────────────────────────────────────────────────────────────
        # Resultado del último escaneo de procesos. Vacío por defecto, así que la
        # clasificación se comporta igual que antes si nunca se escaneó.
        self._running_games: set[str] = set()
        self._foreground_proc: str = ""
        # Juegos vistos en BACKGROUND durante la sesión: evidencia para el reporte,
        # no tiempo de distracción. Tener Steam abierto no es haber jugado.
        self._background_games_seen: set[str] = set()
        self._game_scan_tick: int = 0
        self._detection_task: Optional[asyncio.Task] = None
        self._context_detector = None   # inyectado via set_context_detector()
        self._app_controller = None     # inyectado via set_app_controller()
        self._vision_capture = None     # inyectado via set_vision_tools()
        self._vision_analyze = None
        self._last_vision_ts: float = 0.0
        self._load_permanent_whitelist()

    # ── Injection setters ──────────────────────────────────────────────────────

    def set_ws_registry(self, registry) -> None:
        self._ws_registry = registry

    def set_context_detector(self, detector) -> None:
        self._context_detector = detector

    def set_app_controller(self, app_controller) -> None:
        self._app_controller = app_controller

    def set_vision_tools(self, capture, analyze) -> None:
        self._vision_capture = capture
        self._vision_analyze = analyze

    def set_jarvis_call_mode(self, active: bool) -> None:
        """Toggle the 'JARVIS call active' signal. Frontend POSTs this when
        the user enters/exits a call so the distraction classifier can grant
        a longer grace to messaging/voice apps that the user might be using
        to coordinate the conversation."""
        self._jarvis_call_active = bool(active)

    def set_strict_focus(self, active: bool) -> None:
        """Enable/disable vision-based on-task checks (costly: Haiku Vision).
        When off, only the free app/URL distraction detection runs."""
        self._strict_focus = bool(active)

    # ── Active-session persistence (restart safety) ──────────────────────────────

    def _write_active_snapshot(self, payload: dict) -> None:
        """Persist the live session so a backend restart can detect & clean an
        orphaned Pomodoro. Full resume is future work — today we only clean up."""
        try:
            _ACTIVE_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            snap = {
                "pid": os.getpid(),
                "saved_at": time.time(),
                "task": self._pomodoro_task_name,
                "phase": self._pomodoro_phase,
                "cycle": self._cycles_completed + 1,
                "total_cycles": self._total_cycles,
                "start_ts": self._pomodoro_start_ts,
                "duration_min": self._pomodoro_duration_min,
                "short_break_min": self._short_break_min,
                "long_break_min": self._long_break_min,
                "phase_extend_sec": self._phase_extend_sec,
                "paused": self._pomodoro_paused,
                "last_action": payload.get("action", ""),
            }
            _ACTIVE_SNAPSHOT_PATH.write_text(json.dumps(snap, ensure_ascii=False), "utf-8")
        except Exception:
            pass

    def _clear_active_snapshot(self) -> None:
        try:
            _ACTIVE_SNAPSHOT_PATH.unlink(missing_ok=True)
        except Exception:
            pass

    async def recover_orphan_session(self) -> Optional[dict]:
        """Call once at startup. Any snapshot on disk belongs to a Pomodoro whose
        asyncio task died with the previous process, so it is orphaned: remove it
        and tell connected clients to drop the ghost timer. The frontend also
        self-heals via GET /pomodoro/status on load/reconnect."""
        try:
            if not _ACTIVE_SNAPSHOT_PATH.exists():
                return None
            snap = json.loads(_ACTIVE_SNAPSHOT_PATH.read_text("utf-8"))
        except Exception:
            snap = None
        self._clear_active_snapshot()
        if not snap:
            return None
        await self._ws_broadcast({
            "type": "pomodoro",
            "action": "cancelled",
            "cycle": snap.get("cycle", 1),
            "reason": "backend_restarted",
        })
        return snap

    # ── Distraction property ───────────────────────────────────────────────────

    @property
    def _is_monitoring_active(self) -> bool:
        """True si hay Pomodoro en fase work y sin pausar, O modo estudio manual.

        `not self._pomodoro_paused` importa para la contabilidad, no solo para las
        alertas: sin ese guard un episodio abierto seguía acumulando durante una pausa,
        así que pausar 10 min con YouTube delante sumaba 10 min de distracción contra
        un tiempo de trabajo que no avanzó.
        """
        pomodoro_running = (
            self._pomodoro_task is not None
            and not self._pomodoro_task.done()
            and self._pomodoro_phase == "work"
            and not self._pomodoro_paused
        )
        return pomodoro_running or self._study_mode_active

    # ── Whitelist management ───────────────────────────────────────────────────

    def _load_permanent_whitelist(self) -> None:
        try:
            if _WHITELIST_PATH.exists():
                data = json.loads(_WHITELIST_PATH.read_text("utf-8"))
                self._permanent_additions = {
                    s.lower() for s in data.get("permanent_additions", [])
                }
        except Exception:
            self._permanent_additions = set()

    def _save_permanent_whitelist(self) -> None:
        _WHITELIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "permanent_additions": sorted(self._permanent_additions),
        }
        _WHITELIST_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), "utf-8"
        )

    def add_to_session_whitelist(self, app: str) -> str:
        self._session_whitelist.add(app.lower())
        return f"'{app}' agregado a la whitelist de esta sesión."

    def add_to_permanent_whitelist(self, app: str) -> str:
        self._permanent_additions.add(app.lower())
        self._save_permanent_whitelist()
        return f"'{app}' agregado permanentemente a la whitelist."

    def show_whitelist(self) -> str:
        base = sorted(_BASE_WHITELIST)
        perm = sorted(self._permanent_additions)
        sess = sorted(self._session_whitelist)
        lines = ["Apps siempre permitidas:"] + [f"  · {a}" for a in base]
        if perm:
            lines += ["Adiciones permanentes:"] + [f"  · {a}" for a in perm]
        if sess:
            lines += ["Solo esta sesión:"] + [f"  · {a}" for a in sess]
        lines += [
            "",
            "URLs de música permitidas (60s gracia):",
            "  · music.youtube.com, open.spotify.com",
            "WhatsApp Web (30s gracia).",
        ]
        return "\n".join(lines)

    # ── Distraction classification ─────────────────────────────────────────────

    def _is_whitelisted(self, window_title: str, url: str) -> bool:
        t = (window_title or "").lower()
        u = (url or "").lower()
        if any(w in t for w in _BASE_WHITELIST):
            return True
        combined = self._session_whitelist | self._permanent_additions
        if any(w in t or w in u for w in combined):
            return True
        if any(w in u for w in _BASE_WHITELIST_URLS):
            return True
        return False

    def _classify_distraction(
        self, window_title: str, url: str
    ) -> tuple[bool, str]:
        """Retorna (es_distraccion, categoria)."""
        t = (window_title or "").lower()
        u = (url or "").lower()

        # Juego en foco: se decide por PROCESO y gana sobre todo lo demás, incluida la
        # whitelist. Un juego cuyo título dijera "Visual Studio Code" (o nada) se
        # colaba por el título; el dueño del proceso no se puede falsear.
        if self._foreground_game():
            return True, "game"

        if self._is_whitelisted(window_title, url):
            return False, ""

        for pattern in _DISTRACTION_URLS:
            if pattern in u:
                return True, "browser"

        # Sin URL (el caso normal: el Edge real no expone el puerto CDP), el título de
        # la ventana del navegador es la ÚNICA señal disponible.
        if any(h in t for h in _BROWSER_TITLE_HINTS):
            # Música por título se trata igual que por URL: whitelisteada. Escuchar
            # música mientras trabajás no es distraerse.
            if any(m in t for m in _MUSIC_APPS):
                return False, ""
            for site in _DISTRACTION_SITE_TITLES:
                if site in t:
                    return True, "browser"

        if any(m in t for m in _MESSAGING_APPS):
            # Relax grace for messaging while a JARVIS call is active.
            return True, "messaging_during_call" if self._jarvis_call_active else "messaging"

        if any(d in t for d in _DISTRACTION_APPS):
            # Discord is the common case: voice/chat coordination companion
            # when on a JARVIS call. Same relaxation applies.
            if self._jarvis_call_active and "discord" in t:
                return True, "messaging_during_call"
            return True, "app"

        return False, ""

    def _get_grace_period(self, category: str) -> int:
        return self.GRACE_PERIODS.get(
            category, self.GRACE_PERIODS["default_distraction"]
        )

    def _normalize_label(self, window: str, url: str) -> str:
        if url:
            try:
                netloc = urlparse(url).netloc
                if netloc:
                    return netloc.replace("www.", "")
            except Exception:
                pass
        # Sin URL el título cambia con cada video o pestaña, y `_check_distraction`
        # compara ESTA etiqueta para decidir si sigue siendo la misma distracción.
        # Devolver el título crudo hacía que cada video abriera un episodio NUEVO:
        # grace reseteado y `alert_level` de vuelta a 0. Medido en vivo el 2026-10-04:
        # cuatro alertas de nivel 1 seguidas en lugar de una escalada, con etiquetas
        # como "(6) el ICEBERG DEFINITIVO de l" (el corte de 30 caracteres).
        # Zapeando videos nunca se llegaba al nivel 2.
        t = (window or "").lower()
        if any(h in t for h in _BROWSER_TITLE_HINTS):
            for site in _DISTRACTION_SITE_TITLES:
                if site in t:
                    return site
        return (window or "app desconocida")[:30]

    # ── State helpers ──────────────────────────────────────────────────────────

    def _empty_distraction_state(self) -> dict:
        return {
            "app": "",
            "url": "",
            "started_at": 0.0,
            "grace_expires_at": 0.0,
            "alert_level": 0,
            "level1_at": 0.0,
            "level2_at": 0.0,
            "level3_at": 0.0,
            "enforcement_deadline": 0.0,
            "enforcement_done": False,
            # Categoría de _classify_distraction ("browser" | "app" | "messaging" |
            # "messaging_during_call" | "game"). Se persiste al cerrar el episodio:
            # sin ella el score no puede pesar una partida distinto que un WhatsApp.
            "category": "",
            # Unified state — source tracks which detector opened/escalated the episode.
            "source": "",            # "app" | "vision" | "vision+app"
            "vision_category": "",   # entertainment | social | game | video | etc
            "vision_confidence": 0.0,
        }

    def _close_distraction_episode(self, ended_at: float) -> None:
        s = self._distraction_state
        if not s["app"]:
            return
        self._distraction_log.append({
            "app": s["app"],
            "url": s["url"],
            "started_at": s["started_at"],
            "ended_at": ended_at,
            "duration_sec": ended_at - s["started_at"],
            "max_alert_level": s["alert_level"],
            "enforced": s["enforcement_done"],
            # Un episodio abierto SOLO por visión no pasó por _classify_distraction,
            # así que cae a su categoría de visión ("game", "social", "video"…).
            "category": s.get("category") or s.get("vision_category") or "app",
        })
        self._distraction_state = self._empty_distraction_state()

    # ── Juegos ─────────────────────────────────────────────────────────────────
    # Un escaneo cada N ticks del detector, no en cada uno: a 3 s por tick, cada 5
    # ticks son ~15 s, suficiente para notar que arrancó un juego sin barrer la tabla
    # de procesos 20 veces por minuto.
    GAME_SCAN_EVERY_N_TICKS: int = 5

    @staticmethod
    def _scan_games_sync() -> tuple[set[str], str]:
        """UNA pasada de procesos + el ejecutable en foco. Corre en un thread.

        Una sola `process_iter` intersectada contra los sets, no `is_process_running`
        por nombre: ese helper barre TODOS los procesos por cada nombre consultado, así
        que con ~50 nombres serían 50 barridos completos.
        """
        found: set[str] = set()
        try:
            import psutil
            wanted = _GAME_PROCESSES | _GAME_LAUNCHERS
            for proc in psutil.process_iter(["name"]):
                try:
                    name = (proc.info["name"] or "").lower()
                except Exception:
                    continue
                if name in wanted:
                    found.add(name)
        except Exception:
            pass
        try:
            from core.platform import foreground_process_name
            fg = foreground_process_name() or ""
        except Exception:
            fg = ""
        return found, fg

    async def _refresh_game_state(self) -> None:
        """Actualiza el estado de juegos. Nunca propaga errores al loop de detección."""
        try:
            found, fg = await asyncio.to_thread(self._scan_games_sync)
        except Exception:
            return
        self._running_games = found
        self._foreground_proc = fg
        # Un juego corriendo que NO está en foco es evidencia, no tiempo perdido.
        for name in found:
            if name in _GAME_PROCESSES and name != fg:
                self._background_games_seen.add(name)

    async def _refresh_foreground_proc(self) -> None:
        """Updates only the foreground process owner. Never raises into the loop."""
        try:
            from core.platform import foreground_process_name
            self._foreground_proc = await asyncio.to_thread(foreground_process_name) or ""
        except Exception:
            self._foreground_proc = ""

    def _foreground_game(self) -> str:
        """Nombre del juego en foco, o "" si la ventana activa no es un juego.

        Un launcher solo cuenta si ES la ventana en foco (Steam en la bandeja mientras
        programás no es jugar). Un ejecutable de juego cuenta igual, pero para abrir un
        episodio con tiempo tiene que estar adelante.
        """
        fg = self._foreground_proc
        if fg and (fg in _GAME_PROCESSES or fg in _GAME_LAUNCHERS):
            return fg
        return ""

    # ── Tramos de trabajo efectivo ─────────────────────────────────────────────

    def _open_work_interval(self) -> None:
        """Abre un tramo de trabajo efectivo. Idempotente."""
        if self._work_intervals and self._work_intervals[-1][1] is None:
            return
        self._work_intervals.append([time.time(), None])

    def _close_work_interval(self) -> None:
        """Cierra el tramo abierto, si hay. Idempotente."""
        if self._work_intervals and self._work_intervals[-1][1] is None:
            self._work_intervals[-1][1] = time.time()

    def _work_seconds(self) -> float:
        """Segundos de trabajo efectivo de la sesión (pausas y descansos excluidos)."""
        now = time.time()
        return sum(
            (end if end is not None else now) - start
            for start, end in self._work_intervals
        )

    def _distracted_seconds(self) -> float:
        """Distracción acumulada, CLIPEADA a los tramos de trabajo efectivo.

        El clipping no es cosmético. Un episodio abierto que cruza una pausa o un
        descanso se cierra recién en el siguiente tick del detector, así que sin
        clipear sumaba tiempo que nunca fue de trabajo — y la distracción podía
        terminar siendo MAYOR que lo trabajado. El modo estudio lo empeora: ignora
        la restricción de fase, así que un episodio puede cruzar work→break→work.
        """
        now = time.time()
        total = 0.0
        for ep in self._distraction_log:
            ep_start = ep.get("started_at") or 0.0
            ep_end = ep.get("ended_at") or 0.0
            for w_start, w_end in self._work_intervals:
                w_stop = w_end if w_end is not None else now
                overlap = min(ep_end, w_stop) - max(ep_start, w_start)
                if overlap > 0:
                    total += overlap
        return total

    def _finalize_focus_accounting(self) -> None:
        """Cierra el episodio abierto para que los reportes vean la sesión completa.

        Idempotente: `_close_distraction_episode` no hace nada sin episodio abierto.
        Tiene que correr ANTES de armar cualquier reporte final, o una distracción
        todavía viva queda fuera de `_distraction_log` y dos reportes de la misma
        sesión dan números distintos.
        """
        if self._distraction_state["app"]:
            self._close_distraction_episode(time.time())
        self._close_work_interval()

    async def _release_detection_loop(self) -> None:
        """Apaga el detector salvo que el modo estudio siga necesitándolo.

        Pomodoro y modo estudio comparten UNA sola task (`_start_detection_loop` es
        idempotente), así que apagarla al terminar un pomodoro le robaría el monitoreo
        a un modo estudio que sigue activo.
        """
        if self._study_mode_active:
            return
        await self._stop_detection_loop()

    # ── Detection loop ─────────────────────────────────────────────────────────

    async def _detection_loop(self) -> None:
        """Polling de ventana activa cada POLL_INTERVAL_SEC segundos.
        Se auto-pausa cuando _is_monitoring_active es False (breaks, etc.).
        """
        while True:
            try:
                await asyncio.sleep(self.POLL_INTERVAL_SEC)
                if not self._is_monitoring_active:
                    if self._distraction_state["app"]:
                        self._close_distraction_episode(time.time())
                    continue
                # Juegos: cada N ticks, no en cada uno (barre la tabla de procesos).
                # Va ANTES de _check_distraction porque la clasificación lo consulta.
                self._game_scan_tick += 1
                if self._game_scan_tick % self.GAME_SCAN_EVERY_N_TICKS == 1:
                    await self._refresh_game_state()
                else:
                    # The full scan is throttled, but the foreground owner is cheap and
                    # must be fresh: a stale game exe kept classifying VS Code as
                    # "game" for up to ~15 s after alt-tabbing out.
                    await self._refresh_foreground_proc()
                if self._context_detector is None:
                    # Sin ContextDetector no hay título ni URL, pero la señal de juego
                    # por proceso sí sirve: no perderla por eso.
                    if self._foreground_game():
                        await self._check_distraction(self._foreground_proc, "")
                    continue
                ctx = self._context_detector.get_current()
                window = ctx.get("active_window") or ""
                url = ctx.get("current_url") or ""
                await self._check_distraction(window, url)
                # Vision periódica — solo en modo estricto (tiene costo: Haiku Vision).
                # Guardamos la referencia a la task para evitar que el GC la cancele.
                if self._strict_focus and self._vision_capture and self._vision_analyze:
                    now_v = time.time()
                    if now_v - self._last_vision_ts >= self.VISION_INTERVAL_SEC:
                        self._last_vision_ts = now_v
                        self._vision_task = asyncio.create_task(self._do_vision_check())
            except asyncio.CancelledError:
                return
            except Exception:
                pass  # loop nunca crashea

    async def _check_distraction(self, window: str, url: str) -> None:
        now = time.time()
        is_distraction, category = self._classify_distraction(window, url)

        if not is_distraction:
            if self._distraction_state["app"]:
                self._close_distraction_episode(now)
            return

        normalized = self._normalize_label(window, url)
        current_app = self._distraction_state.get("app", "")

        if current_app != normalized:
            if current_app:
                self._close_distraction_episode(now)
            grace = self._get_grace_period(category)
            self._distraction_state = self._empty_distraction_state()
            self._distraction_state.update({
                "app": normalized,
                "url": url,
                "started_at": now,
                "grace_expires_at": now + grace,
                "category": category,
                "source": "app",
            })
            return

        grace_expires = self._distraction_state["grace_expires_at"]
        if now < grace_expires:
            return

        time_past_grace = now - grace_expires
        current_level = self._distraction_state["alert_level"]

        if current_level == 0 and time_past_grace >= self.ALERT_THRESHOLDS["level1"]:
            await self._send_alert(1, normalized, time_past_grace)

        elif current_level == 1 and time_past_grace >= self.ALERT_THRESHOLDS["level2"]:
            await self._send_alert(2, normalized, time_past_grace)

        elif current_level == 2 and time_past_grace >= self.ALERT_THRESHOLDS["level3"]:
            await self._send_alert(3, normalized, time_past_grace)
            self._distraction_state["enforcement_deadline"] = now + self.ENFORCEMENT_COUNTDOWN

        elif current_level == 3 and not self._distraction_state["enforcement_done"]:
            deadline = self._distraction_state["enforcement_deadline"]
            if deadline > 0 and now >= deadline:
                outcome = await self._enforce_browser_close(url)
                self._distraction_state["enforcement_done"] = True
                # El aviso 4 ("Listo, cerré las pestañas") solo si de verdad
                # cerró algo. Una página con `beforeunload` — un formulario a
                # medio llenar — se queda abierta a propósito, y anunciar que
                # la cerramos sería mentir sobre el estado de su máquina.
                if outcome and not outcome.startswith("0 "):
                    await self._send_alert(4, normalized, time_past_grace)
                else:
                    logger.info(
                        "Enforcement sin efecto en %s (%s)", normalized, outcome
                    )

    async def _send_alert(self, level: int, app: str, duration_sec: float) -> None:
        duration_min = int(duration_sec / 60)
        dur = f"{duration_min} min" if duration_min > 0 else "un momento"
        ec = self.ENFORCEMENT_COUNTDOWN
        phrases = {
            1: [
                f"Ey, estás en {app} hace {dur}. ¿Volvemos al trabajo?",
                f"Oye, {app} no estaba en el plan. ¿Arrancamos de nuevo?",
                f"{app} puede esperar. ¿Le damos al Pomodoro?",
                f"Llevo {dur} viendo {app} en tu pantalla. ¿Seguimos con la tarea?",
            ],
            2: [
                f"Ya van {dur} en {app}. El foco se pierde rápido — volvemos.",
                f"Llevas {dur} fuera del radar. {app} puede esperar, retomemos.",
                f"{dur} en {app} es mucho. Volvamos antes de perder el ritmo.",
                f"Segundo aviso — {dur} en {app}. La tarea te está esperando.",
            ],
            3: [
                f"Último aviso: llevás {dur} en {app}. Cierro las pestañas en {ec} segundos si no volvés.",
                f"Oye, esto es serio — {dur} en {app}. En {ec}s actúo si no volvés.",
                f"Última llamada — {app} fuera en {ec} segundos o lo cierro yo.",
            ],
            4: [
                f"Listo, cerré {app}. Hora de trabajar.",
                f"Pestañas cerradas. Retomemos el Pomodoro.",
                f"Cerré las pestañas de {app}. Volvamos al foco.",
            ],
        }
        message = random.choice(phrases.get(level, [f"Nivel {level}: volvé al trabajo."]))
        self._distraction_state["alert_level"] = level
        ts = time.time()
        for key in ("level1_at", "level2_at", "level3_at"):
            if self._distraction_state.get(key, 0) == 0:
                self._distraction_state[key] = ts
                break

        await self._ws_broadcast({
            "type": "focus_alert",
            "level": level,
            "app": app,
            "source": self._distraction_state.get("source", "app"),
            "vision_category": self._distraction_state.get("vision_category", ""),
            "duration_sec": duration_sec,
            "message": message,
            # Frontend uses this flag to decide if TTS should fire in call mode.
            # Level 1 is informational; level 2+ persists past first warning and
            # deserves voice attention.
            "should_speak": level >= 2,
            "enforcement_in": self.ENFORCEMENT_COUNTDOWN if level == 3 else None,
        })

    async def _register_vision_evidence(
        self, on_task: bool, category: str, suggestion: str, confidence: float = 1.0
    ) -> None:
        """Merge a vision check result into the unified distraction state.

        on_task=True: log only (for end-of-session % calculation). No WS broadcast.
        on_task=False: open or fuse a distraction episode and let the normal alert
        escalation (_send_alert with levels 1-4) drive notifications.
        """
        if on_task:
            self._on_task_count += 1
            return

        self._off_task_count += 1
        now = time.time()
        s = self._distraction_state
        label = category or "pantalla distractora"

        if s["app"]:
            # Fuse into existing app episode — vision confirms or refines category.
            current_source = s.get("source", "app")
            if "vision" not in current_source:
                s["source"] = "vision+app" if current_source == "app" else "vision"
            s["vision_category"] = category or s.get("vision_category", "")
            s["vision_confidence"] = max(s.get("vision_confidence", 0.0), confidence)
            # No re-trigger of alert here — the app-detector loop will escalate
            # naturally on the next tick if grace has expired.
            return

        # New episode opened by vision alone (e.g. unknown game, unlisted app).
        # No grace period — vision capture cadence (15s) already gates the alert.
        self._distraction_state = self._empty_distraction_state()
        self._distraction_state.update({
            "app": label[:30],
            "url": "",
            "started_at": now,
            "grace_expires_at": now,  # immediate alert eligible
            "source": "vision",
            "vision_category": category,
            "vision_confidence": confidence,
        })
        # Send level 1 right away — same message style as app-based level 1.
        suggestion_msg = (
            suggestion
            or f"Detecté {label} en pantalla. ¿Volvemos al trabajo?"
        )
        ts = time.time()
        self._distraction_state["alert_level"] = 1
        self._distraction_state["level1_at"] = ts
        await self._ws_broadcast({
            "type": "focus_alert",
            "level": 1,
            "app": label,
            "source": "vision",
            "vision_category": category,
            "duration_sec": 0,
            "message": suggestion_msg,
            "should_speak": False,  # level 1 is informational
            "enforcement_in": None,
        })

    async def _enforce_browser_close(self, url: str) -> Optional[str]:
        """Cierra las pestañas de la distracción. Devuelve None si no hizo nada.

        El retorno importa: el aviso de nivel 4 dice "Listo, cerré las
        pestañas", y antes se enviaba pase lo que pase — sin controller, con
        excepción, o con cero pestañas cerradas. El `except: pass` se tragaba
        todo en silencio. Si no cerró nada, no hay que anunciar que sí.
        """
        controller = None
        if self._app_controller is not None:
            controller = getattr(self._app_controller, "_browser", None)
        if controller is None:
            return None
        # Episodes detected by window title or game process carry url="". An empty
        # pattern is a substring of every URL, so closing by it wiped every tab in the
        # context. Without a URL there is nothing safe to target: skip enforcement.
        pattern = (urlparse(url).netloc.replace("www.", "") or url or "").strip()
        if not pattern:
            return None
        try:
            return await controller.close_distraction_tabs(pattern)
        except Exception as exc:
            logger.warning("No pude cerrar las pestañas de %s: %s", url, exc)
            return None

    async def _start_detection_loop(self) -> None:
        if self._detection_task is None or self._detection_task.done():
            self._detection_task = asyncio.create_task(
                self._detection_loop(), name="focus-distraction-detector"
            )

    async def _stop_detection_loop(self) -> None:
        if self._detection_task and not self._detection_task.done():
            self._detection_task.cancel()
            try:
                await self._detection_task
            except asyncio.CancelledError:
                pass
        self._detection_task = None

    # ── Study mode ─────────────────────────────────────────────────────────────

    async def start_study_mode(self) -> str:
        if self._study_mode_active:
            return "El modo estudio ya está activo."
        self._study_mode_active = True
        self._distraction_log.clear()
        self._distraction_state = self._empty_distraction_state()
        # Don't inherit the foreground process from a previous Pomodoro.
        self._foreground_proc = ""
        await self._start_detection_loop()
        await self._ws_broadcast({"type": "study_mode", "action": "start"})
        return (
            "Modo estudio activado. Estoy vigilando las distracciones.\n"
            "Te aviso si entrás a YouTube, Instagram u otras apps no permitidas."
        )

    async def stop_study_mode(self) -> str:
        if not self._study_mode_active:
            return "El modo estudio no estaba activo."
        self._study_mode_active = False
        pomodoro_running = self._pomodoro_task is not None and not self._pomodoro_task.done()
        if pomodoro_running:
            # The Pomodoro still owns the work interval: closing it here ended the
            # work tramo early and the rest of the cycle stopped counting as work.
            # Only close the open episode so this report sees it.
            if self._distraction_state["app"]:
                self._close_distraction_episode(time.time())
        else:
            self._finalize_focus_accounting()
            # Mismo leak que en el pomodoro: nadie apagaba el detector al salir.
            await self._stop_detection_loop()
        report = self.get_distraction_report()
        await self._ws_broadcast({
            "type": "study_mode",
            "action": "stop",
            "report": report,
        })
        return f"Modo estudio desactivado.\n{report}"

    def _background_games_line(self) -> Optional[str]:
        """Juegos que estuvieron corriendo SIN estar en foco.

        Se reporta aparte de los episodios y sin minutos: tener un juego abierto no es
        haber jugado, así que no suma tiempo de distracción. Pero es exactamente la
        señal que se pedía ("ver si tiene juegos activos"), y callarla sería peor.
        """
        if not self._background_games_seen:
            return None
        names = ", ".join(sorted(n.removesuffix(".exe") for n in self._background_games_seen))
        return f"Juegos abiertos durante el foco (sin estar al frente): {names}"

    def get_distraction_report(self) -> str:
        games_line = self._background_games_line()
        if not self._distraction_log:
            base = "Sin distracciones registradas. Foco perfecto."
            return f"{base}\n{games_line}" if games_line else base
        total_sec = sum(e["duration_sec"] for e in self._distraction_log)
        enforced = sum(1 for e in self._distraction_log if e["enforced"])
        lines = [
            f"Resumen: {len(self._distraction_log)} episodio(s), "
            f"{int(total_sec / 60)} min de distracción total."
        ]
        for e in self._distraction_log[-5:]:
            dur = int(e["duration_sec"] / 60) or 1
            forced = " [tab cerrada]" if e["enforced"] else ""
            cat = e.get("category") or ""
            tag = f" [{cat}]" if cat and cat != "app" else ""
            lines.append(f"  · {e['app']}{tag}: ~{dur} min{forced}")
        if enforced:
            lines.append(f"Tabs cerradas automáticamente: {enforced} vez/veces.")
        if games_line:
            lines.append(games_line)
        return "\n".join(lines)

    # ── Modo enfoque básico ────────────────────────────────────────────────────

    def start(self) -> str:
        """Activa el modo enfoque."""
        self.active = True
        self._last_check = time.time()
        self._last_break_suggestion = 0.0
        self._app_times.clear()
        return "Modo enfoque activado. Te aviso si necesitas pausas activas."

    def stop(self) -> str:
        """Desactiva el modo enfoque."""
        self.active = False
        total_min = sum(self._app_times.values()) / 60
        apps_summary = ", ".join(
            f"{app}: {int(secs/60)}min"
            for app, secs in sorted(self._app_times.items(), key=lambda x: -x[1])[:5]
        )
        return (
            f"Modo enfoque desactivado. Tiempo total: {total_min:.0f} min.\n"
            f"Apps: {apps_summary if apps_summary else 'sin datos'}"
        )

    def status(self) -> str:
        """Estado actual del monitoreo."""
        if self._pomodoro_task and not self._pomodoro_task.done():
            remaining = self._pomodoro_remaining_min()
            label = f" — {self._pomodoro_task_name}" if self._pomodoro_task_name else ""
            cycle_txt = f"Ciclo {self._cycles_completed + 1}/{self._total_cycles}. "
            phase_txt = "Trabajo" if self._pomodoro_phase == "work" else "Descanso"
            return (
                f"Pomodoro activo{label}. "
                f"{cycle_txt}{phase_txt}. "
                f"Faltan {remaining} min."
            )
        if not self.active:
            return f"Modo enfoque inactivo. Pomodoros hoy: {self._pomodoro_count}."
        apps = ", ".join(
            f"{app}: {int(secs/60)}min"
            for app, secs in sorted(self._app_times.items(), key=lambda x: -x[1])[:3]
        )
        return f"Modo enfoque activo. Top apps: {apps if apps else 'sin datos aun'}."

    def tick(self, current_app: str = "") -> Optional[str]:
        """Llamado periodicamente desde el orchestrator. Retorna sugerencia de break o None."""
        if not self.active:
            return None
        now = time.time()
        if now - self._last_check < self._check_interval:
            return None
        self._last_check = now
        if current_app:
            if current_app != self._current_app:
                if self._current_app and self._app_start > 0:
                    elapsed = now - self._app_start
                    self._app_times[self._current_app] = (
                        self._app_times.get(self._current_app, 0) + elapsed
                    )
                self._current_app = current_app
                self._app_start = now
            else:
                elapsed = now - self._app_start
                self._app_start = now
                self._app_times[current_app] = (
                    self._app_times.get(current_app, 0) + elapsed
                )
        if current_app and current_app in self._app_times:
            app_seconds = self._app_times[current_app]
            if app_seconds > 5400 and (now - self._last_break_suggestion) > self._break_cooldown:
                self._last_break_suggestion = now
                from modules.schedule.recommender import get_active_break
                return (
                    f"Llevas {int(app_seconds/60)} min en {current_app}. "
                    f"Una pausa activa?\n\n{get_active_break(5)}"
                )
        return None

    # ── Pomodoro ───────────────────────────────────────────────────────────────

    async def start_pomodoro(
        self,
        task_name: str = "",
        duration_min: Optional[int] = None,
        total_cycles: Optional[int] = None,
        short_break_min: Optional[int] = None,
        event_id: Optional[int] = None,
        on_done: Optional[Callable] = None,
        strict_focus: bool = False,
    ) -> str:
        if self._pomodoro_task and not self._pomodoro_task.done():
            return "Ya hay un Pomodoro activo. Decí 'parar pomodoro' primero."

        duration = duration_min or self.POMODORO_WORK_MIN
        if duration < 1:
            duration = self.POMODORO_WORK_MIN

        self._pomodoro_task_name = task_name
        self._pomodoro_event_id = event_id
        self._on_pomodoro_done = on_done
        self._pomodoro_duration_min = duration
        self._total_cycles = max(1, total_cycles or 4)
        self._short_break_min = max(1, short_break_min or self.POMODORO_SHORT_BREAK_MIN)
        self._strict_focus = bool(strict_focus)
        self._cycles_completed = 0
        self._pomodoro_phase = "work"
        self._pomodoro_start_ts = time.time()
        self._distraction_log.clear()
        self._distraction_state = self._empty_distraction_state()

        self._pomodoro_task = asyncio.create_task(self._pomodoro_session())

        label = f" para '{task_name}'" if task_name else ""
        cycles_txt = f"{self._total_cycles} ciclos" if self._total_cycles > 1 else "1 ciclo"
        return (
            f"Pomodoro iniciado{label}. "
            f"{duration} min trabajo · {self._short_break_min} min descanso · {cycles_txt}. "
            f"¡A trabajar!"
        )

    async def _cancellable_sleep(self, seconds: float) -> None:
        """Sleep until the current phase ends. The deadline is derived from the
        authoritative state (start_ts + base + extends) every iteration, so:
        - pause: handled in pause_pomodoro/resume_pomodoro (they push _pomodoro_start_ts
          forward by the pause duration); here we just wait on the event.
        - extend_pending: accumulated into _phase_extend_sec (current phase only — never
          into _pomodoro_duration_min, which would inflate future work cycles).
        - skip_current: returns early (used by skip_break)."""
        if self._pomodoro_pause_event is None:
            self._pomodoro_pause_event = asyncio.Event()
            self._pomodoro_pause_event.set()

        phase_base = seconds
        while True:
            if self._pomodoro_skip_current:
                self._pomodoro_skip_current = False
                return
            if self._pomodoro_paused:
                await self._pomodoro_pause_event.wait()
                continue
            deadline = self._pomodoro_start_ts + phase_base + self._phase_extend_sec
            now = time.time()
            if now >= deadline:
                return
            await asyncio.sleep(min(1.0, deadline - now))
            if self._pomodoro_extend_pending != 0:
                added_min = self._pomodoro_extend_pending
                self._pomodoro_extend_pending = 0
                self._phase_extend_sec += added_min * 60
                await self._broadcast_pomodoro_state(
                    action="extended",
                    total_sec_override=int(phase_base + self._phase_extend_sec),
                )

    # ── Score de foco ──────────────────────────────────────────────────────────
    # Peso por categoría de distracción. Un minuto de partida no cuesta lo mismo que
    # un minuto de WhatsApp: la partida te saca del trabajo por completo, el mensaje
    # puede ser parte de él.
    DISTRACTION_WEIGHTS: dict[str, float] = {
        "game": 8.0,
        "browser": 5.0,
        "app": 4.0,
        "video": 5.0,
        "social": 5.0,
        "messaging": 2.0,
        "messaging_during_call": 1.0,
    }
    DEFAULT_DISTRACTION_WEIGHT: float = 4.0
    # Tope del castigo por TIEMPO, sobre el total de la sesión. Va al total y no por
    # episodio a propósito: topeando cada episodio, partir una distracción en tres
    # pagaría menos que dejarla entera.
    MAX_TIME_PENALTY: float = 50.0
    ALERT_PENALTIES: dict[int, float] = {2: 3.0, 3: 6.0, 4: 10.0}
    # Minutos de pausa gratis por ciclo antes de empezar a descontar.
    FREE_PAUSE_MIN_PER_CYCLE: int = 5
    PAUSE_PENALTY_PER_MIN: float = 2.0
    # Un ciclo "limpio" alimenta la racha.
    CLEAN_CYCLE_SCORE: int = 85

    def _alert_counts(self) -> dict[int, int]:
        """Cuántos episodios alcanzaron cada nivel de alerta, ACUMULATIVO.

        `max_alert_level` es severidad, no un contador: `_send_alert` escala un mismo
        episodio 1→2→3, y el log guarda solo el nivel final. Contar por igualdad
        (`== 2`) daría cero alertas de nivel 2 en un episodio que llegó a 3, aunque
        la de nivel 2 se emitió. Por eso se cuenta con `>=`.
        """
        counts = {2: 0, 3: 0, 4: 0}
        for ep in self._distraction_log:
            level = int(ep.get("max_alert_level") or 0)
            for threshold in counts:
                if level >= threshold:
                    counts[threshold] += 1
        return counts

    def _time_penalty(self) -> float:
        """Castigo por tiempo: se pesa CADA episodio y se topea el total."""
        now = time.time()
        total = 0.0
        for ep in self._distraction_log:
            ep_start = ep.get("started_at") or 0.0
            ep_end = ep.get("ended_at") or 0.0
            # Clipear a trabajo efectivo antes de pesar: un episodio que cruzó una
            # pausa no debe cobrarse por el tiempo en que nadie estaba trabajando.
            effective = 0.0
            for w_start, w_end in self._work_intervals:
                w_stop = w_end if w_end is not None else now
                overlap = min(ep_end, w_stop) - max(ep_start, w_start)
                if overlap > 0:
                    effective += overlap
            if effective <= 0:
                continue
            weight = self.DISTRACTION_WEIGHTS.get(
                ep.get("category") or "", self.DEFAULT_DISTRACTION_WEIGHT,
            )
            total += (effective / 60.0) * weight
        return min(self.MAX_TIME_PENALTY, total)

    def focus_score(self) -> Optional[int]:
        """Score 0-100 de la sesión, o None si no hay trabajo suficiente para medir."""
        work_sec = self._work_seconds()
        if work_sec < 60:
            return None
        score = 100.0 - self._time_penalty()
        counts = self._alert_counts()
        for level, penalty in self.ALERT_PENALTIES.items():
            score -= penalty * counts.get(level, 0)
        free_min = self.FREE_PAUSE_MIN_PER_CYCLE * max(1, self._cycles_completed)
        pause_min = self._pomodoro_total_pauses_sec / 60.0
        score -= self.PAUSE_PENALTY_PER_MIN * max(0.0, pause_min - free_min)
        # Techo: el score no puede superar el % honesto de foco. Sin esto, el tope de
        # MAX_TIME_PENALTY dejaba una sesión 100% distraída en 50/100 — demasiado
        # generoso. Los pesos y las alertas empujan hacia ABAJO desde ese techo, así
        # que una partida sigue costando más que un WhatsApp del mismo largo.
        distracted = min(self._distracted_seconds(), work_sec)
        on_task_pct = (work_sec - distracted) / work_sec * 100.0
        score = min(score, on_task_pct)
        return int(max(0.0, min(100.0, round(score))))

    def _pomodoro_focus_summary(self) -> str:
        """Resumen de foco de la sesión: % por TIEMPO real, score y pausas.

        Antes el % salía SOLO de los contadores de visión, que únicamente incrementa
        el tier de visión — apagado salvo `strict_focus`. Con `total_vision == 0` la
        función devolvía 100 fijo, así que el reporte decía "Foco: 100% on-task" en la
        misma pantalla que "1 episodio(s), 1 min de distracción". Se contradecía solo.
        """
        work_sec = self._work_seconds()
        pause_min = int(self._pomodoro_total_pauses_sec / 60)
        if work_sec < 60:
            parts = ["Foco: trabajo insuficiente para medir (< 1 min efectivo)"]
        else:
            distracted = min(self._distracted_seconds(), work_sec)
            pct = int(max(0, min(100, round((work_sec - distracted) / work_sec * 100))))
            score = self.focus_score()
            parts = [
                f"Foco: {pct}% on-task",
                f"{int(work_sec / 60)} min de trabajo efectivo",
            ]
            if score is not None:
                parts.append(f"score {score}/100")
        parts.append(f"pausas: {pause_min} min")
        # El ratio de visión es una señal APARTE, no se mezcla con el tiempo.
        total_vision = self._on_task_count + self._off_task_count
        if total_vision:
            vision_pct = round(self._on_task_count / total_vision * 100)
            parts.append(f"visión: {vision_pct}% on-task ({total_vision} muestras)")
        return " · ".join(parts)

    def _next_phase_info(self, current_cycle: int) -> "tuple[str, int]":
        """Return (next_phase_label, next_duration_sec) for the upcoming phase
        after the current one finishes. Used for the frontend 'luego: …' preview."""
        if self._pomodoro_phase == "work":
            if current_cycle >= self._total_cycles:
                return ("", 0)
            is_long = (current_cycle % 4 == 0)
            break_min = self._long_break_min if is_long else self._short_break_min
            return ("long_break" if is_long else "short_break", break_min * 60)
        # In a break → next is work (or end if this was the last cycle's break)
        if current_cycle > self._total_cycles:
            return ("", 0)
        return ("work", self._pomodoro_duration_min * 60)

    async def _broadcast_pomodoro_state(
        self,
        action: str,
        cycle: Optional[int] = None,
        total_sec_override: Optional[int] = None,
    ) -> None:
        """Single source-of-truth pomodoro broadcaster. Includes phase_detail
        (work | short_break | long_break) and next-phase preview so chat/call/BG
        renderers all read coherent state."""
        cycle = cycle if cycle is not None else self._cycles_completed + 1
        phase_detail = self._pomodoro_phase  # 'work' | 'short_break' | 'long_break'
        phase_simple = "work" if phase_detail == "work" else "break"
        if total_sec_override is not None:
            total_sec = total_sec_override
        else:
            total_sec = int(self._current_phase_duration_sec() + self._phase_extend_sec)
        next_phase, next_duration = self._next_phase_info(cycle)
        payload = {
            "type": "pomodoro",
            "action": action,
            "phase": phase_simple,
            "phase_detail": phase_detail,
            "cycle": cycle,
            "total_cycles": self._total_cycles,
            "task": self._pomodoro_task_name,
            "started_at": self._pomodoro_start_ts,
            "total_sec": total_sec,
            "paused": self._pomodoro_paused,
            "extend_pending_min": self._pomodoro_extend_pending,
            "next_phase": next_phase,
            "next_duration_sec": next_duration,
            "total_pauses_sec": self._pomodoro_total_pauses_sec,
        }
        # Compat with existing frontend handler that reads `break_min` on end_work.
        if action == "end_work":
            payload["break_min"] = total_sec // 60
        if action == "session_complete":
            self._clear_active_snapshot()
        else:
            self._write_active_snapshot(payload)
        await self._ws_broadcast(payload)

    # ── Pause / Resume / Extend / Skip ──────────────────────────────────────

    async def pause_pomodoro(self) -> str:
        if not self._pomodoro_task or self._pomodoro_task.done():
            return "No hay Pomodoro activo."
        if self._pomodoro_paused:
            return "El pomodoro ya está pausado."
        if self._pomodoro_pause_event is None:
            self._pomodoro_pause_event = asyncio.Event()
            self._pomodoro_pause_event.set()
        self._pomodoro_paused = True
        self._pomodoro_pause_started_at = time.time()
        self._pomodoro_pause_event.clear()
        # Una pausa corta el tramo de trabajo efectivo, y el detector cierra el
        # episodio abierto en su próximo tick (_is_monitoring_active ya mira la pausa).
        self._close_work_interval()
        await self._broadcast_pomodoro_state(action="paused")
        return "Pomodoro pausado. Decí 'reanuda' cuando quieras seguir."

    async def resume_pomodoro(self) -> str:
        if not self._pomodoro_task or self._pomodoro_task.done():
            return "No hay Pomodoro activo."
        if not self._pomodoro_paused:
            return "El pomodoro no estaba pausado."
        # Account the pause here (not in the sleep loop) so the "resumed" broadcast
        # carries the corrected clock: pushing _pomodoro_start_ts forward by the pause
        # duration moves the phase deadline and keeps the frontend countdown accurate.
        if self._pomodoro_pause_started_at > 0:
            paused_for = time.time() - self._pomodoro_pause_started_at
            self._pomodoro_total_pauses_sec += paused_for
            self._pomodoro_start_ts += paused_for
            self._pomodoro_pause_started_at = 0.0
        self._pomodoro_paused = False
        if self._pomodoro_pause_event is not None:
            self._pomodoro_pause_event.set()
        # Reabrir el tramo solo si lo que se reanuda es trabajo: pausar durante un
        # descanso y reanudar no debe contar como tiempo trabajado.
        if self._pomodoro_phase == "work":
            self._open_work_interval()
        await self._broadcast_pomodoro_state(action="resumed")
        return "Pomodoro reanudado."

    async def extend_pomodoro(self, minutes: int, mode: str = "current") -> str:
        """mode:
        - 'current'   → add minutes to the running phase (work or break alike).
        - 'new_cycle' → bump total_cycles by 1 (minutes ignored).
        """
        if not self._pomodoro_task or self._pomodoro_task.done():
            return "No hay Pomodoro activo."
        if mode == "new_cycle":
            self._total_cycles += 1
            await self._broadcast_pomodoro_state(action="extended")
            return f"Agregué un ciclo extra. Ahora {self._total_cycles} ciclos en total."
        if minutes <= 0:
            return "Indicá cuántos minutos extender."
        self._pomodoro_extend_pending += minutes
        # The sleep loop picks this up on the next 1s tick and broadcasts state.
        return f"Extiendo el ciclo actual {minutes} min más."

    async def skip_break(self) -> str:
        if not self._pomodoro_task or self._pomodoro_task.done():
            return "No hay Pomodoro activo."
        if "break" not in self._pomodoro_phase:
            return "No hay descanso activo para saltar."
        self._pomodoro_skip_current = True
        if self._pomodoro_paused:
            # Unpause so the sleep loop observes the skip flag immediately.
            self._pomodoro_paused = False
            if self._pomodoro_pause_event is not None:
                self._pomodoro_pause_event.set()
        return "Saltando descanso. Arrancamos el próximo ciclo."

    async def _pomodoro_session(self):
        """Multi-cycle Pomodoro loop: work → break → work → … → session_complete."""
        # Arrancar detección de distracciones para esta sesión
        await self._start_detection_loop()
        # Initialize pause/extend state in the running loop's context.
        if self._pomodoro_pause_event is None:
            self._pomodoro_pause_event = asyncio.Event()
        self._pomodoro_pause_event.set()
        self._pomodoro_paused = False
        self._pomodoro_pause_started_at = 0.0
        self._pomodoro_extend_pending = 0
        self._pomodoro_skip_current = False
        self._pomodoro_total_pauses_sec = 0.0
        self._phase_extend_sec = 0.0
        self._on_task_count = 0
        self._off_task_count = 0
        self._work_intervals = []
        self._background_games_seen = set()
        self._running_games = set()
        self._foreground_proc = ""
        self._game_scan_tick = 0
        try:
            cycle = 1
            while cycle <= self._total_cycles:
                # ── Work phase ────────────────────────────────────────────────
                self._pomodoro_phase = "work"
                self._last_vision_ts = 0.0  # fuerza vision check al inicio del ciclo
                self._pomodoro_start_ts = time.time()
                self._phase_extend_sec = 0.0
                self._open_work_interval()
                await self._broadcast_pomodoro_state(action="start", cycle=cycle)
                await self._cancellable_sleep(self._pomodoro_duration_min * 60)
                self._close_work_interval()

                self._cycles_completed += 1
                self._pomodoro_count += 1
                self._log_pomodoro_completed(self._pomodoro_duration_min)

                if cycle >= self._total_cycles:
                    await self._broadcast_pomodoro_state(
                        action="session_complete", cycle=cycle,
                    )
                    if self._on_pomodoro_done:
                        # Cerrar el episodio abierto ANTES de armar el reporte. Si no,
                        # una distracción que seguía viva al completarse la sesión no
                        # entraba en _distraction_log todavía y este reporte decía una
                        # cosa mientras el focus_report del finally decía otra.
                        self._finalize_focus_accounting()
                        report = self.get_distraction_report()
                        focus_summary = self._pomodoro_focus_summary()
                        await self._on_pomodoro_done(
                            f"Sesión Pomodoro completada. "
                            f"{self._total_cycles} ciclos de {self._pomodoro_duration_min} min.\n"
                            f"{report}\n{focus_summary}"
                        )
                    break

                # ── Break phase ───────────────────────────────────────────────
                is_long = (cycle % 4 == 0)
                break_min = self._long_break_min if is_long else self._short_break_min
                self._pomodoro_phase = "long_break" if is_long else "short_break"
                self._pomodoro_start_ts = time.time()
                self._phase_extend_sec = 0.0
                await self._broadcast_pomodoro_state(
                    action="end_work", cycle=cycle, total_sec_override=break_min * 60,
                )
                await self._cancellable_sleep(break_min * 60)

                # Resume work phase for the next cycle
                self._pomodoro_phase = "work"
                self._last_vision_ts = 0.0
                self._pomodoro_start_ts = time.time()
                self._phase_extend_sec = 0.0
                self._open_work_interval()
                await self._broadcast_pomodoro_state(
                    action="end_break", cycle=cycle + 1,
                )
                cycle += 1

        except asyncio.CancelledError:
            await self._ws_broadcast({
                "type": "pomodoro",
                "action": "cancelled",
                "cycle": self._cycles_completed + 1,
            })
        finally:
            # La sesión terminó (completada/cancelada/error): no dejar snapshot huérfano.
            self._clear_active_snapshot()
            # El detector queda vivo si no se cancela acá: su guard `_is_monitoring_active`
            # lo deja idle, pero la task sigue despertando cada POLL_INTERVAL_SEC para
            # siempre y, con strict_focus, podía relanzar una captura de pantalla.
            await self._release_detection_loop()
            # Reporte de distracciones al finalizar o cancelar
            self._finalize_focus_accounting()
            report = self.get_distraction_report()
            focus_summary = self._pomodoro_focus_summary()
            await self._ws_broadcast({
                "type": "focus_report",
                "report": f"{report}\n{focus_summary}",
            })

    async def _ws_broadcast(self, payload: dict):
        if self._ws_registry is None:
            return
        try:
            await self._ws_registry.broadcast(payload)
        except Exception:
            pass

    async def _do_vision_check(self) -> None:
        """Cascade vision check:
        Tier 1 (local, free): classify_screen() via Moondream2 if available — fast
            local on_task/off_task call. Skipped in Phase A (stub returns unavailable).
        Tier 2 (cloud): Claude Haiku Vision — only fires when Tier 1 is unavailable
            OR when Tier 1 detects off_task and we need a personalized suggestion.

        Result feeds the unified distraction state via _register_vision_evidence().
        On-task results are logged silently (for end-of-session % report) and never
        broadcast as user-facing alerts.
        """
        path = None
        try:
            path = await asyncio.to_thread(self._vision_capture.capture)
            task_desc = self._pomodoro_task_name or "la tarea asignada"

            # ── Tier 1: local classifier (stub in Phase A) ────────────────
            local_result = None
            try:
                from modules.vision.local_classifier import (
                    LOCAL_VISION_AVAILABLE, classify_screen,
                )
                if LOCAL_VISION_AVAILABLE:
                    local_result = await asyncio.to_thread(classify_screen, path)
            except Exception:
                local_result = None

            # If local says on-task with high confidence, skip Tier 2 entirely.
            if (
                local_result
                and local_result.get("on_task") is True
                and local_result.get("confidence", 0.0) >= 0.7
            ):
                await self._register_vision_evidence(
                    on_task=True,
                    category="",
                    suggestion="",
                    confidence=local_result["confidence"],
                )
                return

            # ── Tier 2: Haiku for nuanced classification + personalized message ──
            prompt = (
                f'El usuario está en una sesión Pomodoro estudiando "{task_desc}". '
                'Analiza esta captura de pantalla. Responde SOLO en JSON: '
                '{"on_task": true/false, "category": "study|social|video|game|chat|other", '
                '"suggestion": "máx 20 palabras en español"} '
                'on_task=true si estudia, trabaja, lee o tiene código/documentos educativos. '
                'on_task=false si está en redes sociales, vídeos de entretenimiento o juegos. '
                'category: tipo de actividad detectada (study cuando on_task=true). '
                'suggestion: vacío si on_task=true; recordatorio suave personalizado si false.'
            )
            raw = await asyncio.to_thread(
                self._vision_analyze.analyze, path, prompt, True, "anthropic"
            )
            import re as _re, json as _json
            m = _re.search(r'\{[^{}]*\}', raw, _re.DOTALL)
            data = _json.loads(m.group(0)) if m else {}
            on_task = bool(data.get("on_task", True))
            category = str(data.get("category", "")).strip()
            suggestion = str(data.get("suggestion", "")).strip()
            await self._register_vision_evidence(
                on_task=on_task,
                category=category,
                suggestion=suggestion,
                confidence=1.0,
            )
        except Exception:
            pass
        finally:
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass

    async def stop_pomodoro(self) -> str:
        if not self._pomodoro_task or self._pomodoro_task.done():
            return "No hay Pomodoro activo."
        self._pomodoro_task.cancel()
        try:
            await self._pomodoro_task
        except asyncio.CancelledError:
            pass
        self._pomodoro_phase = "work"
        return "Pomodoro detenido."

    def _current_phase_duration_sec(self) -> int:
        """Base seconds (sin extends) of the phase currently running."""
        if self._pomodoro_phase == "work":
            return self._pomodoro_duration_min * 60
        if self._pomodoro_phase == "long_break":
            return self._long_break_min * 60
        return self._short_break_min * 60

    def _pomodoro_remaining_min(self) -> int:
        if not self._pomodoro_task or self._pomodoro_task.done():
            return 0
        total_sec = self._current_phase_duration_sec() + self._phase_extend_sec
        if self._pomodoro_start_ts <= 0:
            return math.ceil(total_sec / 60)
        # While paused, freeze the clock at the instant the pause began.
        ref = (
            self._pomodoro_pause_started_at
            if self._pomodoro_paused and self._pomodoro_pause_started_at > 0
            else time.time()
        )
        elapsed = ref - self._pomodoro_start_ts
        return max(0, math.ceil((total_sec - elapsed) / 60))

    def _log_pomodoro_completed(self, duration_min: int):
        db_path = Path(__file__).resolve().parent.parent.parent / "storage" / "pomodoros.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pomodoros "
            "(id INTEGER PRIMARY KEY, task_name TEXT, event_id INTEGER, "
            "duration_min INTEGER, completed_at REAL)"
        )
        conn.execute(
            "INSERT INTO pomodoros (task_name, event_id, duration_min, completed_at) "
            "VALUES (?, ?, ?, ?)",
            (self._pomodoro_task_name, self._pomodoro_event_id, duration_min, time.time()),
        )
        conn.commit()
        conn.close()

    def get_pomodoro_stats(self) -> dict:
        db_path = Path(__file__).resolve().parent.parent.parent / "storage" / "pomodoros.db"
        if not db_path.exists():
            return {"total": 0, "today": 0, "today_minutes": 0, "session_count": self._pomodoro_count}
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pomodoros "
            "(id INTEGER PRIMARY KEY, task_name TEXT, event_id INTEGER, "
            "duration_min INTEGER, completed_at REAL)"
        )
        today_start = time.time() - (time.time() % 86400)
        total = conn.execute("SELECT COUNT(*) FROM pomodoros").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM pomodoros WHERE completed_at >= ?", (today_start,)
        ).fetchone()[0]
        today_minutes = conn.execute(
            "SELECT COALESCE(SUM(duration_min), 0) FROM pomodoros WHERE completed_at >= ?",
            (today_start,),
        ).fetchone()[0]
        conn.close()
        return {
            "total": total,
            "today": today,
            "today_minutes": today_minutes,
            "session_count": self._pomodoro_count,
        }


async def generate_daily_checkin(
    pending_events: list[dict],
    llm_manager: Optional[object] = None,
) -> str:
    """Genera el mensaje de check-in diario basado en eventos pendientes."""
    if not pending_events:
        return "Hoy completaste todo lo agendado. Buen trabajo!"
    event_lines = []
    for i, ev in enumerate(pending_events, 1):
        event_lines.append(f"  {i}. {ev['title']} ({ev.get('category', 'other')})")
    return (
        f"Check-in del dia: tenes {len(pending_events)} evento(s) pendiente(s):\n"
        + "\n".join(event_lines)
        + "\n\nPudiste completar alguno? Decime: \"complete el evento N\" o \"no pude hacer X\"."
    )


_monitor_instance: Optional[FocusMonitor] = None


def get_focus_monitor() -> FocusMonitor:
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = FocusMonitor()
    return _monitor_instance
