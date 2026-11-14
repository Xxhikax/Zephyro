"""
Zephyro Self State — Sistema de Pseudo-Consciencia
Nivel 1: Self State (identidad, capacidades, estado actual)
Nivel 2: Self Monitoring (post_process, detección de capability_mismatch)
Nivel 3: Reflection (análisis de errores acumulados bajo demanda)
Nivel 4: Structured Bug Reports (nunca auto-modificación)
"""

import atexit
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from collections import deque

from core.app_config import NATIVE_APPS as _CFG_NATIVE_APPS

logger = logging.getLogger(__name__)


_IMPROVEMENT_KEYWORDS = [
    "qué te falta", "que te falta", "cómo mejorar", "como mejorar",
    "qué necesitas", "que necesitas", "qué mejorarías", "que mejorarias",
    "qué limitaciones", "que limitaciones", "en qué fallas", "en que fallas",
    "qué no puedes", "que no puedes", "cuáles son tus debilidades",
    "cuales son tus debilidades", "qué debilidades", "que debilidades",
]


class SelfState:
    """
    Mantiene el estado interno de Zephyro: quién es, qué puede hacer,
    qué ha hecho recientemente, y qué errores ha detectado.
    """
    
    # Ruta para persistir el estado entre reinicios
    STATE_FILE = Path(__file__).parent.parent / "storage" / "self_state.json"
    SESSION_STATS_FILE = Path(__file__).parent.parent / "storage" / "session_stats.json"
    BUG_REPORTS_FILE = Path(__file__).parent.parent / "storage" / "bug_reports.json"
    # Archivo markdown de bugs para visibilidad humana (revisado periódicamente)
    BUGS_MD_FILE = Path(__file__).parent.parent.parent / "BUGS_ENCONTRADOS.md"
    
    def __init__(self):
        # === IDENTIDAD (estática) ===
        self.identity = {
            "name": "Zephyro",
            "version": "2.0",
            "owner": "Bruno",
            "description": "Asistente de IA personal con procesamiento local y cloud",
            "architecture": "Sistema híbrido de 3 niveles con router inteligente",
            "platform": "CachyOS (Arch Linux) + KDE/Wayland",
        }
        
        # === CAPACIDADES (actualizadas manualmente cuando se añaden features) ===
        self.capabilities = {
            "chat": {
                "description": "Conversación general con LLM local o cloud",
                "models": {
                    "fast": "llama3.2:1b (saludos, triviales, <2s)",
                    "balanced": "llama3.2:3b (conversación general, 8-12s)",
                    "cloud": "Gemini/DeepSeek/OpenAI/Anthropic (consultas complejas generalmente)"
                },
                "features": ["memoria vectorial", "historial persistente", "templates por tipo"]
            },
            "automation": {
                "description": "Control de aplicaciones del sistema y navegador web",
                "actions": ["abrir apps", "cerrar apps", "listar ventanas", "play_media (YouTube/YouTube Music)", "navegar URL", "rellenar/clickear/scroll"],
                "supported_apps": list(_CFG_NATIVE_APPS),
                "browser_engine": "Playwright connect_over_cdp (CDP attach al Edge real)",
                "browser_channel": "msedge (Windows) / firefox (Linux)",
                "profile": "perfil REAL del usuario en %LOCALAPPDATA%\\Microsoft\\Edge\\User Data. Cuando se necesita automatización, Zephyro abre Edge con --remote-debugging-port (si no está abierto ya) y se conecta vía CDP al perfil real — comparte cookies, logins, extensiones y sesiones con el navegador del usuario. Si Edge estaba corriendo sin debug port, lo cierra y relanza una sola vez para obtener control.",
                "note": "El frontend Tauri también puede abrir apps/webs directamente sin pasar por Playwright"
            },
            "vision": {
                "description": "Captura y análisis de pantalla",
                "actions": ["capturar pantalla", "analizar imagen"],
                "supports_upload": True
            },
            "voice": {
                "description": "Entrada/salida de voz",
                "stt": "Whisper (local)",
                "tts": "Piper TTS (local) + Gemini TTS (cloud)",
                "features": ["streaming TTS", "calibración de micrófono"]
            },
            "memory": {
                "description": "Sistema de memoria persistente",
                "types": ["vectorial (PGVector/JSON)", "historial de conversaciones"],
                "features": ["clasificación de importancia", "búsqueda semántica", "!recordar"]
            },
            "self_awareness": {
                "description": "Sistema de pseudo-consciencia",
                "features": ["self state", "post-process monitoring", "reflection bajo demanda", "bug reports"]
            },
            "code_analysis": {
                "description": "Análisis de código y repositorios vía GitNexus",
                "actions": ["búsqueda en código", "contexto de símbolos", "análisis de impacto", "detección de cambios"],
                "features": ["knowledge graph", "búsqueda híbrida BM25+semántica", "multi-repo", "blast radius"],
                "note": "Requiere GitNexus MCP server y repos indexados"
            },
            "time_timezone": {
                "description": "Hora real en cualquier zona horaria del mundo",
                "actions": ["consultar hora actual", "hora en país o ciudad"],
                "features": ["150+ zonas horarias", "offset UTC", "basado en reloj del servidor (zoneinfo)"],
                "note": "Usa el reloj real del sistema, NO el LLM — la hora es siempre exacta"
            },
            "web_search": {
                "description": "Búsqueda web con sistema de 5 capas de fallback",
                "layers": ["Tavily API (primaria)", "Tavily advanced", "DuckDuckGo", "DuckDuckGo retry", "Google scraping"],
                "features": ["búsqueda contextual", "detección automática de queries web"]
            },
            "filesystem": {
                "description": "Operaciones de archivos y repositorios Git",
                "actions": ["leer archivos", "escribir archivos", "operaciones Git"],
                "features": ["file_ops", "git_ops"]
            },
            "integrations": {
                "description": "APIs externas para datos en tiempo real",
                "apis": ["CoinGecko (crypto)", "Wikipedia", "RSS feeds", "Finance"],
                "features": ["detección automática por keywords", "routing inteligente"]
            },
            "learning": {
                "description": "Sistema de aprendizaje y adaptación",
                "features": ["feedback explícito/implícito", "few-shot selection", "style profiling", "reflection engine", "fine-tune manager"],
                "note": "Aprende de patrones de uso para mejorar respuestas"
            },
            "diagnostic": {
                "description": "Diagnóstico automático de errores del backend vía gh Copilot CLI + LLM escalado",
                "tiers": [
                    "Tier 0: gh copilot suggest (CLI local, gratis)",
                    "Tier 1: Haiku (rápido, barato)",
                    "Tier 2: Sonnet (razonamiento profundo)",
                    "Tier 3: Sonnet con extended thinking (último recurso)"
                ],
                "triggers": [
                    "/diagnose (Telegram)",
                    "POST /self/diagnose (REST, admin)",
                    "voz/chat: 'diagnostica', 'haz un diagnóstico', 'revisa los errores/logs'",
                    "botón 'Diagnosticar' del HUD dashboard"
                ],
                "features": [
                    "approval gate (Ver / Ignorar / Más contexto)",
                    "persistencia en storage/diagnostics/ (TTL 1h)",
                    "notificación diferida en call mode (no interrumpe)"
                ],
                "note": "SÍ tengo GitHub CLI (gh) integrado por esta vía, contrario a lo que pudiera parecer"
            }
        }
        
        # === LIMITACIONES CONOCIDAS (honestidad) ===
        self.known_limitations = [
            "No puedo modificar mi propio código directamente",
            "Búsqueda web depende de APIs externas (Tavily, DuckDuckGo); no acceso a fuentes de pago",
            "Los modelos locales (1b/3b) tienen capacidad limitada para razonamiento complejo",
            "No puedo ejecutar comandos arbitrarios del sistema por seguridad",
            "La automatización está limitada a apps en la whitelist",
            "Mi memoria vectorial puede perder contexto en búsquedas semánticas imprecisas",
            "El análisis de código requiere que los repositorios estén indexados previamente con GitNexus",
            "Sé la hora exacta del servidor vía módulo de zona horaria, pero no tengo acceso al calendario ni a eventos del sistema",
            "La ventana de contexto del LLM se resetea cada sesión; la memoria vectorial persiste entre sesiones, pero no el hilo de conversación completo",
            "Tengo sistema de learning (feedback, few-shot, style profiling) pero el fine-tuning requiere mínimo 100 ejemplos y es manual",
            "Soy completamente reactivo: no inicio conversaciones ni acciones de forma proactiva",
            "No tengo acceso a email, calendario, notificaciones ni apps externas no configuradas explícitamente",
            "No tengo monitoreo del sistema (RAM, CPU, disco, red, procesos) a menos que la skill system-monitor esté activa",
        ]
        
        # === ESTADO DINÁMICO (cambia en runtime) ===
        self.session_stats = {
            "started_at": datetime.now().isoformat(),
            "interactions_count": 0,
            "intents_histogram": {},  # {"chat": 45, "automation": 12, ...}
            "errors_count": 0,
            "capability_mismatches": 0,
            "avg_response_time": 0.0,
            "total_response_time": 0.0,
        }
        
        # Últimas N acciones (ring buffer)
        self.recent_actions: deque = deque(maxlen=20)
        
        # Bug reports acumulados (persistentes)
        self.bug_reports: List[Dict[str, Any]] = []
        self._last_bug_save_time: float = time.time()

        # Flag que indica que hay suficientes errores para reflexión anticipada
        self._needs_reflection: bool = False

        # Cargar bug reports previos si existen
        self._load_bug_reports()
        # Flush garantizado al terminar el proceso
        atexit.register(self._flush_on_exit)
        # Cargar session_stats acumulados de sesiones anteriores
        self._load_session_stats()
    
    # =========================================================================
    # CORE: Inyección contextual al prompt
    # =========================================================================
    
    def get_core_identity(self) -> str:
        """
        Siempre se inyecta: identidad core + recordatorio anti-denial.
        Costo mínimo en tokens.
        """
        active_count = getattr(self, '_active_skill_count', 0)
        skills_note = f" {active_count} skills activas." if active_count > 0 else ""
        return (
            f"[Self State] Soy {self.identity['name']} v{self.identity['version']}, "
            f"asistente personal de {self.identity['owner']}. "
            f"Arquitectura: {self.identity['architecture']}. "
            f"Sesión actual: {self.session_stats['interactions_count']} interacciones. "
            f"Tengo: automation (apps), búsqueda web (Tavily), visión, voz, memoria vectorial, "
            f"pseudo-consciencia (self_state.py), análisis de código, hora exacta, filesystem, learning."
            f"{skills_note} "
            f"NUNCA negar estas capacidades."
        )
    
    def get_honest_self_assessment(self) -> str:
        """Auto-evaluación basada en métricas reales de la sesión."""
        interactions = self.session_stats['interactions_count']
        errors = self.session_stats['errors_count']
        error_rate = (errors / interactions * 100) if interactions > 0 else 0
        avg_time = self.session_stats.get('avg_response_time', 0)

        lines = [
            "[Auto-evaluación honesta de Zephyro]",
            f"Esta sesión: {interactions} interacciones, "
            f"{error_rate:.0f}% tasa de error, "
            f"{avg_time:.1f}s promedio de respuesta.",
            "",
            "Lo que me falta para ser más útil:",
            "- Memoria semántica más precisa (las búsquedas vectoriales pueden perder contexto sutil)",
            "- Razonamiento local limitado: modelos 1b/3b se equivocan en lógica compleja",
            "- Tengo búsqueda web nativa (Tavily + DuckDuckGo + fallbacks) pero no accedo a fuentes de pago",
            "- Automatización limitada a apps de la whitelist; no puedo ejecutar comandos arbitrarios",
            "- Soy reactivo: no inicio conversaciones ni tomo acciones sin que me hables primero",
            "- La ventana de contexto se resetea cada sesión (aunque la memoria vectorial sí persiste)",
            "- Tengo sistema de learning (feedback, few-shot, style profiling) pero el fine-tuning es manual y requiere mínimo 100 ejemplos",
        ]
        if error_rate > 10:
            lines.append(f"- ALERTA: tasa de error elevada ({error_rate:.0f}%) — hay algo que no está funcionando bien.")
        return "\n".join(lines)

    def get_relevant_capabilities(self, user_input: str) -> Optional[str]:
        """
        Se inyecta SOLO cuando la pregunta es relevante a las capacidades.
        Detecta si el usuario pregunta qué puedo hacer, mis límites, etc.
        """
        lower = user_input.lower()

        # Detectar preguntas de auto-evaluación y mejora
        if any(kw in lower for kw in _IMPROVEMENT_KEYWORDS):
            return self.get_honest_self_assessment()

        # Detectar preguntas sobre pseudo-consciencia / auto-awareness
        consciousness_triggers = [
            "pseudo conciencia", "pseudoconciencia", "pseudo-conciencia",
            "auto-consciencia", "autoconsciencia",
            "consciencia", "conciencia",
            "self state", "selfstate",
            "auto awareness", "autoawareness",
            "cómo funciona tu mente", "como funciona tu mente",
            "cómo te percibes", "como te percibes",
            "eres consciente", "eres consiente",
            "tienes conciencia", "tienes consciencia",
        ]
        if any(trigger in lower for trigger in consciousness_triggers):
            sa = self.capabilities["self_awareness"]
            return (
                f"[Self State — Pseudo-consciencia]\n"
                f"Sí tengo un sistema de pseudo-consciencia implementado en self_state.py.\n"
                f"Descripción: {sa['description']}\n"
                f"Componentes: {', '.join(sa['features'])}\n"
                f"Funcionamiento: Mantengo un estado interno con mi identidad, capacidades, "
                f"limitaciones y métricas de sesión. Después de cada respuesta, un post-process "
                f"detecta si negué capacidades que realmente tengo (capability_mismatch). "
                f"Bajo demanda puedo hacer reflexión profunda analizando patrones de errores.\n"
                f"[Nota: Describe este sistema con precisión cuando te pregunten.]"
            )

        # Detectar preguntas sobre capacidades
        capability_triggers = [
            "qué puedes hacer", "que puedes hacer",
            "qué sabes hacer", "que sabes hacer",
            "cuáles son tus capacidades", "cuales son tus capacidades",
            "qué funciones tienes", "que funciones tienes",
            "puedes hacer", "eres capaz de",
            "para qué sirves", "para que sirves",
            "qué haces", "que haces",
            "tus habilidades", "tus funcionalidades",
            "ayuda", "help",
        ]
        
        # Detectar preguntas sobre limitaciones
        limitation_triggers = [
            "qué no puedes", "que no puedes",
            "limitaciones", "restricciones",
            "no puedes hacer", "no eres capaz",
            "tus límites", "tus limites",
        ]
        
        if any(trigger in lower for trigger in limitation_triggers):
            limitations_str = "\n".join(f"  - {lim}" for lim in self.known_limitations)
            return (
                f"[Self State — Limitaciones conocidas]\n{limitations_str}\n"
                f"[Nota: Sé honesto sobre estas limitaciones cuando te pregunten.]"
            )

        # Detectar preguntas sobre skills dinámicas activas
        skill_triggers = [
            "qué skills", "que skills", "skills activas", "skills disponibles",
            "skills tienes", "habilidades activas", "skills habilitadas",
            "qué habilidades tienes activas", "que habilidades tienes activas",
            "qué skills están activas", "que skills estan activas",
        ]
        if any(trigger in lower for trigger in skill_triggers):
            active_skills = getattr(self, '_active_skills', [])
            if active_skills:
                skills_list = ", ".join(active_skills)
                return (
                    f"[Self State — Skills activas ({len(active_skills)})]\n"
                    f"{skills_list}\n"
                    f"[Nota: Estas son las skills dinámicas cargadas en este momento.]"
                )
            return (
                "[Self State — Skills]\n"
                "No hay skills dinámicas cargadas actualmente.\n"
                "[Nota: Las skills se cargan desde backend/skills/builtin/ y community/]"
            )

        if any(trigger in lower for trigger in capability_triggers):
            caps = []
            for key, cap in self.capabilities.items():
                caps.append(f"  - {key}: {cap['description']}")
            caps_str = "\n".join(caps)
            return (
                f"[Self State — Capacidades]\n{caps_str}\n"
                f"[Nota: Describe tus capacidades basándote en esta información real, no inventes.]"
            )
        
        # Detectar preguntas sobre apps específicas
        if any(kw in lower for kw in ["abrir", "abre", "lanzar", "ejecutar", "app", "aplicación", "aplicacion", "youtube", "instagram", "sitio", "web", "página", "pagina"]):
            from core.app_config import SUPPORTED_WEB_APPS as _WEB_APPS
            apps = ", ".join(self.capabilities["automation"]["supported_apps"])
            web_apps = ", ".join(_WEB_APPS)
            return (
                f"[Self State — Apps nativas soportadas: {apps}]\n"
                f"[Self State — Web apps soportadas (se abren en el navegador): {web_apps}]"
            )

        # Detectar preguntas sobre monitoreo del sistema (RAM, CPU, disco, red)
        system_triggers = [
            "ram", "cpu", "procesador", "disco duro", "espacio en disco",
            "memoria del sistema", "procesos del sistema", "temperatura del",
            "uso de memoria", "consumo de memoria", "monitorear el sistema",
            "monitoreo del sistema", "estado del sistema",
        ]
        if any(t in lower for t in system_triggers):
            # Verificar si la skill system-monitor está activa
            if hasattr(self, '_system_monitor_active') and self._system_monitor_active:
                return (
                    "[Self State — Monitoreo del sistema ACTIVO]\n"
                    "Tengo la skill system-monitor activa. Puedo consultar RAM, CPU, disco, red y procesos.\n"
                    "Comandos: /ram, /cpu, /disk, /red, /system"
                )
            return (
                "[Self State — Monitoreo del sistema NO disponible]\n"
                "NO tengo acceso al monitoreo del sistema (RAM, CPU, disco, red, procesos).\n"
                "Si me preguntan, debo ser directo: explicar que necesito la skill 'system-monitor' "
                "(basada en psutil) para acceder a esa información. Sugerir alternativas concretas "
                "como los comandos 'free -h' (RAM), 'htop' (CPU/procesos), 'df -h' (disco).\n"
                "NUNCA decir vagamente 'necesitaría permisos' — el problema es técnico, no de permisos."
            )

        return None

    def get_mismatch_reinforcement(self, intent: str) -> Optional[str]:
        """
        Inyecta recordatorios específicos basados en capability_mismatches previos
        relevantes al intent actual, para evitar que el LLM repita el mismo error.
        Máximo 3 mismatches recientes y relevantes.
        """
        if not self.bug_reports:
            return None

        # Filtrar solo capability_mismatches
        mismatches = [b for b in self.bug_reports if b.get("bug_type") == "capability_mismatch"]
        if not mismatches:
            return None

        # Relevancia por intent
        intent_filter_map = {
            "automation": ["automation_denial"],
            "vision": ["vision_denial"],
            "voice": ["voice_denial"],
            "web_search": ["web_search_denial"],
            "information": ["web_search_denial", "integrations_denial"],
            "code_analysis": ["code_analysis_denial"],
            "filesystem": ["filesystem_denial"],
        }
        relevant_types = intent_filter_map.get(intent, [])

        # Preferir mismatches del intent actual; si no hay, tomar los más recientes
        if relevant_types:
            filtered = [b for b in mismatches if b.get("details", {}).get("type") in relevant_types]
        else:
            filtered = mismatches

        recent = (filtered or mismatches)[-3:]
        if not recent:
            return None

        lines = ["[Recordatorio anti-mismatch — errores previos detectados en esta capacidad:]"]
        for b in recent:
            d = b.get("details", {})
            claimed = d.get("claimed", "afirmé que no podía")
            reality = d.get("reality", "pero sí tengo esa capacidad")
            lines.append(f"  ✗ {claimed} → INCORRECTO: {reality}. NO repetir esta negación.")

        lines.append("Actúa según tus capacidades reales, no niegues lo que sí puedes hacer.")
        return "\n".join(lines)

    def get_session_summary(self) -> str:
        """
        Resumen de la sesión para inyectar en reflexiones.
        """
        stats = self.session_stats
        histogram = ", ".join(f"{k}: {v}" for k, v in stats["intents_histogram"].items()) or "ninguna"
        
        recent = ""
        if self.recent_actions:
            last_3 = list(self.recent_actions)[-3:]
            recent = " | ".join(
                f"{a['intent']}({a.get('status', 'ok')})" for a in last_3
            )
        
        return (
            f"[Sesión] Interacciones: {stats['interactions_count']}, "
            f"Errores: {stats['errors_count']}, "
            f"Mismatches: {stats['capability_mismatches']}, "
            f"Distribución: {histogram}, "
            f"Últimas: {recent or 'ninguna'}"
        )
    
    # =========================================================================
    # RECORDING: Registrar acciones y errores
    # =========================================================================
    
    def record_action(self, intent: str, user_input: str, response: str,
                      elapsed_time: float, status: str = "ok",
                      details: Optional[Dict] = None):
        """
        Registra cada acción completada para tracking.
        """
        self.session_stats["interactions_count"] += 1
        self.session_stats["total_response_time"] += elapsed_time
        self.session_stats["avg_response_time"] = (
            self.session_stats["total_response_time"] / 
            self.session_stats["interactions_count"]
        )
        
        # Histograma de intents
        self.session_stats["intents_histogram"][intent] = (
            self.session_stats["intents_histogram"].get(intent, 0) + 1
        )
        
        # Persistir cada 5 interacciones
        if self.session_stats["interactions_count"] % 5 == 0:
            self._save_session_stats()
        
        # Acción reciente
        self.recent_actions.append({
            "timestamp": datetime.now().isoformat(),
            "intent": intent,
            "user_input_preview": user_input[:80],
            "response_preview": response[:80],
            "elapsed_time": round(elapsed_time, 3),
            "status": status,
            "details": details,
        })
    
    def record_error(self, error_type: str, context: str, 
                     details: Optional[Dict] = None):
        """
        Registra un error detectado.
        """
        self.session_stats["errors_count"] += 1
        
        self.recent_actions.append({
            "timestamp": datetime.now().isoformat(),
            "intent": "error",
            "user_input_preview": context[:80],
            "response_preview": f"ERROR: {error_type}",
            "elapsed_time": 0,
            "status": "error",
            "details": details,
        })
    
    # =========================================================================
    # SKILLS INTEGRATION: Actualizar capacidades desde skills cargadas
    # =========================================================================

    # Mapa skill → efecto sobre capacidades/limitaciones
    _SKILL_CAPABILITY_MAP = {
        "system-monitor": {
            "removes_limitation_kw": "monitoreo del sistema",
            "flag": "_system_monitor_active",
            "capability_note": "system_monitoring",
        },
        "git_ops": {
            "removes_limitation_kw": "git",
            "flag": "_git_ops_active",
            "capability_note": "git_operations",
        },
        "reminders": {
            "removes_limitation_kw": "recordatorios",
            "flag": "_reminders_active",
            "capability_note": "reminders_management",
        },
        "knowledge_graph": {
            "flag": "_knowledge_graph_active",
            "capability_note": "knowledge_graph",
        },
        "file_ops": {
            "flag": "_file_ops_active",
            "capability_note": "advanced_file_ops",
        },
        "skill_manager": {
            "flag": "_skill_manager_active",
        },
    }

    def update_from_skills(self, registry) -> None:
        """
        Actualiza capacidades y limitaciones dinámicamente según skills activas.
        Llamar después de cargar skills en el orchestrator.
        """
        # Reset flags de skills conocidas y lista dinámica
        for cfg in self._SKILL_CAPABILITY_MAP.values():
            if "flag" in cfg:
                setattr(self, cfg["flag"], False)
        self._active_skills: List[str] = []
        self._active_skill_count: int = 0

        if registry is None:
            return

        try:
            active_skill_notes = []
            all_skills = list(registry.get_all_skills())

            for skill in all_skills:
                name = skill.metadata.name
                if not skill.enabled or name not in self._SKILL_CAPABILITY_MAP:
                    continue
                cfg = self._SKILL_CAPABILITY_MAP[name]
                # Activar flag
                if "flag" in cfg:
                    setattr(self, cfg["flag"], True)
                # Remover limitación relacionada
                if "removes_limitation_kw" in cfg:
                    kw = cfg["removes_limitation_kw"]
                    self.known_limitations = [
                        lim for lim in self.known_limitations
                        if kw not in lim.lower()
                    ]
                if "capability_note" in cfg:
                    active_skill_notes.append(cfg["capability_note"])

            # Capturar lista completa de skills activas (incluyendo las no mapeadas)
            self._active_skills = [
                skill.metadata.name
                for skill in all_skills
                if skill.enabled and skill.metadata.name
            ]
            self._active_skill_count = len(self._active_skills)

            # Compat: _system_monitor_active se usa en get_relevant_capabilities
            self._system_monitor_active = getattr(self, "_system_monitor_active", False)
        except Exception as _e:
            logger.warning("[self_state] update_from_skills failed: %s", _e)

    # =========================================================================
    # POST-PROCESS: Detección de capability_mismatch
    # =========================================================================
    
    def post_process(self, user_input: str, response: str, intent: str,
                     elapsed_time: float) -> Optional[Dict[str, Any]]:
        """
        Analiza la respuesta DESPUÉS de generarla.
        Detecta capability_mismatch: el LLM dice "no puedo" pero SÍ puede.
        
        Returns:
            None si todo OK, o un dict con el bug report si hay mismatch.
        """
        response_lower = response.lower()
        
        # --- Detección 1: LLM dice "no puedo" pero tenemos la capacidad ---
        denial_patterns = [
            "no puedo", "no soy capaz", "no tengo la capacidad",
            "no estoy diseñado para", "no me es posible",
            "lamentablemente no", "desafortunadamente no",
            "no tengo acceso", "no cuento con",
            "fuera de mis capacidades", "excede mis capacidades",
            # Denial forms that were missed (identified from Telegram logs):
            "no tengo pseudo", "no tengo conciencia", "no tengo consciencia",
            "soy un modelo de lenguaje", "soy un asistente de texto",
            "no tengo información", "no tengo datos",
        ]
        
        if any(pattern in response_lower for pattern in denial_patterns):
            # ¿Pero realmente NO puede? Verificar contra capabilities
            mismatch = self._check_capability_mismatch(user_input, response_lower)
            if mismatch:
                self.session_stats["capability_mismatches"] += 1
                bug_report = self._create_bug_report(
                    bug_type="capability_mismatch",
                    user_input=user_input,
                    response=response,
                    intent=intent,
                    details=mismatch
                )
                return bug_report
        
        # --- Detección 2: Respuesta excesivamente lenta ---
        if elapsed_time > 30.0 and intent != "vision":
            bug_report = self._create_bug_report(
                bug_type="performance_issue",
                user_input=user_input,
                response=response,
                intent=intent,
                details={
                    "elapsed_time": elapsed_time,
                    "threshold": 30.0,
                    "description": f"Respuesta tardó {elapsed_time:.1f}s (umbral: 30s)"
                }
            )
            return bug_report
        
        # --- Detección 3: Respuesta vacía o incoherente ---
        if len(response.strip()) < 5:
            bug_report = self._create_bug_report(
                bug_type="empty_response",
                user_input=user_input,
                response=response,
                intent=intent,
                details={"response_length": len(response.strip())}
            )
            return bug_report

        # --- Spike de errores: disparar reflexión anticipada ---
        if self.session_stats.get("capability_mismatches", 0) >= 3:
            self._needs_reflection = True
        interactions = max(1, self.session_stats["interactions_count"])
        error_rate = (self.session_stats["errors_count"] / interactions) * 100
        if error_rate >= 20.0 and interactions >= 5:
            self._needs_reflection = True

        return None
    
    def _check_capability_mismatch(self, user_input: str, response_lower: str) -> Optional[Dict]:
        """
        Verifica si hay un mismatch entre lo que el LLM dice y lo que realmente puede hacer.
        """
        input_lower = user_input.lower()

        # Exclusión: system monitoring es una limitación REAL (no mismatch)
        # a menos que la skill system-monitor esté activa
        system_keywords = ["ram", "cpu", "procesador", "disco duro", "espacio en disco",
                           "memoria del sistema", "procesos del sistema", "temperatura",
                           "monitoreo", "monitorear", "uso de memoria"]
        if any(kw in input_lower for kw in system_keywords):
            if not getattr(self, '_system_monitor_active', False):
                return None  # Es una limitación real, no un false denial

        # Mismatch: dice que no puede abrir apps, pero sí puede.
        # IMPORTANTE: igual que los detectores de vision/memory/voice, exige
        # (1) keyword de automation en el INPUT del usuario, y (2) una frase de
        # negación REAL en la respuesta. Antes esto disparaba por mera
        # co-ocurrencia verbo+app en input O response → falsos positivos: una
        # respuesta correcta ("puedo ejecutar la terminal para vos") quedaba
        # registrada como bug. Ver BUGS_ENCONTRADOS #1/#2/#5/#8.
        app_keywords = ["abrir", "abre", "lanzar", "ejecutar", "inicia", "abrime", "ejecutá", "abrí"]
        automation_denial_phrases = [
            "no puedo abrir", "no puedo ejecutar", "no puedo lanzar", "no puedo iniciar",
            "no tengo acceso", "no soy capaz de abrir", "no dispongo", "no tengo la capacidad",
            "no puedo controlar", "no tengo permitido abrir",
        ]
        if any(kw in input_lower for kw in app_keywords):
            if any(phrase in response_lower for phrase in automation_denial_phrases):
                for app in self.capabilities["automation"]["supported_apps"]:
                    if app in input_lower or app in response_lower:
                        return {
                            "type": "automation_denial",
                            "claimed": "No puede abrir aplicaciones",
                            "reality": f"Sí puede abrir '{app}' — está en la whitelist",
                            "app": app
                        }
        
        # Mismatch: dice que no puede ver la pantalla, pero sí puede
        vision_keywords = ["pantalla", "screen", "captura", "screenshot", "ver mi pantalla", "qué ves", "que ves"]
        if any(kw in input_lower for kw in vision_keywords):
            if "no puedo ver" in response_lower or "no tengo acceso" in response_lower:
                return {
                    "type": "vision_denial",
                    "claimed": "No puede ver la pantalla",
                    "reality": "Sí puede capturar y analizar la pantalla vía módulo vision"
                }
        
        # Mismatch: dice que no recuerda, pero tiene memoria
        memory_keywords = ["recuerdas", "recuerda", "te acuerdas", "memoria", "recordar"]
        if any(kw in input_lower for kw in memory_keywords):
            if "no tengo memoria" in response_lower or "no puedo recordar" in response_lower:
                return {
                    "type": "memory_denial",
                    "claimed": "No tiene memoria",
                    "reality": "Sí tiene memoria vectorial persistente (PGVector/JSON)"
                }
        
        # Mismatch: dice que no puede hablar/escuchar, pero tiene TTS/STT
        voice_keywords = ["habla", "escucha", "voz", "audio", "di algo", "lee en voz alta"]
        if any(kw in input_lower for kw in voice_keywords):
            if "no puedo hablar" in response_lower or "no tengo voz" in response_lower:
                return {
                    "type": "voice_denial",
                    "claimed": "No puede hablar",
                    "reality": "Sí tiene TTS (Piper local + Gemini cloud) y STT (Whisper)"
                }

        # Mismatch: dice que no puede analizar código, pero tiene GitNexus
        code_keywords = ["código", "codigo", "repo", "dependencias", "imports", "impacto", "refactor"]
        if any(kw in input_lower for kw in code_keywords):
            if "no puedo analizar" in response_lower or "no tengo acceso al código" in response_lower:
                return {
                    "type": "code_analysis_denial",
                    "claimed": "No puede analizar código",
                    "reality": "Sí puede analizar código vía GitNexus (knowledge graph, búsqueda híbrida, análisis de impacto)"
                }

        # Mismatch: dice que no puede buscar en internet, pero tiene Tavily + fallbacks
        web_keywords = ["buscar", "búsqueda", "busqueda", "internet", "web", "googlear", "investigar"]
        if any(kw in input_lower for kw in web_keywords):
            if "no puedo buscar" in response_lower or "no tengo acceso a internet" in response_lower:
                return {
                    "type": "web_search_denial",
                    "claimed": "No puede buscar en internet",
                    "reality": "Sí tiene búsqueda web con 5 capas de fallback (Tavily, DuckDuckGo, Google scraping)"
                }

        # Mismatch: dice que no puede acceder a archivos, pero tiene módulo filesystem
        file_keywords = ["archivo", "fichero", "carpeta", "directorio", "file", "leer archivo", "escribir archivo"]
        if any(kw in input_lower for kw in file_keywords):
            if "no tengo acceso" in response_lower or "no puedo acceder a archivos" in response_lower:
                return {
                    "type": "filesystem_denial",
                    "claimed": "No tiene acceso al sistema de archivos",
                    "reality": "Sí tiene módulo filesystem con operaciones de lectura, escritura y Git"
                }

        # Mismatch: dice que no aprende, pero tiene módulo de learning
        learning_keywords = ["aprend", "retroalimentación", "feedback", "mejora con el uso", "adapta"]
        if any(kw in input_lower for kw in learning_keywords):
            if "no aprendo" in response_lower or "no puedo aprender" in response_lower:
                return {
                    "type": "learning_denial",
                    "claimed": "No aprende de interacciones",
                    "reality": "Sí tiene sistema de learning (feedback, few-shot selection, style profiling, reflection engine)"
                }

        # Mismatch: dice que no tiene pseudo-consciencia
        consciousness_keywords = ["consciencia", "conciencia", "self state", "auto-awareness"]
        if any(kw in input_lower for kw in consciousness_keywords):
            if "no tengo" in response_lower and ("conciencia" in response_lower or "consciencia" in response_lower):
                return {
                    "type": "consciousness_denial",
                    "claimed": "No tiene pseudo-consciencia",
                    "reality": "Sí tiene sistema de pseudo-consciencia (self state, post-process monitoring, reflection, bug reports)"
                }

        # Mismatch: dice que no sabe la hora, pero tiene módulo timezone
        time_keywords = ["hora", "qué hora", "que hora", "tiempo actual", "hora actual", "qué día", "que dia"]
        if any(kw in input_lower for kw in time_keywords):
            if any(p in response_lower for p in ["no sé la hora", "no se la hora", "no tengo acceso al reloj",
                                                  "no puedo saber la hora", "no tengo información de la hora",
                                                  "no tengo acceso a la hora", "no sé qué hora"]):
                return {
                    "type": "timezone_denial",
                    "claimed": "No sabe la hora actual",
                    "reality": "Sí tiene módulo timezone con 150+ zonas horarias basado en el reloj real del servidor"
                }

        # Mismatch: dice que no tiene datos de crypto/finanzas/wikipedia, pero tiene integrations
        integration_keywords = ["precio", "cotización", "cotizacion", "bitcoin", "crypto", "criptomoneda",
                                 "ethereum", "acción", "accion", "bolsa", "wikipedia", "rss", "feed"]
        if any(kw in input_lower for kw in integration_keywords):
            if any(p in response_lower for p in ["no tengo acceso a datos", "no puedo consultar precios",
                                                  "no tengo información en tiempo real", "no puedo acceder a esa información",
                                                  "no tengo datos de"]):
                return {
                    "type": "integrations_denial",
                    "claimed": "No tiene acceso a datos en tiempo real",
                    "reality": "Sí tiene integrations: CoinGecko (crypto), Wikipedia, RSS feeds, Finance API"
                }

        # Mismatch: dice que no puede consultar Canvas, pero tiene la skill
        canvas_keywords = ["canvas", "tareas pendientes", "entregas faltantes", "calificaciones"]
        if any(kw in input_lower for kw in canvas_keywords):
            if any(p in response_lower for p in ["no puedo acceder a canvas", "no tengo acceso a canvas",
                                                  "no puedo consultar canvas", "no tengo integración con canvas",
                                                  "no tengo integracion con canvas", "no tengo forma de filtrar",
                                                  "no puedo revisar canvas"]):
                return {
                    "type": "canvas_denial",
                    "claimed": "No puede acceder a Canvas",
                    "reality": "Sí tiene integración con Canvas LMS (tareas, notas, entregas faltantes, anuncios, calendario)"
                }

        return None
    
    # =========================================================================
    # BUG REPORTS: Structured logging
    # =========================================================================
    
    def _create_bug_report(self, bug_type: str, user_input: str, response: str,
                           intent: str, details: Dict) -> Dict[str, Any]:
        """
        Crea un bug report estructurado (nunca un patch sugerido).
        """
        report = {
            "id": len(self.bug_reports) + 1,
            "timestamp": datetime.now().isoformat(),
            "bug_type": bug_type,
            "severity": self._classify_severity(bug_type),
            "user_input": user_input[:200],
            "response_snippet": response[:200],
            "intent": intent,
            "details": details,
            "session_interactions": self.session_stats["interactions_count"],
        }
        
        self.bug_reports.append(report)
        # Guardar si han pasado 60s desde el último guardado
        if time.time() - self._last_bug_save_time > 60:
            self._save_bug_reports()
            self._last_bug_save_time = time.time()

        # Sincronizar con BUGS_ENCONTRADOS.md para visibilidad humana
        self._sync_bugs_to_md()

        return report

    def _sync_bugs_to_md(self) -> None:
        """
        Sincroniza los bug reports al archivo BUGS_ENCONTRADOS.md
        para que el usuario pueda verlos y tomar acción sin revisar logs.
        """
        try:
            if not self.bug_reports:
                return

            lines = [
                "# BUGS ENCONTRADOS — Auto-detectados",
                "",
                f"> Actualizado: {datetime.now().isoformat()}",
                f"> Total bugs: {len(self.bug_reports)}",
                "",
            ]

            for bug in self.bug_reports[-30:]:
                severity = bug.get("severity", "unknown").upper()
                bug_type = bug.get("bug_type", "unknown")
                timestamp = bug.get("timestamp", "desconocido")
                user_input = bug.get("user_input", "")[:100]
                response = bug.get("response_snippet", "")[:100]
                details = bug.get("details", {})

                lines.append("---")
                lines.append("")
                lines.append(f"## Bug [{severity}] {bug_type}")
                lines.append(f"**Timestamp:** {timestamp}")
                lines.append(f"**Input:** {user_input}")
                lines.append(f"**Response:** {response}")
                if details:
                    lines.append(f"**Detalles:** {json.dumps(details, ensure_ascii=False, indent=2)}")
                lines.append("")

            self.BUGS_MD_FILE.write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[SelfState] Error sync bugs to md: {e}")
    
    def _classify_severity(self, bug_type: str) -> str:
        """Clasifica la severidad del bug."""
        severity_map = {
            "capability_mismatch": "medium",
            "performance_issue": "low",
            "empty_response": "high",
            "hallucination": "high",
        }
        return severity_map.get(bug_type, "low")
    
    # =========================================================================
    # REFLECTION: Análisis bajo demanda (/reflect)
    # =========================================================================
    
    def build_reflection_prompt(self) -> Optional[str]:
        """
        Construye el prompt para reflexión (se envía a cloud LLM).
        Returns None si no hay suficientes datos para reflexionar.
        """
        if self.session_stats["interactions_count"] < 5 and not self.bug_reports:
            return None
        
        # Construir resumen de la sesión
        session_summary = self.get_session_summary()
        
        # Construir lista de bug reports recientes (últimos 20)
        recent_bugs = self.bug_reports[-20:] if self.bug_reports else []
        bugs_text = ""
        if recent_bugs:
            bugs_text = "BUG REPORTS RECIENTES:\n"
            for bug in recent_bugs:
                bugs_text += (
                    f"  [{bug['timestamp']}] {bug['bug_type']} (severity: {bug['severity']})\n"
                    f"    Input: {bug['user_input'][:100]}\n"
                    f"    Response: {bug['response_snippet'][:100]}\n"
                    f"    Details: {json.dumps(bug['details'], ensure_ascii=False)[:200]}\n\n"
                )
        
        # Construir lista de acciones recientes
        actions_text = "ACCIONES RECIENTES:\n"
        for action in self.recent_actions:
            actions_text += (
                f"  [{action['timestamp']}] {action['intent']} "
                f"(status: {action['status']}, time: {action['elapsed_time']}s)\n"
                f"    Input: {action['user_input_preview']}\n"
            )
        
        prompt = (
            "Eres un sistema de meta-análisis para Zephyro, un asistente de IA.\n"
            "Tu tarea es analizar los datos de la sesión y bug reports para identificar:\n"
            "1. Patrones recurrentes de errores\n"
            "2. Capability mismatches (el LLM dice que no puede hacer algo que sí puede)\n"
            "3. Problemas de rendimiento\n"
            "4. Sugerencias de mejora (sin escribir código, solo describir el problema)\n\n"
            f"ESTADO DE LA SESIÓN:\n{session_summary}\n\n"
            f"{actions_text}\n"
            f"{bugs_text}\n"
            "IDENTIDAD DEL SISTEMA:\n"
            f"{json.dumps(self.capabilities, ensure_ascii=False, indent=2)[:2000]}\n\n"
            "Responde con un análisis estructurado en español:\n"
            "## Patrones Detectados\n"
            "## Problemas Críticos\n"
            "## Sugerencias de Mejora\n"
            "## Estado General del Sistema\n"
        )
        
        return prompt
    
    def get_bug_reports_summary(self) -> Dict[str, Any]:
        """Resumen de bug reports para la API."""
        if not self.bug_reports:
            return {"total": 0, "by_type": {}, "by_severity": {}, "recent": []}
        
        by_type = {}
        by_severity = {}
        for bug in self.bug_reports:
            by_type[bug["bug_type"]] = by_type.get(bug["bug_type"], 0) + 1
            by_severity[bug["severity"]] = by_severity.get(bug["severity"], 0) + 1
        
        return {
            "total": len(self.bug_reports),
            "by_type": by_type,
            "by_severity": by_severity,
            "recent": self.bug_reports[-5:]  # Últimos 5
        }
    
    def clear_bug_reports(self):
        """Limpia los bug reports después de una reflexión exitosa."""
        self.bug_reports.clear()
        self._save_bug_reports()
    
    # =========================================================================
    # PERSISTENCIA
    # =========================================================================
    
    def _load_session_stats(self):
        """Carga session_stats: resetea contadores por sesión, preserva historial acumulado."""
        try:
            if self.SESSION_STATS_FILE.exists():
                with open(self.SESSION_STATS_FILE, "r") as f:
                    saved = json.load(f)
                # Preservar historial acumulado entre sesiones
                if "historical_stats" in saved:
                    self.session_stats["historical_stats"] = saved["historical_stats"]
                # Los contadores de sesión SIEMPRE arrancan en 0
                # (interactions_count, errors_count, etc. son por sesión, no globales)
                total_prev = saved.get("interactions_count", 0)
                if total_prev > 0:
                    logger.info(f"[SelfState] Sesión nueva. Sesión anterior: {total_prev} interacciones.")
        except Exception as e:
            logger.warning(f"[SelfState] No se pudieron cargar session_stats: {e}")
    
    def _save_session_stats(self, final: bool = False):
        """Persiste session_stats al disco.

        Si final=True (solo al exit), acumula los contadores de esta sesión
        al histórico. Los guardados intermedios solo actualizan la sesión actual
        para evitar inflar el histórico en cada save.
        """
        try:
            self.SESSION_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
            historical = self.session_stats.get("historical_stats", {
                "total_interactions": 0,
                "total_errors": 0,
                "sessions_count": 0,
            })
            if final:
                # Solo acumular al histórico una vez, al terminar la sesión
                historical["total_interactions"] += self.session_stats.get("interactions_count", 0)
                historical["total_errors"] += self.session_stats.get("errors_count", 0)
                historical["sessions_count"] += 1
            to_save = {**self.session_stats, "historical_stats": historical}
            with open(self.SESSION_STATS_FILE, "w") as f:
                json.dump(to_save, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[SelfState] Error guardando session_stats: {e}")

    def _load_bug_reports(self):
        """Carga bug reports previos del disco."""
        try:
            if self.BUG_REPORTS_FILE.exists():
                with open(self.BUG_REPORTS_FILE, "r", encoding="utf-8") as f:
                    self.bug_reports = json.load(f)
                logger.info(f"[SelfState] Cargados {len(self.bug_reports)} bug reports previos")
        except Exception as e:
            logger.warning(f"[SelfState] No se pudieron cargar bug reports: {e}")
            self.bug_reports = []

    def _save_bug_reports(self):
        """Persiste bug reports al disco."""
        try:
            self.BUG_REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.BUG_REPORTS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.bug_reports, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[SelfState] Error guardando bug reports: {e}")
    
    def _flush_on_exit(self):
        """Garantiza persistencia de bug reports y session_stats al salir."""
        self._save_bug_reports()
        self._save_session_stats(final=True)

    def export_state(self) -> Dict[str, Any]:
        """Exporta el estado completo para debugging/API."""
        return {
            "identity": self.identity,
            "capabilities": self.capabilities,
            "known_limitations": self.known_limitations,
            "session_stats": self.session_stats,
            "recent_actions": list(self.recent_actions),
            "bug_reports_summary": self.get_bug_reports_summary(),
        }