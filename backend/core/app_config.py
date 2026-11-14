"""
Fuente única de verdad para mapeo de aplicaciones.

Centraliza:
- AUTO_VERBS: verbos de automation (todas las conjugaciones) — fuente única
- APP_ALIASES: nombres genéricos/español → binarios reales
- APP_COMMANDS: binarios específicos para el daemon
- NATIVE_APPS: lista completa de apps nativas reconocidas por el router
- PROCESS_NAMES: nombres de proceso candidatos para verificación
- WINDOW_PATTERNS: patrones de título de ventana
- WEB_APP_URLS: mapeo de web apps a URLs
- SITE_KEYWORDS: sitios web reconocidos por el router
- resolve_app_binary: detección dinámica con shutil.which + fallback chain
"""
import re
import sys
import shutil
from typing import Optional

_IS_WIN = sys.platform == "win32"


# ── Aliases: nombre genérico/español → nombre canónico de app ──────────────
# Usado por daemon, app_launcher y router para normalizar antes de lanzar.
if _IS_WIN:
    APP_ALIASES: dict[str, str] = {
        "terminal": "cmd",
        "archivos": "explorer",
        "codigo": "code",
        "código": "code",
        "calculadora": "calc",
        "monitor": "taskmgr",
        "navegador": "msedge",
        "browser": "msedge",
        "explorador": "explorer",
        "vscode": "code",
        # Microsoft Office aliases (varias formas que el STT/usuario puede dictar)
        "microsoft word":       "word",
        "ms word":              "word",
        "winword":              "word",
        "microsoft excel":      "excel",
        "ms excel":             "excel",
        "microsoft powerpoint": "powerpoint",
        "ms powerpoint":        "powerpoint",
        "ppt":                  "powerpoint",
        "microsoft outlook":    "outlook",
        "ms outlook":           "outlook",
        "correo":               "outlook",
        "microsoft onenote":    "onenote",
        "microsoft teams":      "teams",
        "ms teams":             "teams",
        # Otras apps
        "bloc de notas":        "notepad",
        "bloc":                 "notepad",
        "notas":                "notepad",
    }
else:
    APP_ALIASES: dict[str, str] = {
        "terminal": "konsole",
        "archivos": "dolphin",
        "codigo": "code",
        "código": "code",
        "calculadora": "kcalc",
        "monitor": "plasma-systemmonitor",
        "navegador": "msedge",
        "browser": "msedge",
        "explorador": "dolphin",
        "vscode": "code",
    }


# ── Fallback chains para detección dinámica ────────────────────────────────
# Si el binario preferido no existe, probar el siguiente en la lista.
if _IS_WIN:
    _FALLBACK_CHAINS: dict[str, list[str]] = {
        "cmd": ["cmd", "powershell", "wt"],
        "explorer": ["explorer"],
        "calc": ["calc"],
        "code": ["code", "codium"],
    }
else:
    _FALLBACK_CHAINS: dict[str, list[str]] = {
        "konsole": ["konsole", "kitty", "alacritty", "xterm", "gnome-terminal"],
        "dolphin": ["dolphin", "nautilus", "thunar", "nemo"],
        "kcalc": ["kcalc", "gnome-calculator", "galculator", "qalculate-gtk"],
        "plasma-systemmonitor": ["plasma-systemmonitor", "ksysguard", "gnome-system-monitor", "htop"],
        "code": ["code", "codium", "code-oss"],
    }


def resolve_app_binary(canonical_name: str) -> str:
    """
    Dado un nombre canónico de app, devuelve el binario real disponible
    en el sistema usando shutil.which() con fallback chain.
    Si ninguno se encuentra, devuelve el nombre canónico original.
    """
    chain = _FALLBACK_CHAINS.get(canonical_name)
    if chain:
        for candidate in chain:
            if shutil.which(candidate):
                return candidate
    # Sin chain o ninguno encontrado: verificar el nombre directo
    if shutil.which(canonical_name):
        return canonical_name
    return canonical_name  # fallback: dejar que el OS reporte el error


def resolve_alias(app_name: str) -> str:
    """Resuelve un alias a nombre canónico, luego al binario disponible."""
    canonical = APP_ALIASES.get(app_name.lower(), app_name.lower())
    return resolve_app_binary(canonical)


# ── Normalización de targets dictados ──────────────────────────────────────
# El STT transcribe frases completas: "Puedes abrir Discord." llega con el punto
# final pegado al target. La heurística vieja (`"." in target` → es un dominio)
# convertía "discord." en https://discord. y abría una pestaña del navegador en
# vez de la app nativa. Bug real, 2026-09-29.

# Puntuación de los extremos: el STT deja tanto cierres ("discord.") como
# aperturas ("¿discord?"). Ninguna es parte del nombre del target.
_EDGE_PUNCT = ".,;:!?¿¡\"'`()[]{}«»"

# Dominio de verdad: exige un TLD alfabético de 2+ caracteres. "discord." no
# matchea; "discord.com" y "onpe.gob.pe" sí.
_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.[a-z]{2,}$"
)


def normalize_target(raw: str) -> str:
    """Limpia un target dictado: minúsculas y sin puntuación en los extremos."""
    if not raw:
        return ""
    return str(raw).strip().strip(_EDGE_PUNCT).strip().lower()


def looks_like_domain(value: str) -> bool:
    """True si `value` es un dominio con TLD real.

    Reemplaza al chequeo `"." in value`, que tomaba cualquier palabra con un
    punto final —incluida una frase dictada— por un dominio.

    Valida solo el host: "onpe.gob.pe/resultados" sigue siendo un dominio, para
    no romper los comandos de navegación con sub-path.
    """
    candidate = normalize_target(value)
    if not candidate or " " in candidate:
        return False
    # Descartar esquema y quedarse con el host (antes del primer "/")
    if "://" in candidate:
        candidate = candidate.split("://", 1)[1]
    host = candidate.split("/", 1)[0].split("?", 1)[0]
    return bool(_DOMAIN_RE.match(host))


# ── Comandos específicos para apps conocidas (daemon) ──────────────────────
# Mapeo de nombre canónico → lista de argumentos del comando.
# Las apps que NO están aquí usan el resolve_app_binary como fallback.
if _IS_WIN:
    APP_COMMANDS: dict[str, list[str]] = {
        "firefox": ["firefox"],
        "chrome": ["chrome"],
        "brave": ["brave"],
        "edge": ["msedge"],
        "spotify": ["spotify"],
        "vlc": ["vlc"],
        "code": ["code"],
        "calc": ["calc"],
        "explorer": ["explorer"],
        "cmd": ["cmd"],
        "powershell": ["powershell"],
        "steam": ["steam"],
        "discord": ["discord"],
        "taskmgr": ["taskmgr"],
    }
else:
    APP_COMMANDS: dict[str, list[str]] = {
        "firefox": ["firefox"],
        "chrome": ["google-chrome-stable"],
        "chromium": ["chromium"],
        "spotify": ["spotify"],
        "kate": ["kate"],
        "dolphin": ["dolphin"],
        "vlc": ["vlc"],
        "konsole": ["konsole"],
        "kitty": ["kitty"],
        "alacritty": ["alacritty"],
        "code": ["code"],
        "kcalc": ["kcalc"],
        "steam": ["steam"],
        "libreoffice": ["libreoffice", "--norestore"],
        "plasma-systemmonitor": ["plasma-systemmonitor"],
    }


# ── Nombres de proceso candidatos para verificación ──────────────────────
if _IS_WIN:
    PROCESS_NAMES: dict[str, list[str]] = {
        "firefox": ["firefox"],
        "chrome": ["chrome"],
        "edge": ["msedge"],
        "brave": ["brave"],
        "discord": ["Discord", "Update"],
        "spotify": ["Spotify"],
        "code": ["Code"],
        "steam": ["steam", "steamwebhelper"],
        "vlc": ["vlc"],
    }
else:
    PROCESS_NAMES: dict[str, list[str]] = {
        "firefox": ["firefox", "firefox-bin"],
        "chrome": ["google-chrome", "google-chrome-stable", "chrome"],
        "chromium": ["chromium", "chromium-browser"],
        "spotify": ["spotify"],
        "kate": ["kate"],
        "dolphin": ["dolphin"],
        "vlc": ["vlc"],
        "discord": ["Discord", "discord"],
        "konsole": ["konsole"],
        "kitty": ["kitty"],
        "alacritty": ["alacritty"],
        "code": ["code", "code-oss"],
        "kcalc": ["kcalc"],
        "steam": ["steam", "steamwebhelper"],
        "libreoffice": ["soffice", "soffice.bin"],
        "plasma-systemmonitor": ["plasma-systemmo", "plasma-systemmonitor"],
    }


# ── Patrones de título de ventana para verificación (wmctrl -l) ────────────
# wmctrl es Linux-only; en Windows no se usa.
if _IS_WIN:
    WINDOW_PATTERNS: dict[str, list[str]] = {}
else:
    WINDOW_PATTERNS: dict[str, list[str]] = {
        "firefox": ["Mozilla Firefox", "Firefox"],
        "chrome": ["Google Chrome", "Chrome"],
        "chromium": ["Chromium"],
        "spotify": ["Spotify"],
        "kate": ["Kate"],
        "dolphin": ["Dolphin"],
        "vlc": ["VLC media player", "VLC"],
        "discord": ["Discord"],
        "konsole": ["Konsole", "Terminal"],
        "kitty": ["kitty"],
        "alacritty": ["Alacritty"],
        "code": ["Visual Studio Code", "Code"],
        "steam": ["Steam"],
        "libreoffice": ["LibreOffice"],
        "plasma-systemmonitor": ["System Monitor", "Plasma System Monitor"],
        "kcalc": ["KCalc"],
    }


# ── Información de ventana para focus/activate ─────────────────────────────
# Usado por AppLauncher.focus_window() y Tauri focus_app para traer
# ventanas al frente cuando la app ya está corriendo (bug Discord en segundo plano).
# Claves: nombre canónico de app (post-resolve_alias).
APP_WINDOW_INFO: dict[str, dict[str, str]] = {
    "discord":    {"process": "Discord",    "class": "discord",    "title": "Discord"},
    "spotify":    {"process": "Spotify",    "class": "Spotify",    "title": "Spotify"},
    "vlc":        {"process": "vlc",        "class": "vlc",        "title": "VLC"},
    "code":       {"process": "Code",       "class": "Code",       "title": "Visual Studio Code"},
    "steam":      {"process": "steam",      "class": "Steam",      "title": "Steam"},
    "firefox":    {"process": "firefox",    "class": "firefox",    "title": "Firefox"},
    "kate":       {"process": "kate",       "class": "kate",       "title": "Kate"},
    "konsole":    {"process": "konsole",    "class": "konsole",    "title": "Konsole"},
    "libreoffice":{"process": "soffice",    "class": "libreoffice","title": "LibreOffice"},
}

if _IS_WIN:
    APP_WINDOW_INFO["discord"]["class"] = "Chrome_WidgetWin_1"
    APP_WINDOW_INFO["spotify"]["class"] = "Chrome_WidgetWin_0"
    APP_WINDOW_INFO["vlc"]["class"] = "QWidget"
    APP_WINDOW_INFO["code"]["class"] = "Chrome_WidgetWin_1"

    # Microsoft Office (desktop) — proceso y título
    APP_WINDOW_INFO["word"]       = {"process": "WINWORD",   "class": "OpusApp",  "title": "Word"}
    APP_WINDOW_INFO["excel"]      = {"process": "EXCEL",     "class": "XLMAIN",   "title": "Excel"}
    APP_WINDOW_INFO["powerpoint"] = {"process": "POWERPNT",  "class": "PPTFrameClass", "title": "PowerPoint"}
    APP_WINDOW_INFO["outlook"]    = {"process": "OUTLOOK",   "class": "rctrl_renwnd32", "title": "Outlook"}
    APP_WINDOW_INFO["onenote"]    = {"process": "ONENOTE",   "class": "Framework::CFrame", "title": "OneNote"}
    APP_WINDOW_INFO["teams"]      = {"process": "ms-teams",  "class": "Chrome_WidgetWin_1", "title": "Microsoft Teams"}
    # Otras apps Windows comunes
    APP_WINDOW_INFO["notepad"]    = {"process": "notepad",   "class": "Notepad",  "title": "Notepad"}
    APP_WINDOW_INFO["paint"]      = {"process": "mspaint",   "class": "MSPaintApp", "title": "Paint"}
    APP_WINDOW_INFO["wordpad"]    = {"process": "wordpad",   "class": "WordPadClass", "title": "WordPad"}
    APP_WINDOW_INFO["explorer"]   = {"process": "explorer",  "class": "CabinetWClass", "title": "Explorer"}


# ── Lista completa de apps nativas reconocidas por el router ───────────────
# Incluye nombres canónicos + aliases (para que `app_hit` funcione con ambos).
if _IS_WIN:
    NATIVE_APPS: list[str] = [
        "firefox", "browser", "navegador", "chrome", "brave", "edge",
        "spotify", "terminal", "code", "codigo", "código",
        "archivos", "explorer", "calculadora", "monitor",
        "vscode", "discord", "vlc", "steam",
        "cmd", "powershell",
        # Microsoft Office (desktop apps)
        "word", "excel", "powerpoint", "outlook", "onenote", "teams",
        # Otras apps comunes Windows
        "notepad", "bloc", "paint", "wordpad",
    ]
else:
    NATIVE_APPS: list[str] = [
        "firefox", "browser", "navegador", "chrome", "edge", "msedge", "spotify",
        "terminal", "kitty", "code", "codigo", "código",
        "archivos", "dolphin", "calculadora", "monitor",
        "vscode", "discord", "vlc", "steam", "libreoffice",
        "konsole", "alacritty", "kate",
    ]


# ── Web App URLs ───────────────────────────────────────────────────────────
WEB_APP_URLS: dict[str, str] = {
    # Social media
    "instagram": "https://instagram.com",
    "facebook": "https://facebook.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "ig": "https://instagram.com",
    "yt": "https://youtube.com",
    "wa": "https://web.whatsapp.com",
    "tiktok": "https://tiktok.com",
    "linkedin": "https://linkedin.com",
    "pinterest": "https://pinterest.com",
    # Messaging
    "whatsapp": "https://web.whatsapp.com",
    "telegram": "https://web.telegram.org",
    # Streaming / media
    "youtube": "https://youtube.com",
    "youtube music": "https://music.youtube.com",
    "youtube_music": "https://music.youtube.com",
    "yt music": "https://music.youtube.com",
    "music youtube": "https://music.youtube.com",
    "twitch": "https://twitch.tv",
    "netflix": "https://netflix.com",
    "spotify web": "https://open.spotify.com",
    "primevideo": "https://primevideo.com",
    "prime video": "https://primevideo.com",
    "hbo": "https://hbomax.com",
    # Productivity / dev
    "gmail": "https://gmail.com",
    "github": "https://github.com",
    "stackoverflow": "https://stackoverflow.com",
    "wikipedia": "https://wikipedia.org",
    "reddit": "https://reddit.com",
    # Gov / Electoral
    "onpe": "https://onpe.gob.pe",
}


# ── Sites where Playwright handles navigation ─────────────────────────────
# For these sites, NAVIGATE uses BrowserController.navigate_to() instead of
# subprocess.Popen so the tab stays under Playwright control and subsequent
# INTERACT/click commands can operate on it. Keys must match what the router
# emits as analysis["target"] (post-alias resolution via _WEB_ALIASES).
PLAYWRIGHT_CAPABLE_SITES: frozenset[str] = frozenset({
    "instagram", "ig",
    "youtube", "yt",
    "youtube_music", "youtube music", "yt music",
    "twitter", "x",
    "reddit",
    "twitch",
    "tiktok",
    "netflix",
    "spotify web",
})


# ── Verbos de automatización ───────────────────────────────────────────────
# DERIVADO de la fuente única core/verbs.py (dominios automation+media+search).
# Antes era un set literal; ahora agregar un verbo o forma voseo se hace UNA vez
# en core/verbs.py y se propaga acá + a todas las demás capas. Usado en: router
# (auto_keywords, compound, guards), clarification_gate, orchestrator, decomposer.
from core.verbs import verbs_for as _verbs_for, verbs_pattern as _verbs_pattern

AUTO_VERBS: set[str] = _verbs_for("automation", "media", "search")
AUTO_VERBS_PATTERN: str = _verbs_pattern("automation", "media", "search")


# ── Sitios web reconocidos por el router (CAPA 1) ─────────────────────────
SITE_KEYWORDS: list[str] = [
    "youtube", "youtube music", "youtube_music", "yt music",
    "wikipedia", "github", "gmail", "reddit", "twitch",
    "instagram", "facebook", "spotify", "netflix", "prime video", "hbo",
    "stackoverflow",
    "twitter", "whatsapp", "telegram", "linkedin", "pinterest", "tiktok",
    "onpe",
]


# ── Web apps soportadas para self_state (claves de WEB_APP_URLS) ────────────────────
SUPPORTED_WEB_APPS: list[str] = [
    "youtube", "instagram", "facebook", "twitter", "tiktok", "linkedin", "pinterest",
    "whatsapp", "telegram", "reddit", "twitch", "netflix", "spotify", "primevideo",
    "hbo", "gmail", "github", "stackoverflow", "wikipedia",
]

# ── Lista de apps para self_state (lo que el LLM dice que puede hacer) ─────
if _IS_WIN:
    SUPPORTED_APPS: list[str] = [
        "firefox", "chrome", "brave", "edge", "discord", "spotify",
        "vlc", "steam", "code",
        "terminal", "archivos", "calculadora", "monitor", "vscode", "codigo",
        "cmd", "powershell", "explorer",
        # Microsoft Office
        "word", "excel", "powerpoint", "outlook", "onenote", "teams",
        # Other common Windows apps
        "notepad", "bloc", "paint", "wordpad",
        # Skills dinámicas (Parte A/B del plan)
        "github", "introspection", "log_reader",
    ]
else:
    SUPPORTED_APPS: list[str] = [
        "firefox", "chrome", "chromium", "edge", "discord", "spotify",
        "vlc", "kate", "dolphin", "konsole", "steam", "code",
        "libreoffice", "systemsettings", "alacritty", "kitty",
        "terminal", "archivos", "calculadora", "monitor", "vscode", "codigo",
        # Skills dinámicas (Parte A/B del plan)
        "github", "introspection", "log_reader",
    ]


# ── Umbrales de clarificación de baja confianza ────────────────────────────
# Consumidos por core/clarification_gate.py. El score viene de
# core/confidence.py (derivado de analysis["_source"]) y del ranking de
# candidatos del skill registry.
#   >= CLARIFY_HIGH                → resolver directo (sin preguntar)
#   [CLARIFY_LOW, CLARIFY_HIGH)    → preguntar si "parece comando"
#   < CLARIFY_LOW y no parece cmd  → tratar como chat (no preguntar)
# AMBIGUITY_MARGIN: si dos candidatos están dentro de este margen entre sí,
#   se consideran empatados → preguntar aunque el top supere CLARIFY_HIGH.
CLARIFY_HIGH: float = 0.8
CLARIFY_LOW: float = 0.4
AMBIGUITY_MARGIN: float = 0.15
