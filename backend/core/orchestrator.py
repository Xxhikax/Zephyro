from core.llm_manager import LLMManager
from core.context_manager import ContextManager
from core.router import IntentRouter
from core.app_config import AUTO_VERBS, NATIVE_APPS, SITE_KEYWORDS
from core.model_catalog import ANTHROPIC_MODELS, cloud_chat_model
from core.self_state import SelfState
from core.llm_cache import LLMCache
from core.response_sanitizer import sanitize as sanitize_response
from core.request_context import RequestContext
from core.confidence import confidence_of
from core import clarification_gate as _gate
from core.action_risk_rules import classify_action as _classify_action_risk
from core import task_decomposer as _decomposer
from core import verbs as _verbs
from core.browser_intent_schema import extract_browser_intent
from core.cloud_intent_classifier import classify_intent
from core.task_envelope import TaskEnvelope, StepRecord
import core.fast_responses as _fast_resp
from skills.registry import SkillRegistry
from skills.loader import SkillLoader
from modules.voice.tts import TTS
from modules.voice.stt import STT
from modules.automation.app_controller import AppController
from modules.vision.screen_capture import ScreenCapturer
from modules.vision.image_analysis import ImageAnalyzer
from modules.memory.conversation_history import ConversationHistory
from modules.memory.importance_classifier import ImportanceClassifier
from modules.schedule.service import get_schedule_service
from rich.console import Console
from rich.table import Table
from typing import Optional
import asyncio
import logging
import re
import os
import random
import time
import secrets
import threading

# ── Nuevos módulos (opcionales) ──────────────────────────────────────
try:
    from modules.integrations.api_registry import (
        query_external_api, is_external_api_query,
        search_web_direct, needs_web_search,
        _ALWAYS_SEARCH_PATTERNS,
    )
    HAS_API_REGISTRY = True
except ImportError as _api_import_err:
    HAS_API_REGISTRY = False
    import sys as _sys
    print(f"[Orchestrator] ✗ API Registry DISABLED: {_api_import_err}", file=_sys.stderr)

try:
    from modules.search.tools import SEARCH_TOOL, execute_search as _execute_search_tool
    from modules.search.web_search import WebSearch as _WebSearch
    HAS_SEARCH_TOOLS = True
except ImportError:
    HAS_SEARCH_TOOLS = False

try:
    from core.skill_tools import (
        DIAGNOSE_TOOL, INTROSPECT_TOOL, READ_FILE_TOOL,
        is_path_allowed as _is_skill_path_allowed,
        PROJECT_ROOT as _SKILL_PROJECT_ROOT,
    )
    HAS_SKILL_TOOLS = True
except ImportError:
    HAS_SKILL_TOOLS = False

# NOTE: knowledge_graph, reminders, file_ops, git_ops migrados a skills/builtin/

try:
    from modules.memory_extended.user_profile import UserProfile
    HAS_USER_PROFILE = True
except ImportError:
    HAS_USER_PROFILE = False

try:
    from modules.memory_extended.conversation_summarizer import ConversationSummarizer
    HAS_SUMMARIZER = True
except ImportError:
    HAS_SUMMARIZER = False

try:
    from modules.memory_extended.session_workspace import SessionWorkspace
    HAS_WORKSPACE = True
except ImportError:
    HAS_WORKSPACE = False

# GitNexus bridge (optional — needs Node.js + gitnexus)
try:
    from modules.automation.gitnexus_bridge import GitNexusBridge
    HAS_GITNEXUS_MODULE = True
except ImportError:
    HAS_GITNEXUS_MODULE = False

# Fase 1 streaming-tts-backend: synthesize_tts necesita estar en el scope del módulo
# para que los tests puedan hacer mock vía patch("core.orchestrator.synthesize_tts").
# Se importa con guard para no romper el módulo si routers.voice no está disponible.
try:
    from routers.voice import synthesize_tts
except ImportError:
    synthesize_tts = None  # type: ignore[assignment]

# ── Módulos de aprendizaje adaptativo (opcionales) ───────────────────
try:
    from modules.learning.feedback_system   import FeedbackSystem
    from modules.learning.few_shot_selector import DynamicFewShotSelector
    from modules.learning.reflection_engine import ReflectionEngine
    from modules.learning.style_profiler    import StyleProfiler
    from modules.learning.training_curator  import TrainingDataCurator
    from modules.learning.finetune_manager  import FineTuneManager
    HAS_ADAPTIVE_LEARNING = True
except ImportError as _al_err:
    HAS_ADAPTIVE_LEARNING = False
    print(f"[Orchestrator] Aprendizaje adaptativo deshabilitado: {_al_err}")

# ── Frases para fillers de espera (variedad, sensibles a contexto) ───
THINKING_PHRASES = [
    "Déjame procesar eso...",
    "Un segundo, lo estoy pensando.",
    "Procesando, dame un momento.",
    "Estoy reflexionando sobre eso.",
    "Un momento mientras lo elaboro.",
    "Dame un instante para resolverlo...",
    "Estoy en ello...",
    "Permíteme analizarlo con calma.",
]
THINKING_AUTOMATION = [
    "Voy a eso...",
    "Ejecutando...",
    "Dame un segundo...",
    "Resolviendo...",
    "Manos a la obra...",
    "Abriendo lo que necesitas...",
]
THINKING_COMPLEX = [
    "Esto requiere análisis detallado...",
    "Dejame investigar a fondo...",
    "Procesando información compleja...",
    "Dame un minuto, pensándolo bien...",
    "Esto es denso, voy con cuidado...",
    "Analizando cada detalle...",
]
THINKING_CODE = [
    "Analizando la estructura del código...",
    "Revisando dependencias...",
    "Mapeando el repositorio...",
    "Examinando el código fuente...",
    "Trazando las conexiones del proyecto...",
]
THINKING_VISION = [
    "Capturando pantalla...",
    "Procesando imagen...",
    "Analizando lo que veo...",
    "Mirando tu pantalla...",
    "Interpretando la interfaz...",
]
SEARCHING_PHRASES = [
    "Un momento, estoy buscando información...",
    "Buscando datos relevantes en internet...",
    "Déjame consultar fuentes externas.",
    "Recopilando información actualizada...",
    "Un segundo mientras busco eso.",
    "Consultando la web...",
    "Rastreando fuentes confiables...",
    "Buscando en tiempo real...",
]

# Intents de actividad "lenta" — los que justifican un filler de espera aunque
# la complejidad sea simple (abrir apps, buscar, analizar código/pantalla, etc).
# Una pregunta/chat simple, aunque sea larga, NO está acá: no merece "Esto es denso".
_SLOW_INTENTS = frozenset({
    "automation", "search", "web_search", "code_analysis",
    "vision", "screen_query", "diagnostic",
})

def _should_emit_filler(intent: str = "", complexity: str = "") -> bool:
    """True si vale la pena un filler de espera: complejidad alta o actividad lenta.
    Deliberadamente NO usa la longitud del input — una instrucción simple pero
    larga no debe disparar un filler de 'análisis profundo'."""
    return complexity == "complex" or intent in _SLOW_INTENTS

def _start_search_gate(user_input: str, force_local: bool) -> Optional[asyncio.Task]:
    """Speculatively start the tool-loop "needs search?" classifier.

    It depends only on `user_input`, so it runs concurrently with the router's LLM
    escalation instead of after it (they were ~1.2s + ~1.5s back to back). Returns None
    when the tool loop can't run or the regex tiers already decide (no LLM call to hide).
    """
    if not HAS_SEARCH_TOOLS or force_local or not os.getenv("ANTHROPIC_API_KEY", ""):
        return None
    try:
        from modules.integrations.api_registry import (
            _tool_loop_needs_search,
            _tool_loop_search_heuristic,
        )
    except Exception:
        return None
    if _tool_loop_search_heuristic(user_input) is not None:
        return None
    task = asyncio.create_task(_tool_loop_needs_search(user_input))
    # A discarded gate (turn resolved to a skill/automation) must never log
    # "Task exception was never retrieved".
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    return task


_NO_REASONING_INTENTS = ("automation", "search", "web_search", "vision", "code_analysis", "skill")


def _decide_reasoning(user_input: str, analysis: dict, complexity: str, user_forced: bool):
    """ReasoningDecision to pass to the LLM for this turn, or None (= provider default).

    Logged in every mode but "off", so shadow mode collects real turns without
    changing any request (see core/reasoning_policy.py).
    """
    if analysis.get("fast_path") or analysis.get("intent") in _NO_REASONING_INTENTS:
        return None  # answered by a template, a skill or an automation: no chat LLM call
    try:
        from core.reasoning_policy import pick_reasoning, policy_mode
        mode = policy_mode()
        if mode == "off":
            return None
        decision = pick_reasoning(
            user_input,
            intent=analysis.get("intent"),
            complexity=complexity,
            rich_hint=analysis.get("rich_hint"),
            detail_level=analysis.get("detail_level"),
            user_forced=user_forced,
        )
    except Exception as exc:  # the policy must never break a turn
        logging.getLogger("zephyro.reasoning").warning("reasoning policy failed: %s", exc)
        return None
    applied = mode == "on" or user_forced
    logging.getLogger("zephyro.reasoning").info(
        "reasoning mode=%s applied=%s level=%s kind=%s reason=%s",
        mode, applied, decision.level, decision.kind, decision.reason,
    )
    return decision if applied else None


def _reasoning_kw(reasoning) -> dict:
    """Pass `reasoning` only when set, so None keeps every call exactly as before."""
    return {"reasoning": reasoning} if reasoning is not None else {}


def _pick_thinking(intent: str = "", complexity: str = "") -> str:
    # Frase acorde a la ACTIVIDAD real, no a la longitud del texto.
    if intent in ("search", "web_search"):
        return random.choice(SEARCHING_PHRASES)
    if intent == "automation":
        return random.choice(THINKING_AUTOMATION)
    if intent == "code_analysis" or "code" in intent:
        return random.choice(THINKING_CODE)
    if intent in ("vision", "screen_query"):
        return random.choice(THINKING_VISION)
    # Solo "análisis denso" cuando la complejidad es realmente alta (o diagnóstico).
    if intent == "diagnostic" or complexity == "complex":
        return random.choice(THINKING_COMPLEX)
    # Default: frase neutra y corta (sirve para preguntas/chat, incluso largos).
    return random.choice(THINKING_PHRASES)

def _pick_searching() -> str:
    return random.choice(SEARCHING_PHRASES)

# ── Cloud fast-lane: detección barata de automation/vision antes de invocar el router ──
# Study session break reminder threshold (50 min of consecutive study-like interactions)
_STUDY_BREAK_SECS = int(os.getenv("STUDY_BREAK_MINUTES", "50")) * 60
# Regex to detect study/work interactions (used to track session continuity)
_STUDY_INTENT_RE = re.compile(
    r'\b(?:código|code|analiz|debug|bug|error|implement|refactor|función|funcion'
    r'|clase|módulo|modulo|test|estudia|estudiar|proyecto|ejercicio|calculo|cálculo'
    r'|apuntes|tarea|práctica|practica|laboratorio|examen|parcial)\b',
    re.IGNORECASE,
)

# Respuestas a una clarificación del gate (cuando es candidato único, "sí" la
# confirma; "no" la omite). Usadas por _resolve_pending_clarification.
_CLARIFY_AFFIRM = re.compile(
    r'^(?:s[ií]|dale|ok(?:ay|a)?|hazlo|hacelo|haz\s+eso|hac[eé]\s+eso|confirmo'
    r'|obvio|claro|correcto|exacto|eso(?:\s+mismo)?|adelante|proced[eé]|vamos'
    r'|por\s*favor|porfa|porfavor|afirmativo)\b',
    re.IGNORECASE,
)
_CLARIFY_NEGATE = re.compile(
    r'^(?:no|nop|nel|negativo|cancel[aá]|olvidalo|olv[ií]dalo|dejalo|d[eé]jalo'
    r'|mejor\s+no|para\s+nada|ninguno|ninguna)\b',
    re.IGNORECASE,
)

# Cross-turn buffer (call mode): distingue fragmento de VAD vs comando completo.
# Un fragmento real ("hasta las 11 y 10", "a las 8 de la mañana") es corto y no
# contiene verbo imperativo. Un comando completo que arranca con preposición
# ("Para el día de mañana en mi agenda pon el curso...") NO debe fusionarse con
# el buffer: el turno anterior pudo ser chat puro y contamina el título del
# evento (bug en vivo 2026-07-02: evento titulado con la pregunta anterior).
_CALL_TURN_CONTINUATION = re.compile(
    r'^(?:de|a|las|y|con|hasta|desde|por|para|en|del)\s+\w+'
    r'|^\d{1,2}(?:\s*(?:y|con)\s*\d{1,2})?(?:\s*(?:am|pm|de\s+la\s+\w+))?'
    r'|^(?:de|a)\s+la\s+\w+'
    r'|^(?:de|a)\s+las?\s+\d',
    re.IGNORECASE,
)
_CALL_TURN_CMD_VERB = re.compile(
    r'\b(?:pon(?:e|me|le)?|agenda(?:me)?|ag[eé]ndame|anota|a[ñn]ade|agrega|apunta'
    r'|programa|crea|elimina|borra|cancela|abre|abr[ií]|cierra|busca|investiga'
    r'|recu[eé]rdame|modifica|cambia|mueve|reproduce|inicia|lanza|ejecuta)\b',
    re.IGNORECASE,
)
_CALL_TURN_FRAGMENT_MAX_WORDS = 12


def _is_call_turn_fragment(text: str) -> bool:
    """True si el turno parece un fragmento partido por el VAD (mergeable)."""
    t = (text or "").strip()
    if not t or len(t.split()) > _CALL_TURN_FRAGMENT_MAX_WORDS:
        return False
    if _CALL_TURN_CMD_VERB.search(t):
        return False
    return bool(_CALL_TURN_CONTINUATION.match(t))


# Fresh-info enhancement (v4): carrera entre la respuesta rápida y la verificación
# contra la fuente más autoritativa. Si la verificación no llega a tiempo queda
# pendiente y el follow-up del usuario la consume (one-shot, con TTL).
_ENHANCEMENT_RACE_TIMEOUT = 2.5   # segundos

# Streaming TTS: sentences synthesized concurrently per turn (frames still emitted in
# order). 2 hides one synthesis behind the playback of the previous sentence without
# piling Piper subprocesses / Fish Audio requests.
_STREAM_TTS_INFLIGHT = 2
_EXTRA_SEARCH_TTL = 120           # segundos
# Afirmaciones propias de la oferta "¿Querés que te la muestre?" que
# _CLARIFY_AFFIRM no cubre.
_EXTRA_SEARCH_AFFIRM = re.compile(
    r'^\s*(?:mu[eé]strame(?:la)?|mostrame(?:la)?|quiero\s+verla?|a\s+ver)\b',
    re.IGNORECASE,
)

# Si el input matchea alguno de estos patrones, se delega al router normal incluso en
# force_cloud para no perder el path de Playwright/screenshot.
# Verbos derivados de la fuente única (core/verbs.py) + frases de navegación
# específicas (que no son verbos puros y se mantienen acá).
_RE_CLOUD_AUTOMATION = re.compile(
    r'\b' + _verbs.verbs_pattern("automation", "media", "search", "navigation") + r'\b'
    r'|abre\s+la\s+pág|abre\s+la\s+pag'
    r'|ve\s+a|ir\s+a|anda\s+a|dir[ií]gete\s+a',
    re.IGNORECASE,
)
_RE_CLOUD_VISION = re.compile(
    r'\b(captura|toma\s+pantalla|screenshot|pantallazo|foto\s+a\s+la\s+pantalla'
    r'|analiza\s+(?:la\s+|mi\s+)?pantalla'
    r'|mira\s+(?:la\s+|mi\s+)?pantalla'
    r'|lee\s+(?:la\s+|mi\s+)?pantalla'
    r'|ve\s+mi\s+pantalla|ves\s+mi\s+pantalla'
    r'|describe\s+(?:la\s+|mi\s+)?pantalla'
    r'|que\s+(?:hay|ves)\s+en\s+mi\s+pantalla)\b',
    re.IGNORECASE,
)
# Diagnostic / self-introspection — keywords que deben pasar por router incluso
# en cloud mode (de otro modo el LLM alucina logs y skills inexistentes).
_RE_CLOUD_DIAGNOSTIC = re.compile(
    r'\bdiagn[oó]stico\b'
    r'|\bdiagnostica(?:te|me)?\b'
    r'|\bdiagnost[ií]cate\b|\bdiagnost[ií]came\b'
    r'|\brevisa?\s+(?:los\s+)?(?:errores|logs|problemas|bugs)\b'
    r'|\banaliza\s+(?:los\s+)?(?:errores|logs)\b'
    # Guards alineados con _RE_DIAGNOSTIC (router.py): "por qué pasa X" /
    # "qué está pasando con las elecciones" son preguntas de contenido, no
    # pedidos de diagnóstico. Sin esto, la misma frase clasificaba distinto
    # según entrara por el path local (router) o el de cloud (este regex).
    #   TILDE OBLIGATORIA: "que" sin tilde es el relativo ("las funciones que
    #   tienes") y no pide un diagnóstico. Ver _RE_DIAGNOSTIC en router.py.
    r'|(?<!por )\bqu[éè]\s+(?:te\s+)?(?:pasa|pas[oó]|tienes|sucede)\b(?!\s+(?:con|sobre|eso|esto))'
    r'|(?:^|¿)\s*que\s+(?:te\s+)?(?:pasa|pas[oó]|tienes|sucede)\b(?!\s+(?:con|sobre|eso|esto))'
    r'|(?<!por )\bqu[éè]\s+est[aá]\s+pas(?:ando|ó)\b(?!\s+(?:con|sobre|eso|esto))'
    r'|(?:^|¿)\s*que\s+est[aá]\s+pas(?:ando|ó)\b(?!\s+(?:con|sobre|eso|esto))'
    r'|\bya\s+tienes\s+(?:el\s+)?(?:diagn[oó]stico|resultados?|an[aá]lisis)\b'
    r'|\btienes\s+(?:los?\s+)?(?:resultados?|diagn[oó]stico)\b'
    r'|\bqu[eé]\s+encontraste\b'
    r'|\bterminaste\s+(?:el\s+)?(?:diagn[oó]stico|an[aá]lisis)\b',
    re.IGNORECASE,
)
# Follow-up queries ONLY (subset of _RE_CLOUD_DIAGNOSTIC). Used by
# _handle_diagnostic() to distinguish "give me the result" from
# "start a new diagnostic" — the former returns the stored summary,
# the latter starts a fresh DiagnosticAgent.
_RE_DIAGNOSTIC_FOLLOWUP = re.compile(
    r'\bya\s+tienes\s+(?:el\s+)?(?:diagn[oó]stico|resultados?|an[aá]lisis)\b'
    r'|\btienes\s+(?:los?\s+)?(?:resultados?|diagn[oó]stico)\b'
    r'|\bqu[eé]\s+encontraste\b'
    r'|\bterminaste\s+(?:el\s+)?(?:diagn[oó]stico|an[aá]lisis)\b',
    re.IGNORECASE,
)

# Consultas "table-worthy": comparaciones/organización de datos donde una tabla
# Markdown mejora la respuesta. Los modelos locales (1b/3b) emiten tablas rotas,
# así que estas queries nunca van por fast_path y prefieren cloud (ver
# handle_input → rich_hint). Patrón compartido con router CAPA 2 (core/verbs.py).
from core.verbs import TABLE_WORTHY_PATTERN as _RE_TABLE_WORTHY


class Orchestrator:
    def __init__(self, cloud_provider="auto"):
        # SISTEMA HÍBRIDO DE 3 NIVELES
        
        self.console = Console()
        self.specialty_mode = None  # Modo especialidad: None o 'educational_assistant'
        self.tts_mode = "auto"  # Modo TTS: "auto", "piper", "gemini"
        self.authenticated = False  # Estado de autenticación
        self.user_role = None  # 'admin' o 'guest'
        self.user_name = None  # Nombre del usuario (admin: "Bruno", guest: se pregunta)
        self.session_id = secrets.token_hex(8)  # ID único de sesión
        self.force_remember_next = False  # Flag para !recordar
        # Automatizaciones pendientes de confirmación, por session_id. Todos los
        # canales comparten este orchestrator: con un slot global, un "sí" en
        # Telegram confirmaba una acción encolada desde el desktop.
        self._pending_automations: dict[str, dict] = {}
        self._pending_clarification = None  # Clarificación pendiente (gate de baja confianza / multi-tarea)
        self._pending_clarification_lock = asyncio.Lock()
        self._pending_extra_search_task: Optional[asyncio.Task] = None  # Enhancement v4 corriendo en background
        self._pending_extra_search_ts: float = 0.0  # Timestamp de la oferta pendiente (TTL)
        self._last_web_search_topic: Optional[str] = None  # Tema de la última búsqueda web
        self._envelope_store = None       # TaskEnvelopeStore inyectado desde web_app lifespan

        # Modelos locales configurables vía env (defaults preservados). Permite cambiar a
        # p.ej. qwen2.5:1.5b sin editar código: ZEPHYRO_MODEL_FAST=qwen2.5:1.5b
        _model_fast = os.getenv("ZEPHYRO_MODEL_FAST", "llama3.2:1b")
        _model_balanced = os.getenv("ZEPHYRO_MODEL_BALANCED", "llama3.2:3b")

        # NIVEL 1: Ultra-rápido para triviales (saludos, despedidas)
        self.llm_fast = LLMManager(
            local_model=_model_fast,
            preset="rapido"
        )

        # Clasificador de búsqueda web: Haiku (rápido+preciso) con fallback a 1b local
        # Se inicializa después de self.llm para reutilizar detección de Anthropic key
        self.llm_classifier: Optional[LLMManager] = None
        
        # NIVEL 2: Balanceado para uso general (conceptos, chat normal)
        self.llm = LLMManager(
            local_model=_model_balanced,
            cloud_provider=cloud_provider,
            preset="balanceado"
        )
        
        # NIVEL 3: Cloud para complejos (opcional, auto-detectado)
        # Ya integrado en self.llm con cloud_provider="auto"

        # Clasificador Haiku: LLMManager dedicado con ANTHROPIC_MODELS["fast"]
        # Usado para needs_web_search() en lugar de llama3.2:1b → más preciso y rápido
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if anthropic_key.startswith("sk-ant-"):
            try:
                self.llm_classifier = LLMManager(
                    local_model=_model_fast,   # fallback si cloud falla
                    cloud_provider="anthropic",
                    preset="rapido"
                )
                # Sobrescribir anthropic_model para usar Haiku específicamente
                if self.llm_classifier.cloud_model:
                    from core.llm_helpers import build_haiku_classifier
                    # max_tokens=10 (solo SI/NO) alcanza porque la factoría apaga el thinking.
                    self.llm_classifier.cloud_model = build_haiku_classifier(anthropic_key, max_tokens=10)
                    self.llm_classifier.anthropic_model = ANTHROPIC_MODELS["fast"]
                    self.llm_classifier.cloud_provider = "anthropic"
                    print(f"[Info] Web classifier: {ANTHROPIC_MODELS['fast']} (Anthropic)")
            except Exception as _haiku_err:
                self.llm_classifier = None
                print(f"[Info] Web classifier fallback a llm_fast: {_haiku_err}")
        
        # Cache de respuestas instantáneas (sin LLM) — gestionado en fast_responses.py
        self.fast_cache = _fast_resp.FAST_CACHE
        
        self.context = ContextManager()
        self.self_state = SelfState()  # ← NUEVO: Sistema de pseudo-consciencia
        from core.consciousness import get_consciousness
        self.consciousness = get_consciousness()
        self.schedule_service = get_schedule_service()
        self.tts = TTS()
        self.stt: Optional[STT] = None
        self._stt_initialized = False
        # Verificar disponibilidad sin cargar el modelo (~0ms)
        try:
            import importlib.util
            self.stt_available = importlib.util.find_spec("whisper") is not None
        except Exception:
            self.stt_available = False
        self.router = IntentRouter(self.llm, self.context)  # Usar 3b para mejor clasificaci\u00f3n
        
        # Configuracion del navegador: centralizada en BrowserConfig
        from core.browser_config import BrowserConfig
        
        browser_config = BrowserConfig.for_visible()
        
        self.console.print(f"[dim]Edge: {browser_config.edge_executable or 'Playwright bundled'}[/dim]")
        self.console.print(f"[dim]Perfil: {browser_config.user_profile_path or 'sin perfil de usuario'}[/dim]")
        
        self.automation = AppController(config=browser_config)
        self.vision_capture = ScreenCapturer()
        self.vision_analyze = ImageAnalyzer(
            api_key=os.getenv("GOOGLE_API_KEY"),
            openai_key=os.getenv("OPENAI_API_KEY"),
            anthropic_key=os.getenv("ANTHROPIC_API_KEY")
        )
        
        # Sistema de Historial e Importancia (solo para admin)
        self.history = None
        self.importance_classifier = None
        if self.user_role == "admin" or self.user_role is None:
            try:
                self.history = ConversationHistory()
                self.importance_classifier = ImportanceClassifier(llm=self.llm_fast)
                # Compartir el classifier (con LLM) con ContextManager
                self.context.set_importance_classifier(self.importance_classifier)
            except Exception as e:
                self.console.print(f"[yellow]Historial deshabilitado: {e}[/yellow]")

        # GitNexus Code Intelligence (lazy init — requires Node.js + gitnexus)
        self.gitnexus: Optional['GitNexusBridge'] = None
        self._gitnexus_init_attempted = False

        # ── Cache LLM ─────────────────────────────────────────────────
        self.llm_cache = LLMCache(max_size=500)

        # ── Módulos opcionales (declarados None; se inicializan en background) ──
        # ── Skills System ─────────────────────────────────────────────
        self.skill_registry = SkillRegistry()

        # Legacy refs (migrados a skills — estos attrs se mantienen para
        # compatibilidad con código que accede directamente, ej: knowledge_graph.process_message)
        self.knowledge_graph = None
        self.reminder_system = None
        self.file_ops = None
        self.git_ops = None

        self.user_profile:      Optional['UserProfile']            = None
        self.summarizer:        Optional['ConversationSummarizer'] = None
        self.workspace:         Optional['SessionWorkspace']       = None
        self.feedback_system:   Optional['FeedbackSystem']         = None
        self.few_shot_selector: Optional['DynamicFewShotSelector'] = None
        self.reflection_engine: Optional['ReflectionEngine']       = None
        self.style_profiler:    Optional['StyleProfiler']          = None
        self.training_curator:  Optional['TrainingDataCurator']    = None
        self.finetune_manager:  Optional['FineTuneManager']        = None
        self._last_interaction_id: Optional[int] = None
        self._last_user_input_for_feedback: Optional[str] = None
        self._call_turn_buffer: Optional[str] = None  # cross-turn context for call mode
        self._study_start: Optional[float] = None     # study session start timestamp
        self._study_notified: bool = False            # break_reminder already sent this session
        self._pending_diagnostic: bool = False
        self._pending_diagnostic_summary: str = ""
        self._pending_diagnostic_task: Optional[asyncio.Task] = None
        # Inicializar módulos opcionales en background para no bloquear el startup
        threading.Thread(target=self._init_optional_modules, daemon=True).start()

        # Mostrar estado del sistema con los 3 niveles
        self._print_model_status()
        self.console.print("[bold magenta]🧠 Self State: Pseudo-consciencia activa[/bold magenta]")

    def reset_conversation(self):
        """Reset explícito de 'nueva conversación': limpia TODO el contexto de corto
        plazo (historial en RAM + cross-turn buffer + clarificaciones pendientes) sin
        tocar la memoria vectorial de largo plazo (segundo cerebro).
        """
        self.context.clear_short_term()
        self._call_turn_buffer = None
        self._pending_clarification = None

    def _init_optional_modules(self):
        """Inicializa módulos opcionales en background (no bloquea el startup)."""

        # ── Skills System (reemplaza HAS_KNOWLEDGE_GRAPH, HAS_REMINDERS, etc.) ──
        from pathlib import Path
        skill_dirs = [
            Path(__file__).parent.parent / "skills" / "builtin",
            Path(__file__).parent.parent / "skills" / "community",
        ]
        loader = SkillLoader(skill_dirs)
        count = loader.load_into(self.skill_registry)
        self.console.print(f"[green]✓[/green] Skills: {count} cargadas")

        # Inyectar registry ref al skill-manager (meta-skill)
        sm_skill = self.skill_registry.get_skill("skill-manager")
        if sm_skill and hasattr(sm_skill, 'set_registry'):
            sm_skill.set_registry(self.skill_registry)

        # Configurar callback de reminders si la skill está activa
        reminders_skill = self.skill_registry.get_skill("reminders")
        if reminders_skill and hasattr(reminders_skill, 'set_reminder_callback'):
            reminders_skill.set_reminder_callback(self._on_reminder_due)

        # Inyectar llm_manager en todas las skills que lo necesiten
        for skill in self.skill_registry.get_all():
            if hasattr(skill, 'set_llm_manager'):
                skill.set_llm_manager(self.llm)

        # Mantener refs legacy para código que accede directamente
        kg_skill = self.skill_registry.get_skill("knowledge-graph")
        if kg_skill and hasattr(kg_skill, '_get_kg'):
            try:
                self.knowledge_graph = kg_skill._get_kg()
            except Exception:
                pass

        # Hot-reload (opcional, requiere watchdog)
        try:
            loader.watch(self.skill_registry)
        except Exception:
            pass

        # Actualizar self_state con skills activas (capacidades dinámicas)
        self.self_state.update_from_skills(self.skill_registry)

        if HAS_USER_PROFILE:
            try:
                self.user_profile = UserProfile()
                self.console.print("[green]✓[/green] User Profile activo")
            except Exception as e:
                self.console.print(f"[yellow]UserProfile deshabilitado: {e}[/yellow]")

        if HAS_SUMMARIZER:
            try:
                self.summarizer = ConversationSummarizer(summarize_every=20)
                self.console.print("[green]✓[/green] Conversation Summarizer activo")
            except Exception as e:
                self.console.print(f"[yellow]Summarizer deshabilitado: {e}[/yellow]")

        if HAS_WORKSPACE:
            try:
                self.workspace = SessionWorkspace()
                self.console.print("[green]✓[/green] Session Workspace activo")
            except Exception as e:
                self.console.print(f"[yellow]Workspace deshabilitado: {e}[/yellow]")

        if HAS_API_REGISTRY:
            self.console.print("[green]✓[/green] API Registry (crypto, web, wiki, rss) activo")

        if HAS_ADAPTIVE_LEARNING:
            try:
                self.feedback_system   = FeedbackSystem()
                self.few_shot_selector = DynamicFewShotSelector()
                self.reflection_engine = ReflectionEngine(reflect_every=30)
                self.training_curator  = TrainingDataCurator()
                self.finetune_manager  = FineTuneManager()
                self.finetune_manager.resume_monitoring()
                if self.user_profile:
                    self.style_profiler = StyleProfiler(self.user_profile)
                self.console.print("[green]✓[/green] Aprendizaje adaptativo activo")
            except Exception as e:
                self.console.print(f"[yellow]Aprendizaje adaptativo deshabilitado: {e}[/yellow]")

    def _print_model_status(self):
        """Muestra el estado de los 3 niveles de modelos"""
        self.console.print("\n[bold cyan]🚀 Sistema Híbrido de 3 Niveles:[/bold cyan]")
        if self.llm_fast.ollama_available:
            self.console.print("  [green]✓[/green] Nivel 1 (Ultra-rápido): llama3.2:1b [dim](1-2s)[/dim]")
        else:
            self.console.print("  [yellow]○[/yellow] Nivel 1 (Ultra-rápido): Ollama no disponible [dim](instalar cuando haya GPU)[/dim]")
        if self.llm.ollama_available:
            self.console.print("  [green]✓[/green] Nivel 2 (Balanceado): llama3.2:3b [dim](8-12s)[/dim]")
        else:
            self.console.print("  [yellow]○[/yellow] Nivel 2 (Balanceado): Ollama no disponible [dim](futuro: qwen2.5-coder, gemma3, llama3.2:8b)[/dim]")
        
        status = self.llm.get_status()
        if status.get('cloud_available'):
            cloud_name = status.get('cloud_provider', status.get('provider', 'cloud'))
            self.console.print(f"  [green]✓[/green] Nivel 3 (Complejo): Cloud {cloud_name} [dim](auto)[/dim]")
        else:
            self.console.print("  [yellow]○[/yellow] Nivel 3 (Complejo): Cloud [dim](no configurado)[/dim]")

    def _effective_user_role(self, ctx: Optional[RequestContext]) -> Optional[str]:
        if ctx and ctx.user_role:
            return ctx.user_role
        return self.user_role

    # ══════════════════════════════════════════════════════════════════
    # Clarificación de baja confianza + orquestación multi-tarea
    # (ver core/confidence.py, core/clarification_gate.py, core/task_decomposer.py
    #  y el plan velvet-bee). El score de confianza se deriva de analysis["_source"]
    #  + el ranking del skill registry; el gate decide resolver/preguntar/charla.
    # ══════════════════════════════════════════════════════════════════

    def _skill_action_checker(self):
        """Checker para el decomposer: un segmento es 'comando' si tiene verbo
        de acción (decomposer interno) o matchea alguna skill del registry."""
        def _check(segment: str) -> bool:
            if _decomposer._has_action_verb(segment):
                return True
            try:
                return bool(self.skill_registry.rank_candidates(segment))
            except Exception:
                return False
        return _check

    async def _route_one(self, task: str, ctx: RequestContext) -> dict:
        """Rutea UNA sub-tarea: rankea skills + corre el router, construye los
        candidatos unificados y decide vía el gate. No ejecuta nada."""
        try:
            skill_cands = self.skill_registry.rank_candidates(task)
        except Exception:
            skill_cands = []

        try:
            # Espejar handle_input: en cloud mode se salta CAPA 2 (LLM). Sin esto
            # cada sub-tarea pagaba una llamada Haiku aunque el mensaje entero
            # después se re-rutee por el flujo single.
            _skip_llm = getattr(ctx, "force_mode", None) == "cloud"
            analysis = await self.router.classify_and_parse(task, skip_llm=_skip_llm)
        except Exception:
            analysis = {"intent": "chat", "_source": "fallback_information"}

        candidates: list = []
        for sc in skill_cands:
            candidates.append(_gate.make_skill_candidate(sc.skill, sc.score))
        # El router solo aporta candidato si interpretó una acción ejecutable.
        if analysis.get("intent") in ("automation", "search", "web_search"):
            candidates.append(_gate.make_router_candidate(analysis, confidence_of(analysis)))

        decision = _gate.decide(task, candidates)
        return {"task": task, "decision": decision, "analysis": analysis,
                "candidates": candidates}

    async def _execute_candidate(self, task: str, cand, ctx: RequestContext) -> str:
        """Ejecuta el candidato elegido (skill o automation)."""
        if cand is None:
            return f"No pude interpretar «{task}»."
        if cand.kind == "skill":
            res = await cand.payload.handle(task, ctx)
            return res if res is not None else f"No pude completar «{task}»."
        # router / automation — mismo guardrail que el flujo single
        verdict = _classify_action_risk(cand.payload)
        if verdict.needs_confirmation:
            _sid = getattr(ctx, "session_id", None) or self.session_id
            return self._request_automation_confirmation(_sid, cand.payload, verdict.reason)
        return await self.automation.execute(cand.payload)

    # ── Confirmación de automatizaciones (por sesión, con TTL) ───────────

    _PENDING_AUTOMATION_TTL_SECS = 120

    def _set_pending_automation(self, session_id: str, analysis: dict) -> None:
        self._pending_automations[session_id] = {"analysis": analysis, "ts": time.time()}

    def _has_pending_automation(self, session_id: str) -> bool:
        entry = self._pending_automations.get(session_id)
        return entry is not None and time.time() - entry["ts"] <= self._PENDING_AUTOMATION_TTL_SECS

    def _pop_pending_automation(self, session_id: str) -> Optional[dict]:
        """Saca la automatización pendiente de ESTA sesión (None si venció)."""
        entry = self._pending_automations.pop(session_id, None)
        if entry is None or time.time() - entry["ts"] > self._PENDING_AUTOMATION_TTL_SECS:
            return None
        return entry["analysis"]

    def _request_automation_confirmation(self, session_id: str, analysis: dict, reason: str = "") -> str:
        """Encola la acción para esta sesión y devuelve la pregunta sí/no."""
        self._set_pending_automation(session_id, analysis)
        target_desc = analysis.get("interact_target") or analysis.get("target") or "desconocido"
        action_desc = (
            analysis.get("interact_action") or analysis.get("action")
            or analysis.get("automation_intent") or "ejecutar"
        )
        desc_parts = [f"**{action_desc}** → {target_desc}"]
        if analysis.get("search_query"):
            desc_parts.append(f"(buscar: {analysis['search_query']})")
        if reason:
            desc_parts.append(f"— {reason}")
        elif "_confidence" in analysis:
            desc_parts.append(f"[confianza: {analysis['_confidence']:.0%}]")
        desc_str = " ".join(desc_parts)
        self.console.print(f"[bold yellow]⚠[/bold yellow] Confirmación requerida: {desc_str}")
        return f"⚠️ Antes de ejecutar necesito tu confirmación: {desc_str}\n¿Lo ejecuto? (sí/no)"

    @staticmethod
    def _match_clarification_reply(reply: str, options: list):
        """Mapea la respuesta del usuario a una de las opciones ofrecidas:
        por posición ('1', 'el primero', 'la segunda') o por keyword de la
        frase/label de la opción. Devuelve el Candidate o None."""
        low = reply.lower().strip()
        # Afirmación a una pregunta de candidato ÚNICO ("¿querés que reproduzca
        # eso?" → "sí" / "dale" / "si porfavor"). Solo aplica con una opción:
        # con varias, "sí" es ambiguo y se cae al mapeo por posición/keyword.
        if len(options) == 1 and _CLARIFY_AFFIRM.match(low):
            return options[0]
        # Por posición
        _ordinals = {
            "1": 0, "primero": 0, "primera": 0, "el primero": 0, "la primera": 0,
            "2": 1, "segundo": 1, "segunda": 1, "el segundo": 1, "la segunda": 1,
            "3": 2, "tercero": 2, "tercera": 2,
        }
        for key, idx in _ordinals.items():
            if low == key or low.startswith(key + " ") or low.endswith(" " + key):
                if idx < len(options):
                    return options[idx]
        # Por keyword: comparamos los tokens de la respuesta contra las keywords
        # de cada opción, aceptando raíz común de >=4 chars ("música"~"musica",
        # "agendalo"~"agende"). Así "música" mapea a PLAY_MEDIA aunque la frase
        # diga "reproduzca".
        reply_tokens = [t for t in re.findall(r"\w+", low) if len(t) >= 4]
        for opt in options:
            opt_kws = [k.lower() for k in (getattr(opt, "keywords", None) or [])]
            opt_kws += re.findall(r"\w+", f"{opt.phrase} {opt.label}".lower())
            for rt in reply_tokens:
                for kw in opt_kws:
                    if len(kw) >= 4 and (rt[:4] == kw[:4]):
                        return opt
        return None

    async def _resolve_pending_clarification(
        self, user_input: str, ctx: RequestContext
    ) -> Optional[str]:
        """Si hay una clarificación pendiente, intenta resolverla con la
        respuesta del usuario y ejecutar el plan. Devuelve la respuesta final,
        o None si no había pending / expiró / no mapeó (se procesa normal)."""
        pending = self._pending_clarification
        if not pending:
            return None
        # TTL de 90s para no re-ejecutar planes viejos.
        if time.time() - pending.get("ts", 0.0) > 90:
            self._pending_clarification = None
            return None

        self._pending_clarification = None
        plan = pending.get("plan", [])
        _is_negate = bool(_CLARIFY_NEGATE.match(user_input.lower().strip()))
        resolved_any = False
        for step in plan:
            if step.get("chosen") is None and step.get("options"):
                if _is_negate:
                    # "no" / "cancelá" → omitir esa parte, ejecutar el resto.
                    step["skipped"] = True
                    resolved_any = True
                    continue
                match = self._match_clarification_reply(user_input, step["options"])
                if match is not None:
                    step["chosen"] = match
                    resolved_any = True

        # Si no mapeó ninguna ambigüedad, descartamos y procesamos como mensaje nuevo.
        if not resolved_any and any(
            s.get("chosen") is None and not s.get("skipped") for s in plan
        ):
            return None

        results: list[str] = []
        steps: list[StepRecord] = []
        total = len(plan)
        for i, step in enumerate(plan, 1):
            if step.get("skipped"):
                res = f"Omití «{step['task']}»."
                results.append(f"[{i}/{total}] {res}" if total > 1 else res)
                continue
            chosen = step.get("chosen")
            _t0 = time.perf_counter()
            res = await self._execute_candidate(step["task"], chosen, ctx)
            steps.append(self._step_from_candidate(
                step["task"], chosen, res, int((time.perf_counter() - _t0) * 1000)))
            results.append(f"[{i}/{total}] {res}" if total > 1 else res)

        final = "\n".join(results)
        self.context.add("user", user_input)
        self.context.add("assistant", final)
        self._finalize_envelope(
            pending.get("original_input", user_input), ctx,
            intent="multi_task" if total > 1 else "clarified",
            steps=steps, response=final, success=True)
        return final

    # ── Fresh-info enhancement (v4): respuesta rápida + oferta de detalle ─────

    async def _maybe_launch_enhancement(self, query: str, fast_answer: str) -> str:
        """Para queries volátiles (noticias/actualidad), verifica en paralelo la
        respuesta contra la fuente más autoritativa. Si la verificación llega a
        tiempo y aporta algo nuevo, anexa la oferta a la respuesta; si tarda,
        queda pendiente y _resolve_pending_extra_search la consume en el
        follow-up. Para queries estáticas devuelve fast_answer intacto."""
        from modules.search.web_search import _is_news_query
        if not _is_news_query(query):
            return fast_answer

        llm_fn = lambda p: self.llm.generate(
            p, force_cloud=True, force_local=False, preset='calidad')
        task = asyncio.create_task(self._run_enhancement(query, fast_answer, llm_fn))
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), timeout=_ENHANCEMENT_RACE_TIMEOUT)
        except asyncio.TimeoutError:
            # Sigue corriendo en background; el follow-up del usuario la consume.
            self._pending_extra_search_task = task
            self._pending_extra_search_ts = time.time()
            return fast_answer
        except Exception as e:
            self.console.print(f"[yellow]Enhancement v4 falló: {e}[/yellow]")
            return fast_answer

        if result.get("novel") and result.get("answer"):
            whats_new = (result.get("whats_new") or "").strip() or "información más actual"
            # Guardar el resultado ya resuelto para que el "sí" lo muestre al toque.
            self._pending_extra_search_task = asyncio.create_task(
                asyncio.sleep(0, result=result))
            self._pending_extra_search_ts = time.time()
            return (
                f"{fast_answer}\n\n"
                f"Encontré una fuente más autoritativa con información más reciente "
                f"({whats_new}). ¿Querés que te la muestre?"
            )
        return fast_answer

    async def _run_enhancement(self, query: str, fast_answer: str, llm_fn) -> dict:
        """Busca de nuevo con is_news=True, elige la fuente más autoritativa,
        extrae su contenido y lo compara contra la respuesta ya dada.
        Devuelve {"novel": bool, "answer": str, "whats_new": str}."""
        _empty = {"novel": False, "answer": "", "whats_new": ""}
        try:
            from modules.search.web_search import (
                WebSearch, pick_best_source, synthesize_enhancement,
            )
            ws = WebSearch()
            results = await ws.search(query, is_news=True)
            best = pick_best_source(results)
            if best is None:
                return _empty
            content = await ws.fetch_article(best.url)
            if not content or not content.strip():
                browse = await ws.deep_browse(best.url, query)
                content = (browse or {}).get("content", "") if browse.get("success") else ""
            if not content or not content.strip():
                return _empty
            source_label = best.title or best.url
            return await synthesize_enhancement(
                query, fast_answer, content, source_label, llm_fn)
        except Exception as e:
            self.console.print(f"[yellow]Enhancement v4 (background) falló: {e}[/yellow]")
            return _empty

    def _clear_pending_extra_search(self) -> None:
        """Descarta la verificación pendiente (cancela si sigue corriendo)."""
        task = self._pending_extra_search_task
        if task is not None and not task.done():
            task.cancel()
        self._pending_extra_search_task = None

    async def _resolve_pending_extra_search(self, message: str) -> Optional[str]:
        """Resuelve el follow-up a la oferta "¿Querés que te la muestre?".
        Devuelve la respuesta final, o None si el mensaje debe procesarse
        normal. La oferta es one-shot: cualquier follow-up la consume."""
        task = self._pending_extra_search_task
        if task is None:
            return None
        # TTL: oferta vieja → descartar y procesar el mensaje normal.
        if time.time() - self._pending_extra_search_ts > _EXTRA_SEARCH_TTL:
            self._clear_pending_extra_search()
            return None

        msg = (message or "").strip().lower()
        if _CLARIFY_NEGATE.match(msg):
            self._clear_pending_extra_search()
            return "Listo, lo dejamos ahí."
        if _CLARIFY_AFFIRM.match(msg) or _EXTRA_SEARCH_AFFIRM.match(msg):
            try:
                result = await task
            except Exception as e:
                self.console.print(f"[yellow]Enhancement v4 pendiente falló: {e}[/yellow]")
                result = {}
            self._pending_extra_search_task = None
            if result.get("novel") and result.get("answer"):
                return result["answer"]
            return ("Revisé la fuente, pero no encontré nada más reciente "
                    "que lo que ya te di.")
        # Mensaje no relacionado → consumir la oferta y procesar normal.
        self._clear_pending_extra_search()
        return None

    async def _run_multitask(self, tasks: list[str], ctx: RequestContext) -> Optional[str]:
        """Orquesta N sub-tareas: analiza todas, resuelve ambigüedad AL INICIO
        (pregunta agrupada y guarda el plan), y ejecuta en orden reportando y
        siguiendo ante fallo.

        Devuelve ``None`` si ALGÚN segmento no resuelve a un comando ejecutable
        (skill/automation). Es la regla all-or-nothing del decomposer llevada al
        nivel semántico: el split sintáctico ("busca info sobre X **y** analiza el
        benchmark") puede dar segmentos con verbo que NO son comandos (web search,
        chat). Este pipeline no sabe ejecutarlos, así que el caller debe tratar el
        mensaje entero por el flujo single, donde CAPA 1.15 / chat sí lo manejan.
        """
        routes = []
        for t in tasks:
            routes.append(await self._route_one(t, ctx))

        # ── Fase 1: detectar ambigüedad. Si algo requiere clarificación,
        #    preguntamos TODO junto y NO ejecutamos nada todavía.
        plan: list[dict] = []
        questions: list[str] = []
        for r in routes:
            dec = r["decision"]
            if dec.action == "clarify":
                plan.append({"task": r["task"], "chosen": None, "options": dec.options})
                questions.append(f"• «{r['task']}»: {dec.question}")
            elif dec.action == "resolve":
                plan.append({"task": r["task"], "chosen": dec.chosen, "options": []})
            else:  # chat → sin candidato ejecutable → no es multitarea
                self.console.print(
                    f"[dim][multitask] «{r['task'][:60]}» no es comando → "
                    f"flujo single para el mensaje completo[/dim]"
                )
                return None

        if questions:
            self._pending_clarification = {
                "plan": plan, "ts": time.time(),
                "original_input": " ".join(tasks),
            }
            head = "Antes de seguir, necesito que me aclares:"
            return head + "\n" + "\n".join(questions)

        # ── Fase 2: todo resuelto → ejecutar en orden, reportar-y-seguir.
        results: list[str] = []
        steps: list[StepRecord] = []
        total = len(plan)
        for i, step in enumerate(plan, 1):
            chosen = step.get("chosen")
            if chosen is None:
                res = f"No pude interpretar «{step['task']}» como un comando."
                steps.append(StepRecord(skill_name="unresolved",
                                        args={"task": step["task"]},
                                        result_summary=res[:200], success=False))
            else:
                _t0 = time.perf_counter()
                _ok = True
                try:
                    res = await self._execute_candidate(step["task"], chosen, ctx)
                except Exception as exc:
                    res = f"Error en «{step['task']}»: {exc}"
                    _ok = False
                steps.append(self._step_from_candidate(
                    step["task"], chosen, res,
                    int((time.perf_counter() - _t0) * 1000), _ok))
            results.append(f"[{i}/{total}] {res}")

        final = "\n".join(results)
        # Loop Hermes: un envelope multi-step alimenta al skill_proposer.
        self._finalize_envelope(" ".join(tasks), ctx, intent="multi_task",
                                steps=steps, response=final,
                                success=all(s.success for s in steps))
        return final

    # ── Loop Hermes: grabado de TaskEnvelope con steps reales ───────────

    @staticmethod
    def _step_from_candidate(task, cand, result, duration_ms, success=True):
        """Construye un StepRecord a partir de un candidato ejecutado."""
        provider = model = None
        if cand is not None and cand.kind == "router" and isinstance(cand.payload, dict):
            provider = cand.payload.get("provider")
        return StepRecord(
            skill_name=(cand.label or cand.kind) if cand is not None else "unresolved",
            args={"task": task},
            result_summary=(result or "")[:200],
            success=success,
            duration_ms=duration_ms,
            provider=provider,
            model=model,
        )

    def _finalize_envelope(self, user_input, ctx, *, intent, steps, response,
                           success=True, complexity_score=None):
        """Graba un TaskEnvelope en el store (si está inyectado). Reemplaza el
        antiguo wrapper monkey-patch de web_app: ahora el orchestrator tiene el
        intent real, los steps y el success. Nunca rompe el request si falla."""
        store = getattr(self, "_envelope_store", None)
        if store is None:
            return
        try:
            if complexity_score is None:
                try:
                    if getattr(self, "_cc_for_envelope", None) is None:
                        from core.complexity_classifier import ComplexityClassifier
                        self._cc_for_envelope = ComplexityClassifier()
                    complexity_score = float(
                        self._cc_for_envelope.classify(user_input).get("probability", 0.0)
                    )
                except Exception:
                    complexity_score = 0.0
            env = TaskEnvelope(
                request_id=getattr(ctx, "request_id", None) or secrets.token_hex(8),
                user_input=user_input,
                intent=intent or "unknown",
                steps=list(steps or []),
                final_response=(response or "")[:2000],
                success=success,
                complexity_score=complexity_score,
                channel_type=getattr(ctx, "channel_type", None) or "websocket",
                user_name=getattr(ctx, "user_name", None),
            )
            store.save(env)
        except Exception as exc:
            self.console.print(f"[dim]envelope finalize failed: {exc}[/dim]")

    async def _cloud_llm_escalate(self, user_input: str):
        """Nivel 2 del escalado en cloud mode. Cuando la heurística NO resolvió,
        reactiva el extractor LLM (DeepSeek Flash → Haiku, vía extract_browser_intent)
        que entiende formas libres ("che tirame discord", "poneme algo de lofi").
        En cloud mode el router corre con skip_llm=True y este extractor queda
        apagado; acá lo reactivamos SOLO cuando el fast-path falló, así no agrega
        latencia/costo al caso común. Devuelve el analysis de automation o None.
        (Fase 2 sumará el classifier registry-aware para skills.)"""
        # ── Rama A — browser/automation (Fase 1) ────────────────────────────
        # Guard de costo: el extractor de browser solo vale la pena si el mensaje
        # MENCIONA un target accionable conocido (app/sitio). "che tirame el
        # discord" escala (menciona discord); "hola cómo va" no gasta esta rama.
        _low = user_input.lower()
        _known = list(NATIVE_APPS) + list(SITE_KEYWORDS)
        if any(re.search(rf"\b{re.escape(t)}\b", _low) for t in _known):
            try:
                analysis = await extract_browser_intent(user_input, self.llm)
            except Exception as exc:
                self.console.print(f"[dim]cloud LLM escalate (browser) falló: {exc}[/dim]")
                analysis = None
            if analysis and analysis.get("intent") == "automation":
                # El router normalmente enriquece con complexity; lo replicamos
                # para que _select_llm_level y el pipeline tengan los campos.
                analysis.setdefault("complexity", "complex")
                analysis.setdefault("complexity_score", 0.5)
                return analysis

        # ── Rama B — classifier registry-aware (Fase 2, always-on) ──────────
        # Sobre el residual (la rama A no resolvió). Lee el catálogo DINÁMICO de
        # skills → auto-descubre features nuevas sin tocar este código. Usa
        # DeepSeek Flash (barato). La charla casual devuelve route=chat → None.
        try:
            catalog = self.skill_registry.get_prompt_summaries()
            classified = await classify_intent(user_input, self.llm, catalog)
        except Exception as exc:
            self.console.print(f"[dim]cloud classifier falló: {exc}[/dim]")
            return None
        if classified is None:
            return None
        if classified.get("intent") == "skill":
            name = classified.get("skill_name")
            skill = self.skill_registry.get_skill(name) if name else None
            if skill is not None and skill.enabled:
                classified.setdefault("complexity", "simple")
                return classified
            return None
        if classified.get("intent") == "automation":
            # Raro: la rama A cubre automation, pero si el mensaje no nombraba un
            # target conocido y el classifier igual detectó automation, respetar.
            classified.setdefault("complexity", "complex")
            classified.setdefault("complexity_score", 0.5)
            return classified
        return None

    async def handle_input(
        self,
        user_input: str,
        ctx: Optional[RequestContext] = None,
        speak: bool = False,
        force_mode: Optional[str] = None,
        reasoning_mode: bool = False
    ):
        """Procesa entrada con sistema híbrido de 3 niveles"""
        # For backward compat, build ctx from instance attrs if not provided
        if ctx is None:
            ctx = RequestContext(
                user_role=self.user_role,
                user_name=self.user_name,
                authenticated=self.authenticated,
                session_id=self.session_id,
                force_mode=force_mode,
                reasoning_mode=reasoning_mode,
                speak=speak,
            )

        # Reset short-term context if session changed (prevents cross-session hallucination)
        _sid = getattr(ctx, "session_id", None) or self.session_id
        if self.context.reset_for_new_session(_sid):
            # Session changed: also drop the cross-turn buffer (lives on the orchestrator,
            # not the ContextManager) so a stale prior-session utterance can't concatenate.
            self._call_turn_buffer = None

        total_start = time.perf_counter()
        timings = {}

        force_local = force_mode == "local"
        force_cloud = force_mode == "cloud"
        
        # PASO -1: Validar input no vacío (fast-exit antes del pipeline)
        if not user_input or not user_input.strip():
            return "¿En qué puedo ayudarte?"

        # ── CLARIFICACIÓN PENDIENTE: respuesta a una pregunta del gate ───
        # Si Zephyro preguntó "¿querés que X o que Y?" en el turno anterior,
        # la respuesta actual mapea a uno de los candidatos guardados y dispara
        # la ejecución del plan. Va antes de la confirmación de automation.
        if self._pending_clarification is not None:
            _clar_result = await self._resolve_pending_clarification(user_input, ctx)
            if _clar_result is not None:
                return _clar_result

        # ── INFO ADICIONAL PENDIENTE: respuesta a "¿querés ver el detalle más actual?" ──
        # Va ANTES de _IMPLICIT_YES: ese regex también matchea "sí/dale" y
        # re-ejecutaría la última automation en vez de mostrar el detalle.
        if self._pending_extra_search_task is not None:
            _extra_result = await self._resolve_pending_extra_search(user_input)
            if _extra_result is not None:
                self.context.add("user", user_input)
                self.context.add("assistant", _extra_result)
                return _extra_result

        # ── CONFIRMACIÓN IMPLÍCITA: "sí haz eso / hazlo" sin pending ─────
        # El LLM a veces pregunta "¿quieres que reproduzca X?" sin guardar
        # _pending_automation. Si el usuario responde afirmativamente y hay
        # un last_automation en contexto, re-ejecutarlo directamente.
        _IMPLICIT_YES = re.compile(
            r'^(?:s[ií]|yes|dale|hazlo|si\s+haz(?:lo)?|haz(?:lo)?\s+eso'
            r'|ejecuta(?:lo)?|procede|adelante|vamos|confirmo|eso\s+mismo'
            r'|exactamente\s+eso|claro\s+que\s+s[ií]|por\s+favor\s+haz(?:lo)?'
            r'|s[ií]\s+haz\s+eso|ok\s+haz(?:lo)?)\s*(?:por\s+favor|porfavor|please)?\s*$',
            re.IGNORECASE,
        )
        _IMPLICIT_YES_TTL_SECS = 30
        if not self._has_pending_automation(_sid) and _IMPLICIT_YES.match(user_input.strip()):
            _last_auto_snapshot = self.context.get_last_automation() if self.context else None
            # TTL: descartar snapshots viejos para evitar re-ejecutar acciones desconectadas
            if isinstance(_last_auto_snapshot, dict):
                _snapshot_age = time.time() - _last_auto_snapshot.get("ts", 0.0)
                if _snapshot_age > _IMPLICIT_YES_TTL_SECS:
                    _last_auto_snapshot = None
            _pending_from_ctx = (
                _last_auto_snapshot.get("analysis")
                if isinstance(_last_auto_snapshot, dict) else None
            )
            if isinstance(_pending_from_ctx, dict) and _pending_from_ctx.get("automation_intent"):
                # Un "sí" suelto nunca re-ejecuta una acción de riesgo: se pregunta.
                _risk = _classify_action_risk(_pending_from_ctx)
                if _risk.needs_confirmation:
                    response = self._request_automation_confirmation(_sid, _pending_from_ctx, _risk.reason)
                    self.context.add("user", user_input)
                    self.context.add("assistant", response)
                    return response
                _target_dbg = _pending_from_ctx.get("target") or _pending_from_ctx.get("action") or "?"
                self.console.print(f"[bold green]Confirmacion implicita — re-ejecutando last_automation: {_target_dbg}[/bold green]")
                auto_start = time.perf_counter()
                _re_result = await self.automation.execute(_pending_from_ctx)
                timings['automation'] = time.perf_counter() - auto_start
                self.console.print(f"[bold green]✓[/bold green] {_re_result}")
                self.context.add("user", user_input)
                self.context.add("assistant", _re_result)
                total_time = time.perf_counter() - total_start
                self.self_state.record_action(
                    intent="automation", user_input=user_input,
                    response=_re_result, elapsed_time=total_time,
                    details={"implicit_confirm": True}
                )
                return _re_result

        # ── CONFIRMACIÓN PENDIENTE DE AUTOMATIZACIÓN (CAPA 2) ─────────
        # Si hay una automatización pendiente de confirmación, verificar
        # si el usuario respondió sí/no antes de procesar como mensaje nuevo.
        # Se saca SIEMPRE: si la respuesta no es sí/no, la acción se descarta.
        pending = self._pop_pending_automation(_sid)
        if pending is not None:
            user_lower = user_input.lower().strip()
            _confirm_yes = ["sí", "si", "yes", "dale", "hazlo", "ok",
                            "adelante", "confirmo", "claro", "procede",
                            "afirmativo", "correcto", "eso", "exacto"]
            _confirm_no = ["no", "nope", "cancelar", "cancela", "olvídalo",
                           "olvidalo", "nada", "dejalo", "déjalo", "no es eso",
                           "negativo", "nel", "para nada"]
            if any(user_lower == w or user_lower.startswith(w + " ") for w in _confirm_yes):
                # Usuario confirmó → ejecutar la automatización pendiente
                self.console.print("[bold green]✓[/bold green] Confirmado. Ejecutando...")
                auto_start = time.perf_counter()
                result = await self.automation.execute(pending)
                timings['automation'] = time.perf_counter() - auto_start
                self.console.print(f"[bold green]✓[/bold green] {result}")
                self.context.set_last_automation(pending)
                self.context.add("user", user_input)
                self.context.add("assistant", result)
                total_time = time.perf_counter() - total_start
                self.self_state.record_action(
                    intent="automation", user_input=user_input,
                    response=result, elapsed_time=total_time,
                    details={"action": pending.get("action"), "target": pending.get("target"), "confirmed": True}
                )
                return result
            elif any(user_lower == w or user_lower.startswith(w + " ") for w in _confirm_no):
                # Usuario rechazó → descartar y responder
                response = "Entendido, no ejecutaré esa acción. ¿En qué más puedo ayudarte?"
                self.console.print(f"[bold yellow]✗[/bold yellow] Automatización cancelada.")
                self.context.add("user", user_input)
                self.context.add("assistant", response)
                return response
            else:
                # El usuario envió otro mensaje distinto → descartar pending y procesar normalmente
                self.console.print("[dim]⚠ Automatización pendiente descartada (nuevo mensaje).[/dim]")

        # PASO -1: Comando especial de identificación con contraseña
        user_input_lower = user_input.lower().strip()
        if "identificame" in user_input_lower or "identifícame" in user_input_lower:
            # Si ya está autenticado
            if self.authenticated:
                # Admin ya identificado
                if self.user_role == "admin":
                    response = f"Bienvenido, {self.user_name}."
                    self.console.print(f"[bold cyan]ℹ️ {response}[/bold cyan]")
                # Invitado ya identificado
                elif self.user_role == "guest":
                    response = f"Bienvenido, {self.user_name}."
                    self.console.print(f"[bold cyan]ℹ️ {response}[/bold cyan]")
                
                if speak:
                    await self.tts.speak(response)
                
                total_time = time.perf_counter() - total_start
                self.console.print(f"\n[dim]⚡ Ya autenticado: {total_time:.3f}s[/dim]")
                return response
            
            # Si no está autenticado, el comando "identificame" solo confirma
            # La autenticación inicial ya se hizo en main.py
            if self.user_role == "admin":
                response = f"Bienvenido, {self.user_name}."
                self.console.print(f"[bold green]✓ {response}[/bold green]")
                self.authenticated = True
            elif self.user_role == "guest":
                # Preguntar nombre al invitado
                self.console.print("[cyan]Bienvenido. ¿Cuál es su nombre?[/cyan]")
                guest_name = input("Nombre: ").strip()
                if guest_name:
                    self.user_name = guest_name
                    response = f"Bienvenido, {guest_name}. Estás usando Zephyro en modo invitado."
                else:
                    self.user_name = "Invitado"
                    response = "Bienvenido, Invitado. Estás usando Zephyro en modo invitado."
                
                self.console.print(f"[bold green]✓ {response}[/bold green]")
                self.authenticated = True
            
            if speak:
                await self.tts.speak(response)
            
            self.context.add("user", user_input)
            self.context.add("assistant", response)
            
            total_time = time.perf_counter() - total_start
            self.console.print(f"\n[dim]⚡ Identificación: {total_time:.3f}s[/dim]")
            return response
        
        # COMANDO: !recordar
        if user_input.strip() == "!recordar" and self.user_role == "admin":
            self.force_remember_next = True
            response = "✓ La próxima conversación será marcada como importante y guardada en memoria."
            self.console.print(f"[bold cyan]{response}[/bold cyan]")
            return response
        
        # FAST-PATH: Consultas de capacidades/limitaciones → skip LLM, retorna self_state directo
        _cap_triggers = [
            "qué puedes hacer", "que puedes hacer", "qué sabes hacer", "que sabes hacer",
            "cuáles son tus capacidades", "cuales son tus capacidades",
            "qué funciones tienes", "que funciones tienes", "tus habilidades",
            "tus funcionalidades", "para qué sirves", "para que sirves",
            "dime tus capacidades", "capacidades actuales", "qué capacidades tienes",
            "que capacidades tienes", "qué puedes", "lista tus capacidades",
            "/capacidades", "/habilidades_zephyro",
        ]
        _lim_triggers = [
            "qué no puedes", "que no puedes", "cuáles son tus limitaciones",
            "cuales son tus limitaciones", "tus limitaciones", "tus límites",
            "tus limites", "qué limitaciones tienes", "que limitaciones tienes",
            "/limitaciones",
        ]
        _uil = user_input.lower().strip()
        if any(t in _uil for t in _cap_triggers):
            return self._handle_capabilities_query()
        if any(t in _uil for t in _lim_triggers):
            return self._handle_limitations_query()

        # ← NUEVO: COMANDO /reflect — Reflexión bajo demanda
        _reflect_input = user_input.strip()
        if _reflect_input.lower().startswith(("/reflect", "/reflexion", "/reflexión")):
            _reflect_arg = _reflect_input.split(maxsplit=1)[1] if " " in _reflect_input else ""
            return await self._handle_reflect_command(_reflect_arg)
        
        # ← NUEVO: COMANDO /selfstate — Ver estado interno
        if user_input.strip().lower() in ("/selfstate", "/estado", "/self"):
            return self._handle_selfstate_command()
        
        # ← NUEVO: COMANDO /bugs — Ver bug reports
        if user_input.strip().lower() in ("/bugs", "/errores"):
            return self._handle_bugs_command()

        # ─── LENGUAJE NATURAL: activar/desactivar modos de especialidad ──────
        # Detecta frases como "activa el modo educativo", "sal del modo asistente",
        # "pon modo asistente técnico", "vuelve al modo normal", etc.
        _SPECIALTY_NATURAL_TRIGGERS: dict[str, list[str]] = {
            "educational_assistant": [
                "modo educativo", "modo asistente técnico", "modo asistente tecnico",
                "modo educational", "activa modo educativo", "activa el modo educativo",
                "pon modo educativo", "cambia a modo educativo",
                "activa modo asistente técnico", "activa el modo asistente técnico",
                "modo de asistente técnico", "modo de asistente tecnico",
                "modo tutor", "modo explicativo",
            ],
        }
        _OFF_TRIGGERS = [
            "desactiva el modo", "sal del modo", "salte del modo",
            "vuelve al modo normal", "vuelve a modo normal", "modo normal",
            "desactiva especialidad", "cancela el modo", "quita el modo",
            "desactiva modo", "sal de modo", "regresa al modo normal",
        ]
        _uil_specialty = user_input_lower.strip()
        _matched_specialty: str | None = None
        for _sp_name, _sp_triggers in _SPECIALTY_NATURAL_TRIGGERS.items():
            if any(t in _uil_specialty for t in _sp_triggers):
                _matched_specialty = _sp_name
                break
        if _matched_specialty:
            if _matched_specialty in self.context.specialties:
                self.specialty_mode = _matched_specialty
                _label = self.context.specialties.get("_labels", {}).get(_matched_specialty, _matched_specialty)
                response = f"Modo activado: {_label}. Responderé todas las preguntas en ese contexto. Di 'vuelve al modo normal' para salir."
            else:
                response = f"La especialidad '{_matched_specialty}' no está disponible."
            self.context.add("user", user_input)
            self.context.add("assistant", response)
            if speak:
                await self.tts.speak(response)
            return response
        if any(t in _uil_specialty for t in _OFF_TRIGGERS) and self.specialty_mode:
            self.specialty_mode = None
            response = "Volviendo al modo normal. ¿En qué puedo ayudarte?"
            self.context.add("user", user_input)
            self.context.add("assistant", response)
            if speak:
                await self.tts.speak(response)
            return response

        # ← NUEVO: COMANDOS /specialty — Gestionar modos de especialidad
        # Uso:
        #  /specialty list        -> lista especialidades cargadas
        #  /specialty off|disable -> desactiva especialidad
        #  /specialty set NAME    -> activa especialidad NAME
        #  /specialty NAME        -> alias para set
        if user_input.strip().lower().startswith("/specialty"):
            parts = user_input.strip().split()
            # /specialty
            if len(parts) == 1 or parts[1].lower() in ("help", "list"):
                try:
                    keys = list(self.context.specialties.keys())
                    # remove _labels key if present
                    keys = [k for k in keys if not k.startswith("_")]
                    return "Especialidades disponibles: " + ", ".join(keys) if keys else "No hay especialidades definidas."
                except Exception:
                    return "Error obteniendo lista de especialidades."

            cmd = parts[1].lower()
            if cmd in ("off", "disable", "none"):
                self.specialty_mode = None
                return "Especialidad desactivada."

            # soportar: /specialty set NAME  o /specialty NAME
            if cmd in ("set", "on", "use") and len(parts) >= 3:
                name = parts[2]
            else:
                name = parts[1]

            if name in self.context.specialties:
                self.specialty_mode = name
                return f"Especialidad establecida: {name}"
            else:
                return f"No existe la especialidad '{name}'. Usa '/specialty list' para ver las disponibles."
        
        # COMANDO: Calibrar micrófono
        if re.search(r'\b(calibra|calibrar)\b.*\b(micro|micrófono|microfono|mic)\b', user_input_lower) or \
           re.search(r'\b(micro|micrófono|microfono|mic)\b.*\b(calibra|calibrar)\b', user_input_lower):
            if self.stt_available and self._get_stt():
                self._get_stt().calibrate()
                response = "Micrófono calibrado correctamente."
            else:
                response = "El reconocimiento de voz no está disponible para calibrar."
            self.console.print(f"[bold cyan]{response}[/bold cyan]")
            if speak:
                await self.tts.speak(response)
            return response
        
        # ── CALL MODE: cross-turn context buffer ───────────────────────
        # Concatenates short continuations from VAD split turns. El guard de
        # fragmento (longitud + sin verbo imperativo) evita fusionar comandos
        # completos con el turno anterior (ver _is_call_turn_fragment).
        _original_input = user_input
        if self._call_turn_buffer and _is_call_turn_fragment(user_input):
            user_input = f"{self._call_turn_buffer} {user_input}"
        else:
            self._call_turn_buffer = user_input.strip()

        # ── DESPACHO DE SKILLS (kg, reminders, file_ops, git_ops, etc.) ──
        def _commit_skill(inp: str, result: str) -> None:
            """Registra respuesta de skill en contexto conversacional y self_state."""
            self.context.add("user", inp)
            self.context.add("assistant", result)
            _lower_result = result.lower()[:60]
            _status = "error" if any(w in _lower_result for w in (
                "no se pudo", "no encontré", "error", "no puedo", "falta"
            )) else "ok"
            self.self_state.record_action(
                intent="skill", user_input=inp,
                response=result, elapsed_time=time.perf_counter() - total_start,
                status=_status,
            )

        # Inyectar historial conversacional para que los skills contextualicen follow-ups
        ctx.conversation_history = list(self.context.history)

        # ── MULTI-TAREA: "agendá X y después poné música" → 2 sub-tareas ─
        # Se descompone solo si cada segmento tiene su propio verbo/skill. Si
        # da >1 tarea, se orquesta aparte (analiza todo, resuelve ambigüedad al
        # inicio, ejecuta en orden). Tarea única sigue el flujo normal de abajo.
        _subtasks = _decomposer.decompose(user_input, has_action=self._skill_action_checker())
        if len(_subtasks) > 1:
            self._call_turn_buffer = None
            _multi_result = await self._run_multitask(_subtasks, ctx)
            # None → algún segmento no era comando (web search / chat): el
            # mensaje COMPLETO sigue por el flujo single de abajo.
            if _multi_result is not None:
                self.context.add("user", user_input)
                self.context.add("assistant", _multi_result)
                return _multi_result

        _dr = await self.skill_registry.dispatch(user_input, ctx)
        if _dr is not None:
            self._call_turn_buffer = None  # success: clear buffer
            _commit_skill(user_input, _dr.result)
            self._finalize_envelope(
                user_input, ctx, intent="skill",
                steps=[StepRecord(skill_name=_dr.skill_name,
                                  args={"input": user_input},
                                  result_summary=_dr.result[:200],
                                  duration_ms=_dr.duration_ms)],
                response=_dr.result, success=True)
            return _dr.result
        # Combined input didn't match → restore original and retry ONCE.
        # El retry vive dentro del `if` a propósito: sin merge previo `user_input`
        # es idéntico al del dispatch de arriba, así que re-despacharlo volvía a
        # escanear las 19 skills con el mismo string y el mismo resultado (None).
        if user_input != _original_input:
            user_input = _original_input
            self._call_turn_buffer = user_input.strip()
            _dr = await self.skill_registry.dispatch(user_input, ctx)
            if _dr is not None:
                _commit_skill(user_input, _dr.result)
                self._finalize_envelope(
                    user_input, ctx, intent="skill",
                    steps=[StepRecord(skill_name=_dr.skill_name,
                                      args={"input": user_input},
                                      result_summary=_dr.result[:200],
                                      duration_ms=_dr.duration_ms)],
                    response=_dr.result, success=True)
                return _dr.result

        # ���─ SUGERENCIA DE SKILL: Si hay una skill deshabilitada que podría manejar esto ──
        suggestion = self.skill_registry.suggest_for_query(user_input)
        if suggestion:
            return (
                f"No tengo esa capacidad activa, pero tengo la skill '{suggestion.name}' "
                f"({suggestion.description}). Actívala con: /skills enable {suggestion.name}"
            )

        # ── COMANDO: /workspace — Gestión de espacios de trabajo ─────
        if user_input_lower.startswith("/workspace") or user_input_lower.startswith("/ws"):
            return self._handle_workspace_command(user_input)

        # ── COMANDO: /perfil — Ver perfil del usuario ─────────────────
        if user_input_lower in ("/perfil", "/profile", "/usuario"):
            return self._handle_profile_command()

        # ── COMANDO: /resumenes — Ver resúmenes de conversación ───────
        if user_input_lower in ("/resumenes", "/resúmenes", "/summaries"):
            return self._handle_summaries_command()

        # ── User Profile: aprender del mensaje antes de procesarlo ────
        if self.user_profile:
            try:
                self.user_profile.learn_from_message(user_input)
                self.user_profile.log_activity(user_input[:100])
            except Exception:
                pass

        # ── Aprendizaje adaptativo: feedback implícito + estilo ───────
        if self.feedback_system:
            try:
                self.feedback_system.detect_implicit_feedback(
                    user_input,
                    self._last_interaction_id,
                    self._last_user_input_for_feedback,
                )
            except Exception:
                pass
        if self.style_profiler:
            try:
                self.style_profiler.infer_from_input(user_input)
                self.style_profiler.infer_from_correction(user_input)
            except Exception:
                pass

        # PASO 0: Cache para saludos/despedidas/acks (0.0s)
        cache_response = _fast_resp.lookup(user_input)
        if cache_response:
            self.console.print(f"[bold yellow]Assistant:[/bold yellow] {cache_response}")
            
            if speak:
                await self.tts.speak(cache_response)
            
            self.context.add("user", user_input)
            self.context.add("assistant", cache_response)
            
            # ← NUEVO: Registrar en Self State incluso cache hits
            elapsed = time.perf_counter() - total_start
            self.self_state.record_action(
                intent="cache_hit", user_input=user_input,
                response=cache_response, elapsed_time=elapsed
            )
            
            total_time = time.perf_counter() - total_start
            self.console.print(f"\n[dim]⚡ Cache hit: {total_time:.3f}s[/dim]")
            return cache_response
        
        # PASO 1: Router
        if self.user_role == "guest" and force_cloud:
            self.console.print("[yellow]⚠ Modo cloud no disponible para invitados. Usando modelo local.[/yellow]")
            force_cloud = False
            force_local = True
        
        router_start = time.perf_counter()
        _search_gate_task: Optional[asyncio.Task] = None
        if force_cloud:
            # CAPA 0/1 heurístico (regex puro, sin LLM) PRIMERO — barato y atrapa
            # saludos/despedidas/acks (CAPA 0) y automation/vision con keyword (CAPA 1).
            # Esto evita que un saludo ("buenos días, ¿cómo estás?") que no es match
            # exacto del FAST_CACHE caiga al fallback de "chat complejo" (que disparaba
            # el filler de análisis denso y el path cloud pesado con LivingState).
            analysis = await self.router.classify_and_parse(user_input, skip_llm=True)
            _trivial_templates = ("greeting", "farewell", "thanks")
            _is_trivial = analysis.get("response_template") in _trivial_templates
            _is_resolved_intent = analysis.get("intent") in ("automation", "vision", "diagnostic")
            # CAPA 1.15 ("busca información sobre X") ya resolvió una búsqueda web.
            # Escalar acá devolvía None (no es skill/automation) y el reemplazo de
            # abajo PERDÍA needs_web_search → con DeepSeek/Gemini/OpenAI el LLM
            # respondía sin buscar (alucinaba un <tool_call> o "no encontré").
            # Anthropic no lo sufría porque su tool-loop decide buscar por su cuenta.
            _is_info_search = bool(
                analysis.get("intent") == "information" and analysis.get("needs_web_search")
            )
            if not _is_trivial and not _is_resolved_intent and not _is_info_search:
                # La heurística no resolvió acción ni trivial → escalar al extractor
                # LLM (registry-aware) antes de caer al chat (evita alucinación).
                # The tool-loop search gate runs meanwhile (see _start_search_gate).
                _search_gate_task = _start_search_gate(user_input, force_local)
                _escalated = await self._cloud_llm_escalate(user_input)
                analysis = _escalated if _escalated is not None else {
                    "intent": "chat",
                    # Charla casual: NO es "complex". Marcarla simple evita el filler
                    # de "análisis denso" (THINKING_COMPLEX) en un mensaje trivial.
                    "complexity": "simple",
                    "fast_path": False,
                    "response_template": None,
                    "_source": "cloud_fast_lane",
                }
        else:
            _search_gate_task = _start_search_gate(user_input, force_local)
            analysis = await self.router.classify_and_parse(user_input)
        timings['router'] = time.perf_counter() - router_start
        
        intent = analysis.get("intent", "chat")
        complexity = analysis.get("complexity", "simple")
        fast_path = analysis.get("fast_path", False)
        template = analysis.get("response_template")

        # ── RICH HINT: queries de comparación → tabla Markdown ─────────────
        # Nunca N1 (llama 1b emite tablas rotas) y complexity mínimo "complex"
        # para que _select_llm_level elija self.llm (3b/cloud).
        if (
            intent in ("chat", "information")
            and template not in ("greeting", "farewell", "thanks", "math")
            and _RE_TABLE_WORTHY.search(user_input)
        ):
            analysis["rich_hint"] = "table"
            fast_path = False
            if complexity == "simple":
                complexity = "complex"

        # ── DISPATCH FORZADO DE SKILL (Fase 2, cloud) ───────────────────────
        # El classifier registry-aware ruteó a una skill aunque su heurística
        # (matches_command) haya fallado. Forzamos el dispatch vía handle_forced,
        # que entra directo al extractor de la skill. Si devuelve None (la skill
        # tampoco pudo), caemos al chat normal de abajo sin romper.
        if intent == "skill" and analysis.get("skill_name"):
            _sk = self.skill_registry.get_skill(analysis["skill_name"])
            if _sk is not None and _sk.enabled:
                _forced_start = time.perf_counter()
                try:
                    _forced = await _sk.handle_forced(user_input, ctx)
                except Exception as exc:
                    self.console.print(f"[dim]handle_forced({_sk.name}) falló: {exc}[/dim]")
                    _forced = None
                if _forced is not None:
                    if _search_gate_task is not None:
                        _search_gate_task.cancel()
                    self._call_turn_buffer = None
                    _commit_skill(user_input, _forced)
                    self._finalize_envelope(
                        user_input, ctx, intent="skill",
                        steps=[StepRecord(
                            skill_name=_sk.name,
                            args={"input": user_input},
                            result_summary=_forced[:200],
                            duration_ms=(time.perf_counter() - _forced_start) * 1000)],
                        response=_forced, success=True)
                    return _forced
            # No se pudo forzar → tratar como chat para no quedar en limbo.
            intent = "chat"

        # ── REASONING EFFORT (core/reasoning_policy.py) ─────────────────────
        # After the skill dispatch, with the final intent: a skill that failed is chat now.
        # The WS router stores the THINK toggle in ctx.reasoning_mode and never passes
        # the `reasoning_mode` parameter. It only forces effort here: the legacy
        # reasoning_mode branches (CoT preamble + a web search per turn) stay as they were.
        _reasoning = _decide_reasoning(
            user_input, {**analysis, "intent": intent, "fast_path": fast_path}, complexity,
            user_forced=bool(reasoning_mode or getattr(ctx, "reasoning_mode", False)),
        )

        # PASO 2: Seleccionar nivel de modelo
        selected_llm = self._select_llm_level(fast_path, complexity, force_cloud)

        if reasoning_mode and not force_cloud:
            selected_llm = self.llm  # force 3b minimum for reasoning

        # Tool use loop: activar cuando ANTHROPIC_API_KEY está disponible.
        # Usa Anthropic SDK raw (no LangChain), así que funciona independientemente
        # de cuál sea el cloud_provider principal (gemini, deepseek, etc).
        # Claude decide autónomamente si necesita buscar en internet o invocar
        # skills (log_reader, introspection, file_ops) para diagnóstico.
        _use_tool_loop = (
            HAS_SEARCH_TOOLS
            and not fast_path
            and not force_local
            and intent not in ("automation", "search", "web_search", "vision", "code_analysis")
            and bool(os.getenv("ANTHROPIC_API_KEY", ""))
            and selected_llm.is_online
        )
        if not _use_tool_loop and _search_gate_task is not None:
            _search_gate_task.cancel()
            _search_gate_task = None

        # Heurística: detectar intent diagnóstica / introspectiva para habilitar
        # skill tools (diagnose_zephyro / introspect_code / read_project_file).
        # Estos tools le permiten al LLM leer logs/código autónomamente en vez
        # de inventar hipótesis.
        _DIAG_HINTS_RE = re.compile(
            r'\b('
            r'qu[eé]\s+(?:pas[oó]|pasa|fall[oó]|hizo|dice|hace|sucedi[oó]|sucede)|'
            r'por\s+qu[eé]\s+\w+|'                # "por qué reproduciste/fallaste/X"
            r'investig[ao]r?|investigaste|'
            r'diagn[oó]stic|'
            r'realiza(?:r)?\s+(?:un\s+)?(?:diagn|an[aá]lisis|chequeo)|'
            r'no\s+(?:funcion[oó]?|sirvi[oó]|se\s+ejecut)|'
            r'fall[oó]\s+(?:el|la|al)|'
            r'revisa(?:r)?\s+(?:los\s+)?(?:logs?|lo\s+que\s+pas[oó]|el\s+c[oó]digo)|'
            r'reviso\s+(?:los\s+)?logs?|'
            r'analiza(?:r)?\s+(?:los\s+)?(?:logs?|lo\s+que\s+pas[oó])|'
            r'd[oó]nde\s+est[aá]|c[oó]mo\s+funciona|qu[eé]\s+hace\s+la|'
            r'mu[eé]strame|mostr[aá]me|expl[ií]came|'
            r'haz?\s+(?:un\s+)?(?:diagn[oó]stic|an[aá]lisis)|'
            r'tier\s*\d|'
            r'taller\s*\d|'           # STT a veces transcribe "tier 1" como "taller 1"
            r'lee\s+(?:el\s+)?(?:archivo|c[oó]digo|log)|'
            r'inspecc?iona|inspecciono|'
            r'verifica(?:r)?\s+(?:el|si|c[oó]mo)'
            r')\b',
            re.IGNORECASE,
        )
        _has_diag_intent = bool(_DIAG_HINTS_RE.search(user_input or ""))

        # ── GUARD: pregunta de conocimiento GENERAL (no sobre Zephyro) ──────
        # Los tools de introspección (diagnose/introspect/read_file) leen los
        # propios logs/código de Zephyro. Verbos genéricos como "cómo funciona X"
        # o "explícame Y" matchean _DIAG_HINTS_RE pero suelen ser conocimiento
        # general (biología, historia, etc.). Si la query es claramente de
        # conocimiento general Y no menciona nada inequívocamente interno de Zephyro,
        # NO se deben inyectar esos tools (si no, Claude reinterpreta "célula" como
        # "módulo de Zephyro" y se niega a responder).
        #
        # IMPORTANTE: este guard SÓLO actúa cuando _KNOWLEDGE_ONLY_RE matchea (pregunta
        # tipo concepto). Las queries diagnósticas reales ("revisá los logs", "qué pasó",
        # "por qué falló") NO matchean _KNOWLEDGE_ONLY_RE → _is_general_knowledge=False →
        # los tools se inyectan igual vía _has_diag_intent. Por eso _ZEPHYRO_INTERNAL_RE
        # sólo necesita capturar introspección PHRASED como concepto ("cómo funciona el
        # orchestrator", "explícame tu arquitectura"). Términos comunes (error, función,
        # módulo, router, sistema, bug, frontend/backend) se EXCLUYEN a propósito: matchean
        # conocimiento general ("el sistema nervioso", "función del hígado") y reintroducían
        # el secuestro de persona.
        _ZEPHYRO_INTERNAL_RE = re.compile(
            r'\b(zephyro|orchestrator|gitnexus|'
            r'tus?\s+(?:c[oó]digo|sistema|arquitectura|implementaci[oó]n|prompt|skills?|m[oó]dulos?|capacidades|logs?)|'
            r'(?:este|el)\s+(?:proyecto|asistente))\b',
            re.IGNORECASE,
        )
        try:
            from modules.integrations.api_registry import _KNOWLEDGE_ONLY_RE as _KO_RE
            _is_general_knowledge = bool(_KO_RE.search(user_input or "")) and \
                not _ZEPHYRO_INTERNAL_RE.search(user_input or "")
        except Exception:
            _is_general_knowledge = False

        # PASO 3: Procesar según intent
        if intent in ("automation", "search", "web_search"):
            # ── CONFIRMACIÓN: ambigüedad (router marcó needs_confirmation) o
            # riesgo (action_risk_rules). Se encola por sesión y se pregunta sí/no.
            _risk = _classify_action_risk(analysis)
            if analysis.get("needs_confirmation") or _risk.needs_confirmation:
                response = self._request_automation_confirmation(_sid, analysis, _risk.reason)
                self.context.add("user", user_input)
                self.context.add("assistant", response)
                total_time = time.perf_counter() - total_start
                self.self_state.record_action(
                    intent="confirmation_requested", user_input=user_input,
                    response=response, elapsed_time=total_time,
                    details={
                        "pending_action": analysis.get("interact_action") or analysis.get("action"),
                        "pending_target": analysis.get("interact_target") or analysis.get("target"),
                        "risk": _risk.level.value,
                    }
                )
                return response

            # ── MULTI_ACTION: ejecutar lista de acciones secuencialmente ──
            if analysis.get("automation_intent") == "MULTI_ACTION":
                actions = analysis.get("actions", [])
                results: list[str] = []
                for i, action_item in enumerate(actions):
                    action_target = action_item.get("target", "desconocido")
                    try:
                        single_analysis = {
                            **action_item,
                            "intent": "automation",
                            "automation_intent": action_item.get("automation_intent", "OPEN_APP"),
                            "complexity": analysis.get("complexity", "simple"),
                            "complexity_score": analysis.get("complexity_score", 0.1),
                        }
                        single_result = await self.automation.execute(single_analysis)
                        results.append(f"✓ {action_target}: {single_result}")
                    except Exception as e:
                        results.append(f"✗ {action_target}: {e}")
                result = "\n".join(results) if results else "No se ejecutaron acciones."
                self.console.print(f"[bold green]✓[/bold green] {result}")
            else:
                auto_start = time.perf_counter()
                try:
                    result = await self.automation.execute(analysis)
                except Exception as auto_exc:
                    # Mensaje honesto al usuario: nunca dejar que la excepción
                    # burbujee como frame de error crudo ni que el LLM invente
                    # una confirmación de éxito.
                    _auto_intent = analysis.get("automation_intent", "?")
                    _auto_target = (
                        analysis.get("search_query") or analysis.get("target") or "?"
                    )
                    result = (
                        f"No pude ejecutar la automatización ({_auto_intent} → "
                        f"{_auto_target}): {type(auto_exc).__name__}: {auto_exc}"
                    )
                    self.console.print(
                        f"[red]✗ automation.execute falló: "
                        f"{type(auto_exc).__name__}: {auto_exc!r}[/red]"
                    )
                timings['automation'] = time.perf_counter() - auto_start

            # BROWSE_AND_EXTRACT: si el resultado contiene contenido extraído,
            # pasarlo al LLM para sintetizar una respuesta natural
            if isinstance(result, str) and result.startswith("__BROWSE_EXTRACT__"):
                try:
                    extract_query = analysis.get("extract_query", user_input)
                    synth_prompt = (
                        f"El usuario pidió: '{user_input}'\n\n"
                        f"Se navegó a la página y se extrajo el siguiente contenido:\n\n"
                        f"{result[len('__BROWSE_EXTRACT__'):].strip()}\n\n"
                        f"Basándote en este contenido, responde a lo que el usuario preguntó "
                        f"de forma concisa y útil. Si hay datos numéricos, porcentajes o "
                        f"estadísticas relevantes, inclúyelos. Responde en español."
                    )
                    _synth_llm = self.llm
                    result = await asyncio.to_thread(
                        lambda: _synth_llm.generate(synth_prompt, force_cloud=True, force_local=False)
                    )
                    result = sanitize_response(result)
                except Exception as synth_err:
                    self.console.print(f"[yellow]⚠ Error sintetizando: {synth_err}[/yellow]")
                    # Fallback: retornar el contenido raw (sin el marker)
                    result = result[len("__BROWSE_EXTRACT__"):].strip()

            # BROWSE_AND_EXTRACT (híbrido): la página oficial no rindió (falla/vacía/contenido
            # fino) → red de seguridad con búsqueda web (Tavily) + síntesis, para redactar
            # igual una respuesta en vez de "no pude acceder a la página".
            elif isinstance(result, str) and result.startswith("__BROWSE_FALLBACK__"):
                _fb_lines = result.split("\n")
                _fb_query = _fb_lines[1].strip() if len(_fb_lines) > 1 and _fb_lines[1].strip() else user_input
                _fb_hint = _fb_lines[2].strip() if len(_fb_lines) > 2 else ""
                _fb_effective = (f"{_fb_query} {_fb_hint}".strip()) or user_input
                try:
                    if HAS_API_REGISTRY:
                        _synth_llm_b = self.llm
                        _synth_fn_b = lambda p: _synth_llm_b.generate(
                            p, force_cloud=True, force_local=False
                        )
                        web_fb = await search_web_direct(_fb_effective, llm_fn=_synth_fn_b)
                        _no_res = f"No encontré resultados en internet para: '{_fb_effective}'"
                        if web_fb and web_fb.strip() and web_fb.strip() != _no_res:
                            result = sanitize_response(web_fb)
                            self._last_web_search_topic = _fb_effective[:150]
                        else:
                            result = f"No encontré información sobre: {_fb_query}"
                    else:
                        result = f"No pude acceder a la página ni buscar en la web sobre: {_fb_query}"
                except Exception as fb_err:
                    self.console.print(f"[yellow]⚠ Error en fallback de búsqueda: {fb_err}[/yellow]")
                    result = f"No pude completar la investigación sobre: {_fb_query}"
            
            self.console.print(f"[bold green]✓[/bold green] {result}")
            self.context.set_last_automation(analysis)
            self.context.add("user", user_input)
            self.context.add("assistant", result)

            total_time = time.perf_counter() - total_start
            self._print_timing_stats(timings, total_time, intent, complexity, ctx=ctx)
            self.context.record_latency(intent, complexity, total_time)

            self.self_state.record_action(
                intent=intent, user_input=user_input,
                response=result, elapsed_time=total_time,
                details={"action": analysis.get("action"), "target": analysis.get("target")}
            )

            # Publicar para que VerificationAgent detecte "describió en vez de ejecutar"
            try:
                from core.event_bus import get_bus, AUTOMATION_EXECUTED
                get_bus().publish(AUTOMATION_EXECUTED, {
                    "intent": analysis.get("automation_intent", "unknown"),
                    "user_input": user_input,
                    "response": result,
                    "target": analysis.get("target", ""),
                    "_source": analysis.get("_source", "capa1"),
                })
            except Exception:
                pass

            # Guardar en historial (non-blocking) — automation también debe persistir
            if self._effective_user_role(ctx) == "admin" and self.history and self.importance_classifier:
                _r, _i, _t, _role = result, user_input, total_time, self._effective_user_role(ctx)
                asyncio.get_event_loop().call_soon(
                    lambda r=_r, i=_i, t=_t, role=_role: asyncio.ensure_future(asyncio.to_thread(
                        self._save_to_history, user_input=i, assistant_response=r,
                        intent="automation", complexity=complexity,
                        elapsed_time=t, mode_used="auto", skip_save=False,
                        user_role=role
                    ))
                )

            self._finalize_envelope(
                user_input, ctx, intent=intent,
                steps=[StepRecord(
                    skill_name="automation",
                    args={"action": analysis.get("action"),
                          "target": analysis.get("target"),
                          "automation_intent": analysis.get("automation_intent")},
                    result_summary=(result or "")[:200],
                    duration_ms=int(timings.get("automation", 0) * 1000),
                    provider=analysis.get("provider"))],
                response=result,
                complexity_score=analysis.get("complexity_score"),
                success=True)
            return result

        elif intent == "screen_query":
            return await self._handle_screen_query(user_input, speak, total_start, timings)
        
        elif intent == "vision":
            vision_start = time.perf_counter()
            image_path = self.vision_capture.capture()
            description = self.vision_analyze.analyze(image_path, user_input)
            timings['vision'] = time.perf_counter() - vision_start
            
            self.console.print(f"[bold yellow]Assistant:[/bold yellow] {description}")
            if speak:
                await self.tts.speak(description)
            
            self.context.add("user", user_input)
            self.context.add("assistant", description)
            
            total_time = time.perf_counter() - total_start
            self._print_timing_stats(timings, total_time, intent, complexity, ctx=ctx)
            self.context.record_latency(intent, complexity, total_time)

            # ← NUEVO: Self State tracking para vision
            self.self_state.record_action(
                intent=intent, user_input=user_input,
                response=description, elapsed_time=total_time
            )
            
            return description
        
        elif intent == "code_analysis":
            return await self._handle_code_analysis(
                user_input, analysis, speak, total_start, timings
            )

        elif intent == "diagnostic":
            return await self._handle_diagnostic(
                user_input, analysis, speak, total_start, timings
            )

        elif intent == "product_search":
            return await self._handle_product_search(
                user_input, analysis, speak, total_start, timings
            )

        # ── Enriquecimiento de query para follow-ups sin contexto geográfico ────
        # Calculado una sola vez aquí, usado tanto en api_registry como en search_web_direct.
        _search_ctx_hint = (self._last_web_search_topic or "")[:150]
        _search_effective_query = user_input
        if _search_ctx_hint:
            _search_stopwords = {
                'sobre', 'acerca', 'cual', 'cuál', 'qué', 'que', 'como', 'cómo',
                'puedes', 'puede', 'para', 'esta', 'esto', 'aquí', 'alli',
                'investigar', 'buscar', 'dime', 'zephyro', 'jarvis',
                'situa', 'situación', 'situacion', 'información', 'informacion',
                'votaciones', 'votacion', 'elecciones', 'eleccion',
                'candidatos', 'candidato', 'candidata', 'candidatas',
                'votantes', 'votante', 'segunda', 'primera', 'vuelta',
                'presidente', 'presidenta', 'presidencial',
                'noticias', 'noticia', 'actualidad', 'actual', 'actuales',
            }
            _followup_marker_re = re.compile(
                r'\b(pero|sin embargo|me refiero|me referí|'
                r'hablando de eso|en ese contexto|en ese sentido|'
                r'siguiendo con|volviendo a|sobre lo mismo|'
                r'ahorita mismo|ahora mismo)\b',
                re.IGNORECASE,
            )
            _topic_entity_kws = {
                w for w in re.findall(r'[a-záéíóúüñ]{4,}', _search_ctx_hint.lower())
                if w not in _search_stopwords
            }
            _cur_lower = user_input.lower()
            _is_followup = bool(_followup_marker_re.search(user_input))
            _entity_missing = _topic_entity_kws and not any(
                kw in _cur_lower for kw in _topic_entity_kws
            )
            if _is_followup or _entity_missing:
                _search_effective_query = f"{_search_ctx_hint.strip()} {user_input.strip()}"
                self.console.print(f"[dim cyan]🔗 Follow-up enriched: {_search_effective_query[:80]}...[/dim cyan]")

        # ── API REGISTRY: Consultar APIs externas si aplica ──────────
        if HAS_API_REGISTRY and not force_local:
            api_check = is_external_api_query(user_input)
            if api_check:
                api_start = time.perf_counter()
                try:
                    # Pasar llm.generate como llm_fn para que web_search pueda resumir.
                    # preset='calidad' (num_predict=1024) evita que la síntesis multi-fuente
                    # se corte a mitad de oración cuando el ruteo queda local (el preset
                    # se ignora en cloud, así que es seguro pasarlo siempre).
                    _llm_fn = lambda p: selected_llm.generate(p, force_cloud=force_cloud, force_local=force_local, preset='calidad')
                    api_result = await query_external_api(_search_effective_query, llm_fn=_llm_fn)
                    timings['api_registry'] = time.perf_counter() - api_start
                    if api_result:
                        # Safety net: si el resultado es muy largo/crudo, resumir con LLM
                        if len(api_result) > 600 and _llm_fn:
                            _looks_raw = (
                                "From Wikipedia" in api_result
                                or api_result.count("\n") > 15
                                or "disambiguation" in api_result.lower()
                                or api_result.count(",") > 20
                            )
                            if _looks_raw:
                                try:
                                    _summ = _llm_fn(
                                        f"El usuario preguntó: \"{user_input}\"\n\n"
                                        f"Datos obtenidos:\n{api_result[:2500]}\n\n"
                                        "Instrucciones:\n"
                                        "- Responde en español de forma clara y concisa (3-5 oraciones).\n"
                                        "- Responde directamente la pregunta del usuario.\n"
                                        "- NO uses emojis, asteriscos ni markdown.\n"
                                    )
                                    if _summ and len(_summ.strip()) > 20:
                                        api_result = _summ.strip()
                                except Exception:
                                    pass  # Usar resultado original si LLM falla
                        
                        # Sanitizar: eliminar emojis y markdown bold
                        api_result = sanitize_response(api_result)
                        api_result = re.sub(r'\*\*(.+?)\*\*', r'\1', api_result)
                        
                        # Detectar saludo en la query original y anteponer saludo personalizado
                        api_result = self._prepend_greeting_if_needed(user_input, api_result)
                        
                        # Guardar tema si fue una búsqueda web (para follow-ups)
                        from modules.integrations.api_registry import _api_registry
                        _matched_route = next(
                            (r for r in _api_registry._routes if r.name == "web_search" and r.matches(user_input)[0]),
                            None
                        )
                        if _matched_route:
                            # Preservar topic original si este turno fue un follow-up enriquecido
                            if _search_effective_query != user_input and _search_ctx_hint:
                                self._last_web_search_topic = _search_ctx_hint[:150]
                            else:
                                self._last_web_search_topic = user_input[:150]
                            # Enhancement v4: para queries volátiles, verificar contra la
                            # fuente más autoritativa y ofrecer el detalle si aporta algo.
                            api_result = await self._maybe_launch_enhancement(
                                user_input, api_result)

                        self.console.print(f"[bold green]✓ API Registry:[/bold green] {api_result[:100]}...")
                        self.context.add("user", user_input)
                        self.context.add("assistant", api_result)
                        total_time = time.perf_counter() - total_start
                        self._print_timing_stats(timings, total_time, "api_query", "external", ctx=ctx)
                        # Knowledge Graph: procesar mensaje
                        if self.knowledge_graph:
                            self.knowledge_graph.process_message(user_input, role="user")

                        # Guardar en historial (non-blocking) — API Registry también debe persistir
                        if self._effective_user_role(ctx) == "admin" and self.history and self.importance_classifier:
                            _r, _i, _t, _role = api_result, user_input, total_time, self._effective_user_role(ctx)
                            asyncio.get_event_loop().call_soon(
                                lambda r=_r, i=_i, t=_t, role=_role: asyncio.ensure_future(asyncio.to_thread(
                                    self._save_to_history, user_input=i, assistant_response=r,
                                    intent="api_query", complexity="external",
                                    elapsed_time=t, mode_used="cloud" if force_cloud else "auto",
                                    skip_save=False, user_role=role
                                ))
                            )

                        return api_result
                except Exception as api_err:
                    self.console.print(f"[yellow]⚠ API Registry error: {api_err}[/yellow]")

            elif not fast_path and not _use_tool_loop:   # keyword match failed, check if we should still search
                classifier_start = time.perf_counter()
                _classifier = self.llm_classifier or self.llm_fast
                _ctx_hint = (self._last_web_search_topic or "")[:150]
                if force_cloud:
                    # Cloud mode: Layer 0 regex gratis (0ms) — sin overhead LLM extra
                    should_search = analysis.get("needs_web_search", False)
                    if not should_search:
                        _ql = user_input.strip().lower()
                        for _p in _ALWAYS_SEARCH_PATTERNS:
                            if re.search(_p, _ql):
                                should_search = True
                                break
                    # Follow-up: si había búsqueda previa reciente
                    if not should_search and _ctx_hint:
                        # Heurística rápida: detectar follow-ups sin LLM local
                        _ql_words = set(_ql.split())
                        _followup_cues = {
                            'más', 'mas', 'también', 'tambien', 'además', 'ademas',
                            'dime', 'listame', 'listamelos', 'lista', 'cuáles', 'cuales',
                            'quiénes', 'quienes', 'todos', 'completa', 'completo',
                            'detalle', 'detalles', 'detállame', 'detallame', 'detalla',
                            'amplía', 'amplia', 'profundiza', 'explícame', 'explicame',
                            'específicamente', 'especificamente', 'exactamente',
                        }
                        _has_followup_cue = bool(_ql_words & _followup_cues)
                        # Referencia implícita al tema anterior (pronombres, "esos", "esas")
                        _has_reference = bool(re.search(
                            r'\b(esos|esas|estos|estas|ellos|ellas|los mismos|las mismas|'
                            r'al respecto|sobre eso|de eso|de esto|lo mismo|lo anterior)\b', _ql
                        ))
                        if _has_followup_cue or _has_reference:
                            should_search = True
                        else:
                            # Fallback: clasificador LLM (async)
                            should_search = await asyncio.to_thread(
                                needs_web_search, user_input, _classifier, _ctx_hint
                            )
                else:
                    should_search = analysis.get("needs_web_search", False) or needs_web_search(
                        user_input, _classifier, context=_ctx_hint
                    )
                timings['web_classifier'] = time.perf_counter() - classifier_start

                if should_search:
                    self.console.print("[dim cyan]🌐 Web classifier: necesita internet[/dim cyan]")
                    await ctx.emit_status("searching", _pick_searching())
                    web_start = time.perf_counter()
                    try:
                        # Síntesis final: usar cloud (Haiku/Sonnet) si disponible
                        _synth_llm = self.llm
                        _synth_fn = lambda p: _synth_llm.generate(
                            p, force_cloud=True, force_local=False
                        )
                        web_result = await search_web_direct(_search_effective_query, llm_fn=_synth_fn)
                        timings['web_direct'] = time.perf_counter() - web_start
                        if web_result:
                            # Sanitizar resultado web
                            web_result = sanitize_response(web_result)
                            # Preservar topic original si fue enriquecido como follow-up
                            if _search_effective_query != user_input and _search_ctx_hint:
                                self._last_web_search_topic = _search_ctx_hint[:150]
                            else:
                                self._last_web_search_topic = user_input[:150]
                            self.console.print(f"[bold green]✓ Web Direct:[/bold green] {web_result[:100]}...")
                            self.context.add("user", user_input)
                            self.context.add("assistant", web_result)
                            total_time = time.perf_counter() - total_start
                            self._print_timing_stats(timings, total_time, "web_search", "external", ctx=ctx)
                            if self.knowledge_graph:
                                self.knowledge_graph.process_message(user_input, role="user")
                            return web_result
                    except Exception as web_err:
                        self.console.print(f"[yellow]⚠ Web Direct error: {web_err}[/yellow]")
                        # Falls through to normal LLM response

        # ── Gate de baja confianza (absorbe el guard anti-alucinación) ───
        # El router clasificó como chat. Antes de mandar al LLM genérico,
        # consultamos el registry: si alguna skill detectó intención (aunque
        # no pudo resolver el comando), preguntamos en vez de responder como
        # chatbot — y guardamos el pending para resolver la respuesta solos.
        if intent == "chat" and analysis.get("_source") != "cloud_fast_lane":
            _gate_cands = []
            try:
                for _sc in self.skill_registry.rank_candidates(user_input):
                    _gate_cands.append(_gate.make_skill_candidate(_sc.skill, _sc.score))
            except Exception:
                pass
            if _gate_cands:
                _dec = _gate.decide(user_input, _gate_cands)
                if _dec.action == "clarify":
                    self._pending_clarification = {
                        "plan": [{"task": user_input, "chosen": None,
                                  "options": _dec.options}],
                        "ts": time.time(), "original_input": user_input,
                    }
                    return _dec.question

            # Caso automation no detectada: verbo + app/sitio conocido. Se
            # conserva tal cual (el router no supo interpretar el comando).
            _user_lower = user_input.lower()
            _known_targets = list(NATIVE_APPS) + list(SITE_KEYWORDS)
            _has_action_verb = any(
                re.search(rf"\b{re.escape(v)}\b", _user_lower) for v in AUTO_VERBS
            )
            if _has_action_verb:
                _matched = []
                for t in _known_targets:
                    if re.search(rf"\b{re.escape(t)}\b", _user_lower):
                        _matched.append(t)
                if _matched:
                    _targets_str = ", ".join(_matched[:3])
                    return (
                        f"No pude interpretar ese comando como automatización.\n"
                        f"¿Querés que abra {_targets_str} en el navegador o como app nativa?"
                    )

        # CHAT/INFORMATION
        context_start = time.perf_counter()
        
        # NUEVO: Predecir qué modelo se usará para incluirlo en el prompt
        # Esto es crítico para que el modelo sepa quién es cuando le preguntan
        predicted_model = "unknown"
        predicted_provider = "unknown"
        
        if force_cloud:
            # Si se fuerza cloud, usará el cloud disponible
            predicted_provider = selected_llm.cloud_provider if selected_llm.cloud_model else "local"
            if predicted_provider == "anthropic":
                predicted_model = selected_llm.anthropic_model
            elif predicted_provider == "deepseek":
                predicted_model = selected_llm.deepseek_model
            elif cloud_chat_model(predicted_provider):
                predicted_model = cloud_chat_model(predicted_provider)
            else:
                predicted_model = selected_llm.local_model_name
                predicted_provider = "local"
        elif fast_path:
            # Fast path usa llm_fast (llama3.2:1b)
            predicted_model = "llama3.2:1b"
            predicted_provider = "local"
        elif complexity == "complex" and selected_llm.cloud_model and selected_llm.is_online:
            # Complejo con cloud disponible: usará cloud
            predicted_provider = selected_llm.cloud_provider
            if predicted_provider == "anthropic":
                predicted_model = selected_llm.anthropic_model
            elif predicted_provider == "deepseek":
                predicted_model = selected_llm.deepseek_model
            elif cloud_chat_model(predicted_provider):
                predicted_model = cloud_chat_model(predicted_provider)
        else:
            # Usará el modelo local predeterminado
            predicted_model = selected_llm.local_model_name
            predicted_provider = "local"
        
        system_info = None
        if self.user_role == "admin" or True:
            system_info = {
                "current_model": predicted_model,
                "current_provider": predicted_provider,
                "mode_used": "cloud" if force_cloud else ("local" if force_local else "auto"),
                "user_role": self.user_role,
                "session_id": self.session_id
            }
            self.context.last_system_info = system_info
        
        skip_memory_decision = fast_path or (force_cloud and not self.specialty_mode)

        # Obtener contexto de Self State para inyectar en el prompt
        self_state_context = self.self_state.get_relevant_capabilities(user_input)

        # Refuerzo anti-mismatch: recordatorios de capability_mismatches previos relevantes
        _mismatch_reminder = self.self_state.get_mismatch_reinforcement(intent)
        if _mismatch_reminder:
            self_state_context = (
                _mismatch_reminder + "\n\n" + self_state_context
                if self_state_context else _mismatch_reminder
            )

        # Phase 3: Proactive preconditions + consciousness snapshot injection
        _c_action = (
            "temporal_search"
            if analysis.get("needs_web_search") or intent in ("web_search", "search", "information")
            else "browser_action" if intent == "automation"
            else "llm_call"
        )
        _decision = self.consciousness.pre_execute(_c_action, user_input, analysis)
        if not _decision.allowed:
            return (
                _decision.reason
                + (f"\n\n💡 {_decision.suggested_fix}" if _decision.suggested_fix else "")
            )
        if _decision.enriched_context:
            self_state_context = (
                _decision.enriched_context + "\n\n" + self_state_context
                if self_state_context else _decision.enriched_context
            )

        # Reasoning mode: prepare chain-of-thought preamble + optional web context
        user_input_for_prompt = user_input
        reasoning_code_context = None
        if reasoning_mode:
            reasoning_preamble = (
                "[MODO RAZONAMIENTO ACTIVO]\n"
                "Piensa paso a paso antes de responder:\n"
                "1. ¿Qué exactamente se está preguntando?\n"
                "2. ¿Qué información tengo disponible?\n"
                "3. ¿Hay múltiples ángulos o interpretaciones?\n"
                "4. ¿Cuál es la respuesta más precisa y completa?\n"
                "Responde de forma detallada y bien fundamentada.\n"
            )
            user_input_for_prompt = f"{reasoning_preamble}\nPregunta: {user_input}"
            if not fast_path and HAS_API_REGISTRY:
                try:
                    web_ctx = await search_web_direct(user_input)
                    if web_ctx:
                        reasoning_code_context = f"[Contexto web actual para razonar]\n{web_ctx}\n"
                        self.console.print("[dim cyan]🧠 Razonamiento: contexto web obtenido[/dim cyan]")
                except Exception as _rea_err:
                    self.console.print(f"[yellow]⚠ Reasoning web search: {_rea_err}[/yellow]")

        # Few-shot examples + long-term memory: both embed the input (Ollama), so they run
        # in worker threads and concurrently. They used to run on the event loop — and the
        # memory one silently failed (a sync method shadowed the async one), so build_prompt
        # embedded the same text a second time. Fast-path greetings skip few-shot entirely.
        async def _few_shot() -> str:
            if not self.few_shot_selector or fast_path:
                return ""
            try:
                examples = await asyncio.to_thread(
                    self.few_shot_selector.get_examples, user_input_for_prompt, 2
                )
                return self.few_shot_selector.format_for_prompt(examples)
            except Exception:
                return ""

        async def _memory():
            if skip_memory_decision:
                return None
            # "" (searched, nothing found) keeps build_prompt from searching again; None
            # (no store / failure) lets it fall back to its own lookup.
            return await self.context.get_vector_context_async(user_input_for_prompt, k=1)

        _few_shot_str, _precomputed_memory = await asyncio.gather(_few_shot(), _memory())

        _vision_context_str = None
        if ctx is None or getattr(ctx, "vision_context", None) is None:
            try:
                from routers.vision import get_vision_context as _gvc
                _vision_context_str = _gvc()
            except Exception:
                pass
        else:
            _vision_context_str = ctx.vision_context

        # ── VISION FAST-PATH: skip LLM for explicit screen queries ──────────
        _SCREEN_QUERY_RE = re.compile(
            r'\b(qu[eé]\s+(ves|est[aá]s?\s+viendo|hay\s+en\s+(la\s+)?pantalla|tienes\s+en\s+pantalla)'
            r'|desc(ribe|rib[eé])\s+(mi\s+)?pantalla'
            r'|qu[eé]\s+est[oó]y\s+haciendo'
            r'|qu[eé]\s+(app|aplicaci[oó]n|programa)\s+tengo\s+abierta?'
            r'|mir[ao]\s+(mi\s+)?pantalla'
            r'|qu[eé]\s+veo\b'
            r'|an[aá]lisis\s+de\s+(mi\s+)?pantalla)\b',
            re.IGNORECASE
        )
        if _SCREEN_QUERY_RE.search(user_input) and not reasoning_mode:
            try:
                from routers.vision import _continuous_vision as _cv
                _ctx_data = _cv["context"]
                _is_fresh = (
                    _cv["active"]
                    and (time.time() - _ctx_data.get("last_updated_ts", 0)) < 30
                    and (_ctx_data.get("active_app") or _ctx_data.get("user_activity"))
                )

                if _is_fresh:
                    _parts = []
                    if _ctx_data.get("active_app"):
                        _parts.append(f"Tienes {_ctx_data['active_app']} abierto.")
                    if _ctx_data.get("user_activity"):
                        _parts.append(_ctx_data["user_activity"])
                    if _ctx_data.get("context_hint"):
                        _parts.append(_ctx_data["context_hint"])
                    _direct = " ".join(_parts)
                    self.context.add("user", user_input)
                    self.context.add("assistant", _direct)
                    return _direct
                else:
                    # Contexto ausente o desactualizado → captura fresca
                    if ctx:
                        await ctx.emit_status("thinking", "Analizando pantalla...")
                    _img_path = self.vision_capture.capture()
                    _vfp_prompt = (
                        'Analiza esta captura de pantalla. Responde SOLO en JSON: '
                        '{"active_app": "nombre de la app principal", '
                        '"user_activity": "qué está haciendo el usuario (1 oración)", '
                        '"context_hint": "en qué podría ayudar el asistente (breve, o vacío)"}'
                    )
                    _fresh_result = await asyncio.to_thread(
                        self.vision_analyze.analyze,
                        _img_path, _vfp_prompt, True, "anthropic"
                    )
                    try:
                        from pathlib import Path as _Path
                        _Path(_img_path).unlink()
                    except Exception:
                        pass
                    import json as _json_vfp
                    _direct = _fresh_result[:200]
                    if '{' in _fresh_result:
                        try:
                            _p = _json_vfp.loads(
                                _fresh_result[_fresh_result.index('{'):_fresh_result.rindex('}') + 1]
                            )
                            _parts = []
                            if _p.get("active_app"):
                                _parts.append(f"Tienes {_p['active_app']} abierto.")
                            if _p.get("user_activity"):
                                _parts.append(_p["user_activity"])
                            if _p.get("context_hint"):
                                _parts.append(_p["context_hint"])
                            _direct = " ".join(_parts) if _parts else _fresh_result[:200]
                        except Exception:
                            pass
                    self.context.add("user", user_input)
                    self.context.add("assistant", _direct)
                    return _direct
            except Exception:
                pass  # Fall through to normal LLM path
        # ────────────────────────────────────────────────────────────────────

        # Inyectar LivingState snapshot si hay issues activos (no solo en guards).
        # EXCEPCIÓN: en respuestas triviales (saludo/despedida/ack) NO inyectar el
        # snapshot — un "buenos días" no debe soltar "187 issues abiertos en el tracker".
        if (self.consciousness and getattr(self.consciousness, "_initialized", False)
                and template not in ("greeting", "farewell", "thanks")):
            try:
                snap = self.consciousness.get_snapshot(tier=1)
                if snap and ("Issues abiertos" in snap or "degraded" in snap or "broken" in snap):
                    self_state_context = (
                        snap + "\n\n" + self_state_context
                        if self_state_context else snap
                    )
            except Exception:
                pass

        # A cloud generation needs a configured cloud model, not just internet:
        # `generate(force_cloud=True)` raises without one. `is_online` alone made a
        # local-only setup crash on table/concept queries and fed the cloud-only
        # Mermaid/chart rules to llama turns.
        _cloud_ready = (
            not force_local
            and getattr(selected_llm, "is_online", False)
            and getattr(selected_llm, "cloud_model", None) is not None
        )
        _force_cloud_gen = force_cloud or (
            _cloud_ready
            and (
                analysis.get("response_template") == "concept"
                or analysis.get("rich_hint") == "table"
            )
        )
        # Rich cloud-only rules go in only when this turn will actually run on cloud
        # (forced, or via the Anthropic tool loop, which always generates on cloud).
        _cloud_tier = _cloud_ready and (_force_cloud_gen or _use_tool_loop)

        full_prompt = self.context.build_prompt(
            user_input_for_prompt,
            skip_memory=skip_memory_decision,
            precomputed_memory=_precomputed_memory,
            template=template,
            detail_level=analysis.get("detail_level"),
            specialty_mode=self.specialty_mode,
            system_info=system_info,
            self_state=self.self_state,
            self_state_context=self_state_context,
            code_context=reasoning_code_context,
            # Voice formatting/brevity: a call-mode turn arrives with ctx.speak=True but
            # speak=False (audio goes to the client, never played on the host).
            speak=speak or bool(getattr(ctx, "speak", False)),
            user_profile=self.user_profile,
            few_shot_examples=_few_shot_str,
            reflection=self.reflection_engine.get_current_reflection() if self.reflection_engine else "",
            style_instructions=self.style_profiler.get_style_instructions() if self.style_profiler else "",
            skill_summaries=self.skill_registry.get_prompt_summaries(),
            device_hint=self._get_device_hint(ctx),
            vision_context=_vision_context_str,
            rich_hint=analysis.get("rich_hint"),
            cloud_tier=_cloud_tier,
        )
        timings['context_build'] = time.perf_counter() - context_start
        
        # Determinar preset según complejidad
        preset_to_use = None
        if analysis.get("response_template") == "concept":
            detail_level = analysis.get("detail_level", "normal")
            preset_map = {
                "brief": "concepto_breve",
                "normal": "concepto_normal",
                "detailed": "concepto_detallado"
            }
            preset_to_use = preset_map.get(detail_level, "concepto_normal")
        elif fast_path or complexity == "simple":
            preset_to_use = "rapido"
        elif complexity == "complex":
            preset_to_use = "balanceado"

        if reasoning_mode:
            preset_to_use = "calidad"
        
        # ── LLM CACHE: Buscar en cache antes de generar ───────────────
        cache_key = full_prompt
        if _reasoning is not None:
            # An answer generated at another effort must not be reused (e.g. THINK on).
            cache_key = f"{full_prompt}\x00effort={_reasoning.claude_effort}/{_reasoning.deepseek_thinking}"
        cached_response = self.llm_cache.get(cache_key)

        llm_start = time.perf_counter()

        if cached_response and not force_cloud:
            response = cached_response
            timings['llm'] = time.perf_counter() - llm_start
            self.console.print(f"[dim cyan]⚡ Cache LLM hit[/dim cyan]")
        elif _use_tool_loop:
            # ── ANTHROPIC TOOL USE LOOP ──────────────────────────────────────────
            # Claude decide autónomamente si necesita buscar en internet
            self.console.print("[dim cyan]🔧 Anthropic tool use loop activo[/dim cyan]")
            _filler_task = None
            if _should_emit_filler(intent, complexity):
                _filler_task = await ctx.emit_status_deferred(
                    "thinking", _pick_thinking(intent, complexity), after_ms=3500
                )
            _ws_instance = _WebSearch()

            async def _tool_exec(name: str, inp: dict) -> str:
                if name == "search":
                    return await _execute_search_tool(
                        query=inp.get("query", ""),
                        max_results=inp.get("max_results", 5),
                        searcher=_ws_instance
                    )
                if name == "diagnose_zephyro":
                    return await self._exec_diagnose_tool(inp)
                if name == "introspect_code":
                    return await self._exec_introspect_tool(inp)
                if name == "read_project_file":
                    return await self._exec_read_file_tool(inp)
                return f"Tool '{name}' no disponible"

            # Composición dinámica de tools según intent del usuario:
            # - SEARCH_TOOL: solo si la query necesita datos actualizados (gate via _tool_loop_needs_search)
            # - Cuando hay intent diagnóstica/introspectiva O complejidad alta:
            #   agregar DIAGNOSE/INTROSPECT/READ_FILE para que Claude pueda leer
            #   los propios logs/código del proyecto autónomamente.
            from modules.integrations.api_registry import _tool_loop_needs_search as _tlns
            if _search_gate_task is not None:
                try:
                    _needs_search_tool = await _search_gate_task
                except Exception:
                    _needs_search_tool = True  # same conservative default as the gate
            else:
                _needs_search_tool = await _tlns(user_input)
            if not _needs_search_tool:
                self.console.print("[dim cyan]   🧠 knowledge query — SEARCH_TOOL omitido[/dim cyan]")

            _tools_for_loop = ([SEARCH_TOOL] if _needs_search_tool else [])
            # Gate the Zephyro-internal skill tools: never inject them for a general
            # knowledge question (would hijack the persona — "célula" → "módulo de Zephyro").
            if HAS_SKILL_TOOLS and (_has_diag_intent or complexity == "complex") and not _is_general_knowledge:
                _tools_for_loop.extend([DIAGNOSE_TOOL, INTROSPECT_TOOL, READ_FILE_TOOL])
                self.console.print(
                    f"[dim cyan]   + skill tools enabled (diag_intent={_has_diag_intent}, complexity={complexity})[/dim cyan]"
                )
            elif _is_general_knowledge and (_has_diag_intent or complexity == "complex"):
                self.console.print("[dim cyan]   🧠 general knowledge — skill tools omitidos[/dim cyan]")

            if not _tools_for_loop:
                # No tools needed — answer directly from cloud LLM training, skip tool loop overhead.
                # Call mode: token-stream it so the first sentence is spoken while the rest is
                # still generating (before, audio started only after the full answer).
                if (
                    getattr(ctx, "stream_tts", False)
                    and getattr(ctx, "tts_chunk_callback", None) is not None
                ):
                    response = await self._generate_with_streaming_tts_ws(
                        selected_llm, full_prompt, False, True,
                        preset_to_use or "rapido", ctx.tts_chunk_callback, ctx=ctx,
                        reasoning=_reasoning,
                    )
                else:
                    response = await asyncio.to_thread(
                        selected_llm.generate,
                        full_prompt,
                        force_cloud=True,
                        force_local=False,
                        **_reasoning_kw(_reasoning),
                    )
                if _filler_task and not _filler_task.done():
                    _filler_task.cancel()
            else:
                try:
                    response = await asyncio.to_thread(
                        selected_llm.generate_with_tools,
                        full_prompt,   # system context completo (historial + instrucciones)
                        user_input,    # mensaje actual del usuario (sin preambles de reasoning)
                        _tools_for_loop,
                        _tool_exec,
                        **_reasoning_kw(_reasoning),
                    )
                finally:
                    if _filler_task and not _filler_task.done():
                        _filler_task.cancel()

                # Fallback: si el tool loop agotó iteraciones sin síntesis, caer a search_web_direct
                _TOOL_LOOP_SENTINEL = "No se pudo completar la respuesta con las herramientas disponibles."
                if response == _TOOL_LOOP_SENTINEL:
                    self.console.print("[yellow]⚠ Tool loop agotó iteraciones — fallback a search_web_direct[/yellow]")
                    try:
                        _synth_llm_fb = self.llm
                        _synth_fn_fb = lambda p: _synth_llm_fb.generate(
                            p, force_cloud=True, force_local=False
                        )
                        web_fallback = await search_web_direct(user_input, llm_fn=_synth_fn_fb)
                        if web_fallback and web_fallback != f"No encontré resultados en internet para: '{user_input}'":
                            response = sanitize_response(web_fallback)
                            self._last_web_search_topic = user_input[:150]
                            self.console.print(f"[bold green]✓ Tool loop fallback OK:[/bold green] {response[:100]}...")
                    except Exception as _fb_err:
                        self.console.print(f"[yellow]⚠ Tool loop fallback también falló: {_fb_err}[/yellow]")

        else:
            # Streaming habilitado si: hay TTS activo Y hay preset (local), O hay TTS activo Y cloud
            # disponible (generate_stream soporta ambos). No streamear si el tool loop va a manejar
            # la búsqueda web (el tool loop consume la respuesta completa internamente).
            use_streaming = speak and preset_to_use is not None

            # Predicate WS streaming-TTS: activo SOLO cuando el cliente optó con
            # stream_tts=True y hay callback inyectado. Sin gate de longitud: sin la
            # retención de una oración, streamear una respuesta corta cuesta las mismas
            # síntesis que el fallback por oración de chat.py y arranca antes.
            # Es INDEPENDIENTE de use_streaming (Piper path).
            _ws_stream = bool(
                getattr(ctx, "stream_tts", False)
                and getattr(ctx, "tts_chunk_callback", None) is not None
                and preset_to_use is not None
            )

            # Concept/knowledge questions deserve the capable cloud model when
            # online — local 3b with num_predict caps gives terse, weaker answers
            # than Claude. Respect an explicit force_local. Table-worthy
            # queries also go cloud: local models emit malformed tables.
            # `_force_cloud_gen` is computed before build_prompt (needs cloud_model).

            # Solo agendar filler si tiene chance real de tardar (complejidad alta o
            # actividad lenta). Con streaming TTS la voz arranca con el primer chunk,
            # así que el filler no aporta. after_ms alto: solo dispara en esperas
            # genuinamente largas, donde el filler tiene tiempo de completarse.
            _filler_task = None
            if _should_emit_filler(intent, complexity) and not use_streaming and not _ws_stream:
                _filler_task = await ctx.emit_status_deferred(
                    "thinking", _pick_thinking(intent, complexity), after_ms=3500
                )

            try:
                if _ws_stream:
                    response = await self._generate_with_streaming_tts_ws(
                        selected_llm, full_prompt, force_local, _force_cloud_gen,
                        preset_to_use, ctx.tts_chunk_callback, ctx=ctx,
                        reasoning=_reasoning,
                    )
                elif use_streaming:
                    response = await self._generate_with_streaming_tts(
                        selected_llm, full_prompt, force_local, _force_cloud_gen, preset_to_use,
                        reasoning=_reasoning,
                    )
                else:
                    response = await asyncio.to_thread(
                        selected_llm.generate,
                        full_prompt,
                        force_local=force_local,
                        force_cloud=_force_cloud_gen,
                        preset=preset_to_use,
                        **_reasoning_kw(_reasoning),
                    )
            finally:
                if _filler_task and not _filler_task.done():
                    _filler_task.cancel()

            # Guardar en cache si no es una respuesta de error
            if response and len(response) > 10 and not force_cloud:
                self.llm_cache.set(cache_key, response)

        timings['llm'] = time.perf_counter() - llm_start
        
        # Actualizar system_info con modelo real (use cached last_* attrs, no HTTP calls)
        if self.user_role == "admin":
            system_info = {
                "current_model": selected_llm.last_model_used or "unknown",
                "current_provider": selected_llm.last_provider_used or "unknown",
                "mode_used": "cloud" if force_cloud else ("local" if force_local else "router"),
                "user_role": self.user_role,
                "session_id": self.session_id
            }
            self.context.last_system_info = system_info
        
        # Validar y limpiar respuesta
        response = self._validate_response(response, template, user_input)

        # Normalizar tono (elimina emojis y aperturas entusiastas sin importar el LLM)
        response = sanitize_response(response)

        # ── Charts: ```chart {json}``` → ![...](data:image/png;base64,...) ──
        # Mismo chokepoint que el guard de web-denial: cubre tool loop, web
        # fall-through y LLM crudo. CPU-bound → to_thread. El path streaming
        # TTS ya suprime el fence del audio (make_stream_tts_cleaner).
        response_for_history = None
        if response and "```chart" in response:
            response_for_history = response
            try:
                from modules.charts.renderer import replace_chart_blocks
                response = await asyncio.to_thread(replace_chart_blocks, response)
            except Exception as _chart_err:
                self.console.print(f"[dim]charts: render falló: {_chart_err}[/dim]")

        # ── Guard anti-negación de búsqueda web (chokepoint único) ──────────
        # Los 3 paths de generación (tool loop, fall-through del bloque web,
        # LLM crudo) pueden producir "No puedo buscar en internet" aunque el
        # sistema SÍ tiene búsqueda web. En vez de solo DETECTAR el mismatch
        # post-hoc (lo que llenaba BUGS_ENCONTRADOS — bugs #7/#9/#10, mismo
        # input "buscar noticias de hoy"), lo PREVENIMOS: si la respuesta niega
        # la capacidad, intentamos la búsqueda real una vez; si no hay
        # resultado, devolvemos un mensaje honesto que NUNCA niega la capacidad.
        if HAS_API_REGISTRY and response:
            _resp_low = response.lower()
            _web_denial = (
                "no puedo buscar" in _resp_low
                or "no tengo acceso a internet" in _resp_low
                or "no tengo acceso a la web" in _resp_low
                or "no puedo acceder a internet" in _resp_low
            )
            if _web_denial:
                self.console.print(
                    "[yellow]⚠ Respuesta negó búsqueda web — recuperando vía search_web_direct[/yellow]"
                )
                _recovered = None
                try:
                    _synth_llm_g = self.llm
                    _synth_fn_g = lambda p: _synth_llm_g.generate(
                        p, force_cloud=True, force_local=False
                    )
                    _recovered = await search_web_direct(user_input, llm_fn=_synth_fn_g)
                except Exception as _g_err:
                    self.console.print(f"[yellow]⚠ Recuperación de búsqueda falló: {_g_err}[/yellow]")
                _no_results = f"No encontré resultados en internet para: '{user_input}'"
                if _recovered and _recovered.strip() and _recovered.strip() != _no_results:
                    response = sanitize_response(_recovered)
                    self._last_web_search_topic = user_input[:150]
                else:
                    # Nunca emitir una negación de capacidad: mensaje honesto.
                    response = (
                        "No pude obtener resultados de la búsqueda web en este momento. "
                        "Probá de nuevo en un momento o reformulá la consulta."
                    )

        # ← NUEVO: POST-PROCESS — Detectar capability_mismatch y otros issues
        total_elapsed = time.perf_counter() - total_start
        bug_report = self.self_state.post_process(
            user_input=user_input,
            response=response,
            intent=intent,
            elapsed_time=total_elapsed
        )
        
        if bug_report:
            self.console.print(
                f"[bold red]🐛 Bug detectado: {bug_report['bug_type']}[/bold red] "
                f"[dim](severity: {bug_report['severity']})[/dim]"
            )
            if bug_report['bug_type'] == 'capability_mismatch':
                details = bug_report.get('details', {})
                self.console.print(
                    f"[yellow]   → LLM dijo: \"{details.get('claimed', '?')}\"[/yellow]\n"
                    f"[green]   → Realidad: \"{details.get('reality', '?')}\"[/green]"
                )
        
        # ← NUEVO: Self State tracking para chat/information
        self.self_state.record_action(
            intent=intent, user_input=user_input,
            response=response, elapsed_time=total_elapsed,
            status="mismatch" if bug_report else "ok",
            details={"model": predicted_model, "provider": predicted_provider,
                     "complexity": complexity, "template": template}
        )
        
        self.console.print(f"[bold yellow]Assistant:[/bold yellow] {response}")
        
        # Solo hablar si no se usó streaming
        use_streaming = speak and not force_cloud and preset_to_use
        if speak and not use_streaming:
            response = self._clean_text_for_tts(response)
            force_piper = self.tts_mode == "piper"
            await self.tts.speak(response, force_local=force_piper)
        
        skip_save = False
        if not self.specialty_mode:
            skip_save = fast_path or template in ["greeting", "farewell", "thanks"]
        
        self.context.add("user", user_input, skip_save=skip_save)
        self.context.add(
            "assistant",
            response_for_history if response_for_history is not None else response,
            skip_save=skip_save,
        )

        # ── Knowledge Graph: procesar mensaje del usuario ─────────────
        if self.knowledge_graph and not skip_save:
            try:
                new_entities = self.knowledge_graph.process_message(user_input, role="user")
                if new_entities > 0:
                    self.console.print(f"[dim]🕸 KG: +{new_entities} entidades[/dim]")
            except Exception:
                pass

        # ── Conversation Summarizer: disparar si toca ─────────────────
        if self.summarizer and not skip_save:
            try:
                # ContextManager.history es lista de (role, content) tuples
                raw_history = getattr(self.context, 'history', [])
                messages = [{"role": r, "content": c} for r, c in raw_history]
                if messages:
                    summary = self.summarizer.on_message(messages)
                    if summary:
                        self.console.print(f"[dim cyan]📝 Resumen generado ({len(summary)} chars)[/dim cyan]")
            except Exception:
                pass

        # ── Workspace: guardar contexto de la sesión ──────────────────
        if self.workspace and self.workspace.get_active_workspace() and not skip_save:
            try:
                ctx_snippet = f"{user_input[:80]} → {response[:120]}"
                self.workspace.save_session_context(ctx_snippet)
            except Exception:
                pass

        # Sistema de Historial Inteligente (non-blocking — runs after response is returned)
        if self._effective_user_role(ctx) == "admin" and self.history and self.importance_classifier:
            _role = self._effective_user_role(ctx)
            asyncio.get_event_loop().call_soon(
                lambda role=_role: asyncio.ensure_future(asyncio.to_thread(
                    self._save_to_history,
                    user_input=user_input,
                    assistant_response=response,
                    intent=intent or "chat",
                    complexity=complexity,
                    elapsed_time=time.perf_counter() - total_start,
                    mode_used="cloud" if force_cloud else "auto",
                    skip_save=skip_save,
                    user_role=role
                ))
            )
        
        total_time = time.perf_counter() - total_start
        self._print_timing_stats(timings, total_time, intent or "chat", complexity, ctx=ctx)
        self.context.record_latency(intent or "chat", complexity, total_time)

        # ── Aprendizaje adaptativo: registrar interacción ─────────────
        if self.feedback_system and response:
            try:
                interaction_id = self.feedback_system.record_interaction(
                    user_input=user_input,
                    response=response,
                    intent=intent or "chat",
                    importance_score=0.5,   # Score base; se actualiza con feedback
                    session_id=self.session_id or "",
                )
                self._last_interaction_id = interaction_id
                self._last_user_input_for_feedback = user_input
            except Exception:
                pass

        # ── Reflection periódica (cada 30 interacciones) — fire-and-forget ──
        if self.reflection_engine and self.user_profile:
            count = self.self_state.session_stats.get("interactions_count", 0)
            needs_early = getattr(self.self_state, "_needs_reflection", False)
            due_count = count - self.reflection_engine._last_reflected_at >= self.reflection_engine.reflect_every
            if needs_early or due_count:
                self.self_state._needs_reflection = False
                async def _do_reflect() -> None:
                    try:
                        recent: list = []
                        if self.history:
                            raw = self.history.get_recent_conversations(n=30)
                            recent = [{"user_input": r.get("user_input", ""), "response": r.get("assistant_response", "")} for r in raw]
                        profile_summary = self.user_profile.get_summary()
                        bug_sum = None
                        if self.self_state.bug_reports:
                            summary = self.self_state.get_bug_reports_summary()
                            bug_sum = f"Total: {summary['total']}, Por tipo: {summary['by_type']}"
                        await asyncio.to_thread(
                            self.reflection_engine.maybe_reflect,
                            count,
                            recent,
                            profile_summary,
                            lambda p: self.llm.generate(p, force_cloud=True),
                            bug_sum,
                        )
                    except Exception:
                        pass
                asyncio.create_task(_do_reflect())

        # Publicar para que VerificationAgent detecte respuestas que describieron
        # acciones en vez de ejecutarlas (solo cloud fast-lane, sin sobre-publicar)
        if analysis.get("_source") == "cloud_fast_lane":
            try:
                from core.event_bus import get_bus, AUTOMATION_EXECUTED
                get_bus().publish(AUTOMATION_EXECUTED, {
                    "intent": "cloud_chat",
                    "user_input": user_input,
                    "response": response,
                    "_source": "cloud_fast_lane",
                })
            except Exception:
                pass

        # ── Bonus: alimentar el few-shot selector con interacciones exitosas ─
        # (su pool estaba siempre vacío porque add_example no tenía callers).
        if self.few_shot_selector and response and not bug_report and not skip_save:
            try:
                self.few_shot_selector.add_example(user_input, response, intent or "chat")
            except Exception:
                pass

        # ── Loop Hermes: grabar el envelope del turno de chat ─────────────
        self._finalize_envelope(
            user_input, ctx, intent=intent or "chat",
            steps=[StepRecord(
                skill_name="llm_chat",
                args={"complexity": complexity, "template": template},
                result_summary=(response or "")[:200],
                duration_ms=int(timings.get("llm", 0) * 1000),
                provider=selected_llm.last_provider_used,
                model=selected_llm.last_model_used)],
            response=response,
            success=not bool(bug_report))
        return response

    # =========================================================================
    # NUEVOS COMANDOS: /reflect, /selfstate, /bugs
    # =========================================================================
    
    def _handle_capabilities_query(self) -> str:
        """Retorna capacidades directamente desde self_state — sin LLM, sin latencia."""
        ss = self.self_state
        caps = ss.capabilities

        # Skills activas
        skill_lines = []
        try:
            for skill in self.skill_registry.get_enabled():
                skill_lines.append(f"  - {skill.name}: {skill.description}")
        except Exception:
            pass

        lines = [
            f"Zephyro v{ss.identity['version']} — Capacidades actuales",
            "",
            "Sistema:",
        ]
        for key, cap in caps.items():
            lines.append(f"  - {cap['description']}")
        lines.append("")
        if skill_lines:
            lines.append("Skills activas:")
            lines.extend(skill_lines)
            lines.append("")
        lines.append(f"Sesion: {ss.session_stats['interactions_count']} interacciones, "
                     f"{ss.session_stats['avg_response_time']:.1f}s promedio.")
        return "\n".join(lines)

    def _handle_limitations_query(self) -> str:
        """Retorna limitaciones directamente desde self_state — sin LLM."""
        lims = self.self_state.known_limitations
        lines = ["Zephyro — Limitaciones conocidas:", ""]
        for lim in lims:
            lines.append(f"  - {lim}")
        return "\n".join(lines)

    async def _handle_reflect_command(self, arg: str = "") -> str:
        """Ejecuta reflexión con tier escalation basado en severidad de bugs."""
        from core.llm_helpers import call_deepseek_flash, call_sonnet, call_deepseek_pro, call_opus_thinking

        self.console.print("[bold magenta]🧠 Iniciando reflexión...[/bold magenta]")

        reflection_prompt = self.self_state.build_reflection_prompt()
        if not reflection_prompt:
            return "No hay suficientes datos para reflexionar. Necesito al menos 5 interacciones o algún bug report."

        bugs = self.self_state.bug_reports
        stats = self.self_state.session_stats
        interactions = max(1, stats["interactions_count"])
        error_rate = (stats["errors_count"] / interactions) * 100
        mismatches = stats.get("capability_mismatches", 0)

        try:
            # Tier 3: /reflect 1 o /reflect 2 explícitos (respuesta a pregunta anterior)
            if arg.strip() == "1":
                reflection = await call_deepseek_pro(reflection_prompt)
                if not reflection:
                    reflection = await asyncio.to_thread(self.llm.generate, reflection_prompt, force_cloud=True)
            elif arg.strip() == "2":
                reflection = await call_opus_thinking(reflection_prompt)
                if not reflection:
                    reflection = await asyncio.to_thread(self.llm.generate, reflection_prompt, force_cloud=True)
            elif len(bugs) >= 10 or mismatches >= 5:
                # Tier 3: preguntar al usuario cuál modelo usar
                return (
                    "🔧 La reflexión requiere análisis profundo.\n"
                    f"Bugs: {len(bugs)}, Mismatches: {mismatches}, Error rate: {error_rate:.1f}%\n\n"
                    "¿Qué modelo quieres usar?\n"
                    "[1] DeepSeek v4 Pro — mejor para contexto extenso (muchos bugs, logs)\n"
                    "[2] Claude Opus 4.7 con extended thinking — mejor para patrones complejos\n\n"
                    "Responde `/reflect 1` o `/reflect 2`"
                )
            elif len(bugs) >= 4 or error_rate >= 10:
                # Tier 2: Sonnet 4.6
                reflection = await call_sonnet(reflection_prompt)
                if not reflection:
                    reflection = await asyncio.to_thread(self.llm.generate, reflection_prompt, force_cloud=True)
            else:
                # Tier 1: DeepSeek v4 flash
                reflection = await call_deepseek_flash(reflection_prompt)
                if not reflection:
                    reflection = await asyncio.to_thread(self.llm.generate, reflection_prompt, force_cloud=True)

            if not reflection or len(reflection.strip()) < 20:
                reflection = await asyncio.to_thread(
                    self.llm.generate, reflection_prompt, True, False, "balanceado"
                )
        except Exception as exc:
            import logging as _log
            _log.getLogger("zephyro.orchestrator").error("Error en reflexión: %s", exc)
            return f"Error al generar reflexión: {exc}"

        self.console.print(f"\n[bold magenta]═══ REFLEXIÓN DE ZEPHYRO ═══[/bold magenta]")
        self.console.print(reflection)
        self.console.print(f"[bold magenta]═══════════════════════════[/bold magenta]\n")

        summary = (
            f"\n📊 Stats: {stats['interactions_count']} interacciones, "
            f"{stats['errors_count']} errores, "
            f"{mismatches} mismatches, "
            f"tiempo promedio: {stats['avg_response_time']:.2f}s"
        )
        return f"🧠 **Reflexión completada**\n\n{reflection}{summary}"
    
    def _handle_selfstate_command(self) -> str:
        """Muestra el estado interno de Zephyro."""
        state = self.self_state.export_state()
        
        output_parts = [
            "🧠 **Estado Interno de Zephyro**\n",
            f"**Identidad:** {state['identity']['name']} v{state['identity']['version']}",
            f"**Arquitectura:** {state['identity']['architecture']}",
            f"**Plataforma:** {state['identity']['platform']}\n",
            f"**Sesión:**",
            f"  • Interacciones: {state['session_stats']['interactions_count']}",
            f"  • Errores: {state['session_stats']['errors_count']}",
            f"  • Mismatches: {state['session_stats']['capability_mismatches']}",
            f"  • Tiempo promedio: {state['session_stats']['avg_response_time']:.2f}s",
            f"  • Distribución: {state['session_stats']['intents_histogram']}",
        ]
        
        # Últimas acciones
        if state['recent_actions']:
            output_parts.append(f"\n**Últimas acciones:**")
            for action in state['recent_actions'][-5:]:
                output_parts.append(
                    f"  [{action['intent']}] {action['user_input_preview'][:50]} "
                    f"→ {action['status']} ({action['elapsed_time']}s)"
                )
        
        # Bug reports summary
        bugs = state['bug_reports_summary']
        if bugs['total'] > 0:
            output_parts.append(f"\n**Bug Reports:** {bugs['total']} total")
            output_parts.append(f"  Por tipo: {bugs['by_type']}")
            output_parts.append(f"  Por severidad: {bugs['by_severity']}")
        
        response = "\n".join(output_parts)
        self.console.print(response)
        return response
    
    def _handle_bugs_command(self) -> str:
        """Muestra los bug reports acumulados."""
        summary = self.self_state.get_bug_reports_summary()
        
        if summary['total'] == 0:
            return "✅ No hay bug reports registrados. El sistema funciona correctamente."
        
        output_parts = [
            f"🐛 **Bug Reports: {summary['total']} total**\n",
            f"**Por tipo:** {summary['by_type']}",
            f"**Por severidad:** {summary['by_severity']}\n",
            "**Últimos 5 reportes:**"
        ]
        
        for bug in summary['recent']:
            output_parts.append(
                f"\n  [{bug['severity'].upper()}] {bug['bug_type']}\n"
                f"  📅 {bug['timestamp']}\n"
                f"  📝 Input: {bug['user_input'][:80]}\n"
                f"  💬 Response: {bug['response_snippet'][:80]}\n"
                f"  🔍 Details: {bug.get('details', {})}"
            )
        
        response = "\n".join(output_parts)
        self.console.print(response)
        return response

    # =========================================================================
    # SPRINT 3: User Profile, Summaries, Workspace
    # =========================================================================

    def _handle_profile_command(self) -> str:
        """Muestra el perfil del usuario."""
        if not self.user_profile:
            return "Módulo UserProfile no disponible."
        profile = self.user_profile.get_full_profile()
        parts = ["## Perfil del usuario"]
        if profile.get("name"):
            parts.append(f"**Nombre:** {profile['name']}")
        if profile.get("interests"):
            topics = ", ".join(f"{i['topic']} ({i['count']})" for i in profile["interests"][:8])
            parts.append(f"**Intereses:** {topics}")
        if profile.get("preferences"):
            prefs = ", ".join(f"{k}={v}" for k, v in list(profile["preferences"].items())[:5])
            parts.append(f"**Preferencias:** {prefs}")
        if profile.get("recent_activities"):
            acts = [a["activity"][:60] for a in profile["recent_activities"][:5]]
            parts.append(f"**Actividad reciente:** " + " | ".join(acts))
        response = "\n".join(parts)
        self.console.print(response)
        return response

    def _handle_summaries_command(self) -> str:
        """Muestra los resúmenes de conversación generados."""
        if not self.summarizer:
            return "Módulo Summarizer no disponible."
        stats = self.summarizer.get_stats()
        summaries = self.summarizer.get_all_summaries()
        parts = [
            f"## Resúmenes de conversación",
            f"Total: {stats['total_summaries']} | Mensajes desde último: {stats['messages_since_last_summary']}/{stats['summarize_every']}"
        ]
        if summaries:
            for s in summaries[-5:]:
                ts = time.strftime("%d/%m %H:%M", time.localtime(s["timestamp"]))
                parts.append(f"\n**[{ts}]** {s['summary']}")
        else:
            parts.append("No hay resúmenes generados todavía.")
        response = "\n".join(parts)
        self.console.print(response)
        return response

    def _handle_workspace_command(self, user_input: str) -> str:
        """Gestiona workspaces: crear, listar, cambiar, añadir archivos, tareas, notas."""
        if not self.workspace:
            return "Módulo SessionWorkspace no disponible."
        parts_input = user_input.strip().split()
        # /workspace  o  /ws
        cmd = parts_input[1].lower() if len(parts_input) > 1 else "info"
        rest = " ".join(parts_input[2:]) if len(parts_input) > 2 else ""

        if cmd in ("list", "lista", "ls"):
            workspaces = self.workspace.list_workspaces()
            if not workspaces:
                return "No hay workspaces creados. Usa `/workspace nuevo NOMBRE`."
            active = self.workspace.get_active_workspace()
            lines = ["## Workspaces"]
            for ws in workspaces:
                marker = " ← activo" if ws["name"] == active else ""
                lines.append(f"- **{ws['name']}**{marker}: {ws['description']}")
            return "\n".join(lines)

        elif cmd in ("nuevo", "new", "crear", "create"):
            if not rest:
                return "Uso: `/workspace nuevo NOMBRE [descripción]`"
            name_parts = rest.split(None, 1)
            name = name_parts[0]
            desc = name_parts[1] if len(name_parts) > 1 else ""
            self.workspace.create_workspace(name, desc)
            self.workspace.switch_workspace(name)
            return f"Workspace '{name}' creado y activado."

        elif cmd in ("usar", "use", "switch", "activar"):
            if not rest:
                return "Uso: `/workspace usar NOMBRE`"
            self.workspace.switch_workspace(rest)
            return f"Workspace activo: {rest}"

        elif cmd in ("info", "estado", "status", "ver"):
            ctx = self.workspace.get_context_for_llm()
            return ctx or "No hay workspace activo. Usa `/workspace nuevo NOMBRE`."

        elif cmd in ("nota", "note"):
            if not rest:
                return "Uso: `/workspace nota TEXTO`"
            self.workspace.add_note(rest)
            return f"Nota añadida al workspace '{self.workspace.get_active_workspace()}'."

        elif cmd in ("tarea", "task", "todo"):
            if not rest:
                return "Uso: `/workspace tarea DESCRIPCIÓN`"
            task_id = self.workspace.add_task(rest)
            return f"Tarea #{task_id} añadida: {rest}"

        elif cmd in ("tareas", "tasks", "todos"):
            tasks = self.workspace.get_tasks(status="pending")
            if not tasks:
                return "No hay tareas pendientes en el workspace activo."
            lines = ["## Tareas pendientes"]
            for t in tasks:
                lines.append(f"- #{t['id']}: {t['task']}")
            return "\n".join(lines)

        elif cmd in ("hecho", "done", "completar", "complete"):
            if not rest or not rest.isdigit():
                return "Uso: `/workspace done ID_TAREA`"
            self.workspace.complete_task(int(rest))
            return f"Tarea #{rest} marcada como completada."

        elif cmd in ("archivo", "file", "abrir"):
            if not rest:
                return "Uso: `/workspace archivo RUTA`"
            self.workspace.add_file(rest)
            return f"Archivo '{rest}' añadido al workspace."

        else:
            active = self.workspace.get_active_workspace()
            return (
                f"Workspace activo: {active or 'ninguno'}\n"
                f"Comandos: list, nuevo, usar, info, nota, tarea, tareas, done, archivo"
            )

    # =========================================================================
    # CODE ANALYSIS (GitNexus Integration)
    # =========================================================================

    # ─────────────────────────────────────────────────────────────────────
    # Skill tools (LLM-invoked autonomously via tool-use loop)
    # ─────────────────────────────────────────────────────────────────────

    async def _exec_diagnose_tool(self, inp: dict) -> str:
        """Tool dispatcher: invoca log_reader skill para diagnosticar logs."""
        try:
            question = (inp.get("question") or "").strip()
            if not question:
                return "ERROR: parámetro 'question' vacío"
            lookback = int(inp.get("lookback_seconds", 300))
            log_file = inp.get("log_file", "backend.log")

            skill_registry = getattr(self, "skill_registry", None)
            if skill_registry is None:
                return "ERROR: skill_registry no disponible"

            log_reader = skill_registry.get_skill("log_reader") if hasattr(skill_registry, "get_skill") else None
            if log_reader is None:
                return "ERROR: skill log_reader no encontrada/habilitada"

            # log_reader._diagnose() existe — invocar directamente
            if not hasattr(log_reader, "_diagnose"):
                return "ERROR: log_reader sin método _diagnose"

            # Construir invocación: _diagnose tiene firma variable según implementación
            try:
                result = await log_reader._diagnose(
                    question=question,
                    lookback_seconds=lookback,
                    log_file=log_file,
                )
            except TypeError:
                # Fallback: signatures sin keyword args
                result = await log_reader._diagnose(question)

            # Truncar para no inundar el contexto del LLM
            if isinstance(result, str):
                return result[:4000]
            return str(result)[:4000]
        except Exception as exc:
            return f"ERROR ejecutando diagnose: {exc}"

    async def _exec_introspect_tool(self, inp: dict) -> str:
        """Tool dispatcher: invoca introspection skill."""
        try:
            query = (inp.get("query") or "").strip()
            if not query:
                return "ERROR: parámetro 'query' vacío"
            tier = inp.get("tier", "gitnexus")

            skill_registry = getattr(self, "skill_registry", None)
            if skill_registry is None:
                return "ERROR: skill_registry no disponible"

            introspect = skill_registry.get_skill("introspection") if hasattr(skill_registry, "get_skill") else None
            if introspect is None:
                return "ERROR: skill introspection no encontrada/habilitada"

            # Intentar handle() con un ChannelMessage sintético
            try:
                from channels.base import ChannelMessage
                msg = ChannelMessage(
                    text=query,
                    user_id="tool-use",
                    channel_type="tool",
                    metadata={"tier": tier},
                )
                if hasattr(introspect, "handle"):
                    result = await introspect.handle(msg, orchestrator=self)
                elif hasattr(introspect, "_analyze"):
                    result = await introspect._analyze(query, tier=tier)
                else:
                    return "ERROR: introspection skill sin método handle/_analyze"
            except Exception as exc:
                return f"ERROR en introspect: {exc}"

            if isinstance(result, str):
                return result[:4000]
            return str(result)[:4000]
        except Exception as exc:
            return f"ERROR ejecutando introspect: {exc}"

    async def _exec_read_file_tool(self, inp: dict) -> str:
        """Tool dispatcher: lee un archivo del proyecto con whitelist."""
        try:
            path = (inp.get("path") or "").strip()
            if not path:
                return "ERROR: parámetro 'path' vacío"
            line_start = max(1, int(inp.get("line_start", 1)))
            line_count = max(1, min(int(inp.get("line_count", 200)), 500))

            if not HAS_SKILL_TOOLS:
                return "ERROR: skill_tools module no disponible"

            allowed, reason = _is_skill_path_allowed(path)
            if not allowed:
                return f"ERROR: path no permitido — {reason}"

            from pathlib import Path as _Path
            p = _Path(path.replace("\\", "/"))
            if not p.is_absolute():
                p = _SKILL_PROJECT_ROOT / p
            p = p.resolve()

            if not p.exists():
                return f"ERROR: archivo no existe: {path}"
            if not p.is_file():
                return f"ERROR: no es un archivo: {path}"

            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception as exc:
                return f"ERROR leyendo archivo: {exc}"

            total = len(lines)
            start_idx = line_start - 1
            end_idx = min(start_idx + line_count, total)
            selected = lines[start_idx:end_idx]
            content = "".join(selected).rstrip()

            header = f"# {path} — líneas {line_start}-{start_idx + len(selected)} de {total}\n"
            return (header + content)[:8000]
        except Exception as exc:
            return f"ERROR ejecutando read_file: {exc}"

    async def _ensure_gitnexus(self) -> bool:
        """Inicializa GitNexus bridge lazy (solo al primer uso)."""
        if self.gitnexus and self.gitnexus.is_available:
            return True

        if self._gitnexus_init_attempted and not self.gitnexus:
            return False  # Ya falló antes

        if not HAS_GITNEXUS_MODULE:
            self.console.print("[yellow]⚠ GitNexus module no disponible[/yellow]")
            self._gitnexus_init_attempted = True
            return False

        self._gitnexus_init_attempted = True
        self.console.print("[dim]🔗 Inicializando GitNexus bridge...[/dim]")

        try:
            self.gitnexus = GitNexusBridge()
            success = await self.gitnexus.start()
            if success:
                self.console.print("[green]✓ GitNexus bridge activo[/green]")
                return True
            else:
                self.console.print("[yellow]⚠ GitNexus bridge no pudo iniciarse (Node.js/gitnexus instalado?)[/yellow]")
                self.gitnexus = None
                return False
        except Exception as e:
            self.console.print(f"[yellow]⚠ GitNexus init error: {e}[/yellow]")
            self.gitnexus = None
            return False

    async def _handle_diagnostic(
        self, user_input: str, analysis: dict, speak: bool,
        total_start: float, timings: dict
    ) -> str:
        """Intent 'diagnostic': lanza DiagnosticAgent en background y responde al usuario.

        El approval gate + chunks llegan por WebSocket (diag:* messages) al frontend
        (modal dashboard o strip del call mode). Reutiliza la instancia de
        WebSocketApprovalGate/DiagnosticStore creadas en el lifespan.
        """
        # Follow-up: distinguir "ya tienes el diagnóstico?" (retrieve) vs
        # "haz un diagnóstico" (start new). Solo el primero consume el estado pendiente.
        is_followup = bool(_RE_DIAGNOSTIC_FOLLOWUP.search(user_input))
        if is_followup and self._pending_diagnostic:
            task = self._pending_diagnostic_task
            if task and not task.done():
                return "Sigo analizando en segundo plano — todavía no tengo los resultados. Te aviso cuando termine."
            # Task done → consume & clear
            summary = self._pending_diagnostic_summary or "El diagnóstico ya terminó."
            self._pending_diagnostic = False
            self._pending_diagnostic_summary = ""
            return summary

        gate = getattr(self, "diagnostic_gate", None)
        store = getattr(self, "diagnostic_store", None)
        if gate is None or store is None:
            response = (
                "El puente de diagnóstico no está inicializado en este backend. "
                "Revisa los logs del lifespan de FastAPI."
            )
            self.console.print(f"[bold yellow]Assistant:[/bold yellow] {response}")
            return response

        try:
            from subagents.diagnostic_agent import DiagnosticAgent, _extract_spoken_summary
        except Exception as exc:
            response = f"No pude cargar el DiagnosticAgent: {exc}"
            self.console.print(f"[bold red]{response}[/bold red]")
            return response

        log_file = getattr(self, "diagnostic_log_file", None)
        # Prefer the WS-registered user_id (the actual connected client) over the
        # orchestrator's internal session_id (random hex, never registered in WS).
        _ws_connected = (self._ws_registry.get_connected_users() if getattr(self, "_ws_registry", None) else [])
        user_id = _ws_connected[0] if _ws_connected else (getattr(self, "session_id", None) or "anonymous")
        # Register this diagnostic run in the gate store so push() knows the user_id.
        request_id = await gate.start(user_id)

        code_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not log_file:
            log_file = os.path.join(os.path.dirname(code_root), "logs", "backend.log")

        # Pre-análisis: GitNexus structural context (gratuito, antes de los tiers LLM)
        gitnexus_context = ""
        try:
            from modules.automation.gitnexus_bridge import GitNexusBridge
            _bridge = GitNexusBridge()
            if await _bridge.start():
                _result = await _bridge.search(query=user_input)
                if _result:
                    gitnexus_context = _bridge.format_for_prompt("search", _result, max_tokens=400)
                try:
                    await _bridge.stop()
                except Exception:
                    pass
        except Exception as _exc:
            self.console.print(f"[dim][diagnostic] GitNexus skip: {_exc}[/dim]")

        # Construir contexto del usuario: turnos recientes + mensaje actual
        _user_context_parts = []
        try:
            if self.history:
                _recent = self.history.get_recent_conversations(limit=3)
                for _turn in _recent[-2:]:
                    _ui = str(_turn.get("user_input", "")).strip()[:120]
                    _ar = str(_turn.get("assistant_response", "")).strip()[:120]
                    if _ui:
                        _user_context_parts.append(f"Usuario: {_ui}")
                    if _ar:
                        _user_context_parts.append(f"Asistente: {_ar}")
        except Exception:
            pass
        _user_context_parts.append(f"Usuario (desencadenó diagnóstico): {user_input}")
        user_context = "\n".join(_user_context_parts)

        agent = DiagnosticAgent(
            llm_manager=self.llm,
            gate=gate,
            log_file=log_file,
            code_root=code_root,
            code_context=gitnexus_context,
            user_context=user_context,
        )

        # Pre-clasificar tier para informarlo al usuario antes de spawnear el task.
        # Si el usuario forzó un tier por voz ("usa tier 1"), lo respetamos.
        _TIER_NAMES = {
            0: "Copilot CLI local",
            1: "DeepSeek flash",
            2: "Claude Sonnet",
            3: "Claude Opus",
        }
        _forced_tier_match = re.search(r'\btier\s*([0-3])\b', user_input, re.IGNORECASE)
        if _forced_tier_match:
            tier_used = int(_forced_tier_match.group(1))
        else:
            tier_used = agent.peek_tier(n_lines=50)

        _ws_reg = getattr(self, "_ws_registry", None)

        async def _run():
            _summary = ""
            try:
                result = await agent.run(chat_id=request_id, n_lines=50)
                _summary = str(result) if result else "Análisis completado."
            except Exception as exc:
                self.console.print(f"[red][diagnostic] Error: {exc}[/red]")
                _summary = f"El diagnóstico falló: {exc}"
            finally:
                # Keep _pending_diagnostic = True so the user's follow-up can
                # retrieve the summary. Flag is cleared only when consumed
                # by the follow-up branch in _handle_diagnostic().
                self._pending_diagnostic_summary = _summary
                if _ws_reg:
                    asyncio.create_task(_ws_reg.broadcast({
                        "type": "diagnostic_running",
                        "status": "done",
                        "summary": _summary,
                        # Versión corta para que el frontend la hable en call mode
                        "spoken_summary": _extract_spoken_summary(_summary),
                    }))

        total_time = time.perf_counter() - total_start
        if tier_used is None:
            response = (
                "Arrancando diagnóstico en segundo plano. "
                "No detecté errores recientes en los logs, pero revisaré igualmente."
            )
        else:
            response = (
                f"Arrancando diagnóstico en segundo plano "
                f"(tier {tier_used}: {_TIER_NAMES.get(tier_used, 'desconocido')}). "
                f"Revisaré los logs y te paso la hipótesis cuando la tenga."
            )

        self._pending_diagnostic = True
        self._pending_diagnostic_summary = response
        self._pending_diagnostic_task = asyncio.create_task(_run())

        if _ws_reg:
            asyncio.create_task(_ws_reg.broadcast({
                "type": "diagnostic_running",
                "status": "started",
                "tier": _TIER_NAMES.get(tier_used, ""),
            }))

        self.console.print(f"[bold yellow]Assistant:[/bold yellow] {response}")
        self.self_state.record_action(
            intent="diagnostic", user_input=user_input,
            response=response, elapsed_time=total_time,
            status="started",
        )
        return response

    async def _handle_product_search(
        self, user_input: str, analysis: dict, speak: bool,
        total_start: float, timings: dict
    ) -> str:
        """Maneja búsqueda de productos en tiendas online (MercadoLibre, eBay, AliExpress)."""
        search_start = time.perf_counter()
        query = analysis.get("query", user_input)

        try:
            from modules.search.tools import execute_product_search
            raw_results = await execute_product_search(query)
        except Exception as exc:
            self.console.print(f"[red]✗ product_search error: {exc}[/red]")
            raw_results = f"Error al buscar productos: {exc}"

        timings["product_search"] = time.perf_counter() - search_start

        # Pass through LLM to produce a natural-language response
        if raw_results and not raw_results.startswith("No se encontraron") and not raw_results.startswith("Error"):
            synth_prompt = (
                f"El usuario pidió: '{user_input}'\n\n"
                f"Se encontraron los siguientes productos en tiendas online:\n\n"
                f"{raw_results}\n\n"
                f"Resume los mejores resultados de forma concisa y útil. "
                f"Destaca el producto más barato por tienda, menciona el precio en su moneda original "
                f"y el equivalente en USD si aplica. Incluye los links. Responde en español."
            )
            llm_start = time.perf_counter()
            try:
                _llm = self.llm
                response = await asyncio.to_thread(
                    lambda: _llm.generate(synth_prompt, force_cloud=True, force_local=False)
                )
                from core.response_sanitizer import sanitize_response
                response = sanitize_response(response)
            except Exception:
                response = raw_results
            timings["llm"] = time.perf_counter() - llm_start
        else:
            response = raw_results or "No encontré productos para esa búsqueda."

        self.console.print(f"[bold green]✓[/bold green] {response[:120]}...")
        self.context.add("user", user_input)
        self.context.add("assistant", response)

        if speak:
            await self.tts.speak(response)

        total_time = time.perf_counter() - total_start
        self._print_timing_stats(timings, total_time, "product_search", "balanced")
        self.context.record_latency("product_search", "balanced", total_time)
        self.self_state.record_action(
            intent="product_search", user_input=user_input,
            response=response, elapsed_time=total_time,
            details={"query": query},
        )
        return response

    async def _handle_screen_query(
        self, user_input: str, speak: bool,
        total_start: float, timings: dict
    ) -> str:
        vision_start = time.perf_counter()

        now = time.time()
        cached_path = getattr(self, "_last_screen_path", None)
        cached_at = getattr(self, "_last_screen_ts", 0.0)

        if cached_path and (now - cached_at) < 30:
            image_path = cached_path
        else:
            image_path = self.vision_capture.capture()
            self._last_screen_path = image_path
            self._last_screen_ts = now

        prompt = (
            f"El usuario pregunta: \"{user_input}\"\n\n"
            "Esta es una captura de su pantalla. Responde de forma concisa y directa "
            "en espanol. Si ves la interfaz de Zephyro (chat, Tauri, JARVIS), "
            "ignorala y concentrate en el contenido del resto de la pantalla.\n\n"
            "Si el usuario pregunta 'que dice la pantalla' o 'que ves', "
            "describe el contenido principal visible. Si pregunta algo especifico, "
            "responde solo sobre eso. Maximo 4 oraciones."
        )
        description = self.vision_analyze.analyze(image_path, prompt)
        timings['vision'] = time.perf_counter() - vision_start

        self.console.print(f"[bold yellow]Assistant:[/bold yellow] {description}")
        if speak:
            await self.tts.speak(description)

        self.context.add("user", user_input)
        self.context.add("assistant", description)

        total_time = time.perf_counter() - total_start
        self._print_timing_stats(timings, total_time, "screen_query", "complex")
        self.context.record_latency("screen_query", "complex", total_time)

        self.self_state.record_action(
            intent="screen_query", user_input=user_input,
            response=description, elapsed_time=total_time
        )

        return description

    async def _handle_code_analysis(
        self, user_input: str, analysis: dict, speak: bool,
        total_start: float, timings: dict
    ) -> str:
        """Maneja intent code_analysis: consulta GitNexus + enriquece con cloud LLM."""
        # 1. Asegurar que GitNexus esté disponible
        gitnexus_ready = await self._ensure_gitnexus()

        if not gitnexus_ready:
            response = (
                "⚠ El sistema de análisis de código (GitNexus) no está disponible.\n"
                "Para habilitarlo:\n"
                "1. Instala Node.js ≥18: `sudo pacman -S nodejs npm`\n"
                "2. Instala GitNexus: `npm install -g gitnexus`\n"
                "3. Indexa un repo: `cd /ruta/al/repo && npx gitnexus analyze`\n"
                "Luego reintenta tu consulta."
            )
            self.console.print(f"[bold yellow]Assistant:[/bold yellow] {response}")
            total_time = time.perf_counter() - total_start
            self.self_state.record_action(
                intent="code_analysis", user_input=user_input,
                response=response, elapsed_time=total_time,
                status="unavailable"
            )
            return response

        # 2. Determinar qué tool usar basándose en la query
        query = analysis.get("query", user_input)
        query_lower = query.lower()

        gitnexus_start = time.perf_counter()
        code_result = None
        tool_used = "search"

        try:
            if any(kw in query_lower for kw in ["impacto", "impact", "blast", "afecta", "rompe"]):
                # Extraer símbolo de la query
                symbol = self._extract_symbol_from_query(query)
                if symbol:
                    code_result = await self.gitnexus.impact(symbol)
                    tool_used = "impact"
                else:
                    code_result = await self.gitnexus.search(query)

            elif any(kw in query_lower for kw in ["cambios", "changes", "diff", "modificado"]):
                code_result = await self.gitnexus.detect_changes()
                tool_used = "detect_changes"

            elif any(kw in query_lower for kw in ["contexto", "context", "quién llama", "quien llama", "quién usa", "quien usa", "dependencias", "imports"]):
                symbol = self._extract_symbol_from_query(query)
                if symbol:
                    code_result = await self.gitnexus.context(symbol)
                    tool_used = "context"
                else:
                    code_result = await self.gitnexus.search(query)

            elif any(kw in query_lower for kw in ["repos", "repositorios", "indexados"]):
                code_result = await self.gitnexus.list_repos()
                tool_used = "list_repos"

            else:
                code_result = await self.gitnexus.search(query)
                tool_used = "search"

        except Exception as e:
            self.console.print(f"[yellow]⚠ GitNexus query error: {e}[/yellow]")
            code_result = {"error": str(e)}

        timings['gitnexus'] = time.perf_counter() - gitnexus_start

        # 3. Formatear resultado para el prompt
        code_context_str = self.gitnexus.format_for_prompt(tool_used, code_result)

        # 4. Generar respuesta con cloud LLM (contexto extenso)
        context_start = time.perf_counter()

        self_state_context = self.self_state.get_relevant_capabilities(user_input)

        full_prompt = self.context.build_prompt(
            user_input,
            skip_memory=True,  # No mezclar memoria vectorial con code context
            specialty_mode="code_reviewer",
            self_state=self.self_state,
            self_state_context=self_state_context,
            code_context=code_context_str,
        )
        timings['context_build'] = time.perf_counter() - context_start

        # Forzar cloud LLM (modelos locales no tienen contexto suficiente)
        llm_start = time.perf_counter()
        response = self.llm.generate(
            full_prompt,
            force_cloud=True,
            preset="balanceado"
        )

        # Fallback a local si cloud falla
        if not response or len(response.strip()) < 10:
            self.console.print("[yellow]Cloud no disponible, usando modelo local para code analysis...[/yellow]")
            response = self.llm.generate(
                full_prompt,
                force_local=True,
                preset="balanceado"
            )
        timings['llm'] = time.perf_counter() - llm_start

        response = self._validate_response(response, None, user_input)

        # 5. Post-process y tracking
        total_time = time.perf_counter() - total_start

        bug_report = self.self_state.post_process(
            user_input=user_input, response=response,
            intent="code_analysis", elapsed_time=total_time
        )

        self.self_state.record_action(
            intent="code_analysis", user_input=user_input,
            response=response, elapsed_time=total_time,
            status="mismatch" if bug_report else "ok",
            details={"tool": tool_used, "has_result": code_result is not None}
        )

        self.console.print(f"[bold yellow]Assistant:[/bold yellow] {response}")

        if speak:
            await self.tts.speak(response)

        self.context.add("user", user_input)
        self.context.add("assistant", response)

        self._print_timing_stats(timings, total_time, "code_analysis", "complex")
        self.context.record_latency("code_analysis", "complex", total_time)

        return response

    def _extract_symbol_from_query(self, query: str) -> Optional[str]:
        """Extrae un nombre de símbolo (función/clase/método) de la query del usuario."""
        import re as _re
        # Patrones comunes: "impacto de handle_input", "contexto de UserService"
        patterns = [
            r'(?:de|del|para|of)\s+[`\'"]*(\w[\w.]*)\b',
            r'(?:función|funcion|clase|método|metodo|variable)\s+[`\'"]*(\w[\w.]*)\b',
            r'[`\'"]+(\w[\w.]+)[`\'"]+',  # Entre comillas/backticks
        ]
        for pattern in patterns:
            match = _re.search(pattern, query, _re.IGNORECASE)
            if match:
                symbol = match.group(1)
                # Filtrar palabras comunes que no son símbolos
                common_words = {"el", "la", "los", "las", "un", "una", "este", "esta",
                               "código", "codigo", "repo", "repositorio", "cambio", "impacto"}
                if symbol.lower() not in common_words and len(symbol) > 2:
                    return symbol
        return None

    async def get_gitnexus_health(self) -> dict:
        """Health check del bridge GitNexus para la API."""
        if not self.gitnexus:
            return {"status": "not_initialized", "available": HAS_GITNEXUS_MODULE}
        return await self.gitnexus.health_check()

    async def trigger_gitnexus_index(self, repo_path: str) -> dict:
        """Lanza indexación de un repositorio (async, puede tardar)."""
        import asyncio as _asyncio
        import shutil

        npx = shutil.which("npx")
        if not npx:
            return {"success": False, "error": "npx no encontrado"}

        try:
            proc = await _asyncio.create_subprocess_exec(
                npx, "gitnexus", "analyze",
                cwd=repo_path,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(
                proc.communicate(), timeout=300  # 5 min max
            )
            return {
                "success": proc.returncode == 0,
                "stdout": stdout.decode("utf-8", errors="replace")[-500:],
                "stderr": stderr.decode("utf-8", errors="replace")[-500:],
                "returncode": proc.returncode,
            }
        except _asyncio.TimeoutError:
            return {"success": False, "error": "Indexación tardó más de 5 minutos"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _prepend_greeting_if_needed(self, user_input: str, response: str) -> str:
        """Si el usuario incluyó un saludo en su mensaje, antepone un saludo personalizado."""
        _greeting_map = {
            "buenas noches": "Buenas noches",
            "buenas tardes": "Buenas tardes",
            "buenos días": "Buenos días",
            "buenos dias": "Buenos días",
            "buenas": "Buenas",
            "hola": "Hola",
        }
        user_low = user_input.lower()
        for gkw, gfmt in _greeting_map.items():
            if gkw in user_low:
                name = self.user_name or "señor"
                # Unir saludo con la respuesta, haciendo minúscula la primera letra de la respuesta
                if response and response[0].isupper():
                    return f"{gfmt} {name}, {response[0].lower()}{response[1:]}"
                return f"{gfmt} {name}, {response}"
        return response

    async def _push_break_if_due(self) -> None:
        """Push break_reminder via WebSocket if study session threshold is reached."""
        import time as _t
        if self._study_start is None or self._study_notified:
            return
        elapsed = _t.time() - self._study_start
        if elapsed < _STUDY_BREAK_SECS:
            return
        self._study_notified = True
        mins = int(elapsed / 60)
        try:
            from core.connection_registry import registry as _ws_reg
            await _ws_reg.broadcast({
                "type": "break_reminder",
                "elapsed_minutes": mins,
                "message": f"Llevas {mins} minutos trabajando seguidos. ¿Tomamos un descanso activo?",
            })
            log.info("[orchestrator] break_reminder enviado tras %d min de sesión de estudio", mins)
        except Exception as exc:
            log.debug("[orchestrator] break_reminder push failed: %s", exc)

    def _select_llm_level(self, fast_path: bool, complexity: str, force_cloud: bool) -> LLMManager:
        """Selecciona el nivel de LLM según fast_path y complejidad"""
        if force_cloud:
            return self.llm  # Usa cloud si está forzado

        # Cloud-first cuando no hay Ollama local: en vez de elegir el modelo
        # local (llm_fast 1b) e intentar inferir → fallar → redirigir a cloud
        # reactivamente, vamos directo a self.llm (que ya redirige a cloud si
        # Ollama está apagado). Evita el overhead y respeta hardware débil.
        if not getattr(self.llm_fast, "ollama_available", False):
            return self.llm

        if fast_path:
            # NIVEL 1: Ultra-rápido para triviales
            return self.llm_fast
        elif complexity == "complex":
            # NIVEL 2/3: Balanceado (con auto-switch a cloud si es necesario)
            return self.llm
        else:
            # NIVEL 2: Balanceado para simple/medium
            return self.llm
    
    def _validate_response(self, response: str, template: Optional[str], user_input: str) -> str:
        """Valida y limpia la respuesta para prevenir alucinaciones"""
        if template == "greeting":
            words = response.split()
            if len(words) > 15:
                response = " ".join(words[:15]) + "."
        
        if template == "math":
            hallucination_keywords = ["libreoffice", "calc", "excel", "software", "programa", "aplicación"]
            if any(kw in response.lower() for kw in hallucination_keywords):
                numbers = re.findall(r'-?\d+\.?\d*', user_input)
                if numbers:
                    try:
                        # SEC-02: no eval() — safe numeric literal parse only
                        joined = "".join(numbers)
                        if re.match(r'^-?\d+(\.\d+)?$', joined):
                            result = float(joined) if '.' in joined else int(joined)
                            response = str(result)
                        else:
                            response = "No puedo calcular eso correctamente."
                    except Exception:
                        response = "No puedo calcular eso correctamente."
        
        # Detectar y limpiar respuestas que repiten info del sistema en vez de responder
        response_lower = response.lower()
        system_info_patterns = [
            "modelo actual:", "modelo actual ", "último modelo usado:",
            "proveedor:", "nivel:", "sistema: zephyro con enrutamiento",
            "=== información del sistema", "========",
            "cloud para consultas complejas",
            "ultra-rápido", "balanceado (para",
            "ollama local", "cloud api",
        ]
        
        # Si la respuesta parece ser solo info del sistema sin contestar la pregunta
        system_info_matches = sum(1 for p in system_info_patterns if p in response_lower)
        if system_info_matches >= 2:
            # La respuesta está parroteando info del sistema
            # Limpiar las líneas que son solo info del sistema
            cleaned_lines = []
            for line in response.split('\n'):
                line_lower = line.lower().strip()
                is_system_line = any(p in line_lower for p in system_info_patterns)
                if not is_system_line and line.strip():
                    cleaned_lines.append(line)
            
            if cleaned_lines:
                response = '\n'.join(cleaned_lines)
            else:
                # Si toda la respuesta era info del sistema, dar respuesta genérica
                response = "No entendí bien tu pregunta, ¿podrías reformularla?"
        
        return response.strip()
    
    def _clean_text_for_tts(self, text: str) -> str:
        """Limpia texto para TTS: normaliza Unicode, elimina markdown, URLs."""
        import re
        import unicodedata
        # Normalizar Unicode: corrige decomposed accents (NFD → NFC)
        # para que Piper pronuncie tildes correctamente
        text = unicodedata.normalize('NFC', text)
        # Remove markdown links
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        # Remove URLs
        text = re.sub(r'https?://\S+', '', text)
        # Remove asterisks used for bold/italic
        text = re.sub(r'\*+', '', text)
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove leading/trailing punctuation that might cause TTS errors
        text = text.strip('.,;:!?')
        return text.strip()
    
    async def _generate_with_streaming_tts_ws(
        self,
        llm: 'LLMManager',
        prompt: str,
        force_local: bool,
        force_cloud: bool,
        preset: str,
        on_chunk: 'Callable[[str, str, int, bool], Awaitable[None]]',
        ctx: Optional[RequestContext] = None,
        reasoning=None,
    ) -> str:
        """
        Genera respuesta con streaming TTS sobre WebSocket.

        Puente entre el generador SÍNCRONO `llm.generate_stream` y el loop asyncio:
        un hilo pump-thread alimenta una `asyncio.Queue` con oraciones ya formadas
        (`iter_stream_chunks`, limpiadas para voz con `make_stream_tts_cleaner`; el
        texto raw se conserva para el display).

        - Cada oración hablable lanza su síntesis APENAS llega (sin retener la anterior
          para marcar is_last): el primer audio sale con la primera oración.
        - Un semáforo limita a `_STREAM_TTS_INFLIGHT` síntesis simultáneas (FIFO, así
          arrancan en orden) y un emisor aparte manda los frames en orden: el consumo
          de oraciones nunca espera a la síntesis. `chunk_index` es denso y monotónico
          sobre los frames emitidos.
        - `on_chunk` se llama siempre con is_last=False: el frame terminal lo emite
          UNA sola vez el dueño del turno (routers/chat.py), haya audio o no.
        - Un fallo TTS por oración se loguea y se omite (no aborta el turno).
        - Con `ctx`, retorna apenas termina la GENERACIÓN: el emisor sigue en
          `ctx.tts_drain` (el WS handler manda el texto y después lo espera).

        Cancellation: `CancelledError` corta el loop, cancela emisor y síntesis en
        vuelo, y frena el pump en el próximo token (`stop`).
        """
        import logging
        from typing import Callable, Awaitable
        from modules.voice.tts_chunking import (
            DEFAULT_FIRST_MIN_WORDS, iter_stream_chunks, split_tts_sentences,
        )
        from core.response_sanitizer import make_stream_tts_cleaner, prepare_for_tts
        # synthesize_tts se usa desde el scope del módulo (importado al tope) para
        # que los tests puedan hacer patch("core.orchestrator.synthesize_tts").

        _log = logging.getLogger(__name__)
        loop = asyncio.get_running_loop()

        _SENTINEL = object()
        full_parts: list = []
        synth_slots = asyncio.Semaphore(_STREAM_TTS_INFLIGHT)
        synth_tasks: list = []
        emit_queue: asyncio.Queue = asyncio.Queue()  # synth tasks in sentence order

        async def _synth(text: str):
            clean = (text or "").strip()
            if not clean:
                return None
            try:
                async with synth_slots:
                    tts_result = await synthesize_tts(clean, placeholder=False)
            except asyncio.CancelledError:
                raise
            except Exception as _chunk_err:
                _log.error("[streaming_tts_ws] Error TTS oración '%s': %s", clean[:40], _chunk_err)
                return None
            if not tts_result.get("success"):
                if not tts_result.get("empty"):
                    _log.warning(
                        "[streaming_tts_ws] TTS falló para '%s': %s",
                        clean[:40], tts_result.get("error", "unknown"),
                    )
                return None
            return tts_result["audio"], clean

        def _enqueue(text: str) -> None:
            task = asyncio.create_task(_synth(text))
            synth_tasks.append(task)
            emit_queue.put_nowait(task)

        async def _emitter() -> None:
            emitted_index = 0
            try:
                while True:
                    task = await emit_queue.get()
                    if task is _SENTINEL:
                        return
                    result = await task
                    if result is None:
                        continue
                    audio_b64, clean = result
                    await on_chunk(audio_b64, clean, emitted_index, False)
                    emitted_index += 1
            finally:
                for _t in synth_tasks:
                    _t.cancel()

        async def _cloud_chunked_fallback() -> str:
            """Último recurso: el stream no produjo nada → generación cloud completa y
            audio por oración (así call mode NO queda mudo)."""
            try:
                cloud_text = await asyncio.to_thread(
                    llm.generate, prompt, force_cloud=True, force_local=False,
                    **_reasoning_kw(reasoning),
                )
            except asyncio.CancelledError:
                raise
            except Exception as _cloud_err:
                _log.error("[streaming_tts_ws] cloud fallback falló: %s", _cloud_err)
                return ""
            cloud_text = (cloud_text or "").strip()
            if not cloud_text:
                return ""
            # prepare_for_tts sobre el texto COMPLETO: strip de Fuentes (DOTALL),
            # tablas, fences y math que no deben llegar al audio.
            for _sentence in split_tts_sentences(prepare_for_tts(cloud_text)) or []:
                _enqueue(_sentence)
            return cloud_text

        # Sin modelo local (Ollama caído) el stream va por cloud: generate_stream con
        # force_cloud token-streamea vía LangChain (Anthropic/DeepSeek/Gemini/OpenAI).
        stream_cloud = force_cloud or not getattr(llm, "ollama_available", True)

        _sentence_queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()

        def _tokens():
            for tok in llm.generate_stream(
                prompt,
                force_cloud=stream_cloud,
                force_local=force_local and not stream_cloud,
                preset=preset,
                **_reasoning_kw(reasoning),
            ):
                if stop.is_set():
                    return
                yield tok

        def _pump():
            t0 = time.perf_counter()
            first = None
            n = 0
            try:
                # Cleaner FRESCO por turno: stateful (fences/math/fuentes abiertos entre
                # chunks). Se encola (raw, cleaned): raw va a full_parts para que el frame
                # {type:"response"} conserve el texto íntegro; cleaned es lo que se habla.
                _cleaner = make_stream_tts_cleaner()
                for sentence in iter_stream_chunks(_tokens(), first_min_words=DEFAULT_FIRST_MIN_WORDS):
                    if stop.is_set():
                        break
                    if first is None:
                        first = time.perf_counter() - t0
                    n += 1
                    loop.call_soon_threadsafe(
                        _sentence_queue.put_nowait, (sentence, _cleaner(sentence))
                    )
            except Exception as _pump_err:
                _log.error("[streaming_tts_ws] Error en pump thread: %s", _pump_err)
            finally:
                _log.info(
                    "[streaming_tts_ws] generation: first_chunk=%.2fs total=%.2fs chunks=%d",
                    first or 0.0, time.perf_counter() - t0, n,
                )
                try:
                    loop.call_soon_threadsafe(_sentence_queue.put_nowait, _SENTINEL)
                except RuntimeError:
                    pass  # loop already closed

        emitter = asyncio.create_task(_emitter())
        handed_off = False
        pump_thread = threading.Thread(target=_pump, daemon=True)
        pump_thread.start()
        get_task = None
        try:
            while True:
                if get_task is None:
                    get_task = asyncio.ensure_future(_sentence_queue.get())
                # Also wake if the emitter dies (on_chunk failed: socket gone / cancelled),
                # so a stalled LLM stream can't hide it — surface it instead of generating
                # the rest of an answer nobody will hear.
                await asyncio.wait({get_task, emitter}, return_when=asyncio.FIRST_COMPLETED)
                if emitter.done() and not get_task.done():
                    emitter.result()  # raises the emitter's error / CancelledError
                    break
                item = get_task.result()
                get_task = None
                if item is _SENTINEL:
                    break
                raw, cleaned = item
                full_parts.append(raw)
                if cleaned:
                    _enqueue(cleaned)

            text = " ".join(full_parts).strip()
            if not full_parts:
                # El stream no produjo nada (Ollama se cayó en runtime o el provider
                # devolvió vacío): cloud + chunked TTS para no quedar mudos.
                _log.warning("[streaming_tts_ws] stream sin salida — fallback a cloud + chunked TTS")
                text = await _cloud_chunked_fallback()

            emit_queue.put_nowait(_SENTINEL)
            if ctx is not None and not emitter.done():
                ctx.tts_drain = emitter
                handed_off = True
            else:
                await emitter
            return text
        except asyncio.CancelledError:
            _log.info("[streaming_tts_ws] Cancelado — deteniendo stream de audio.")
            raise
        finally:
            stop.set()
            if get_task is not None and not get_task.done():
                get_task.cancel()
            if not handed_off and not emitter.done():
                emitter.cancel()

    async def _generate_with_streaming_tts(
        self,
        llm: 'LLMManager',
        prompt: str,
        force_local: bool,
        force_cloud: bool,
        preset: str,
        reasoning=None,
    ) -> str:
        """
        Genera respuesta con streaming y pronuncia a medida que se producen chunks.

        Soporta tanto LLMs locales (Ollama) como cloud (Gemini/Anthropic/OpenAI/DeepSeek)
        via llm.generate_stream(). El buffer se vacía al detectar signo de puntuación
        O al acumular >=5 palabras, lo que llegue primero, para reducir la latencia del
        primer audio sin cortar frases a la mitad.
        """
        from core.response_sanitizer import make_stream_tts_cleaner

        _SENTENCE_ENDS = frozenset({'.', '!', '?', '\n'})
        _WORD_FLUSH_THRESHOLD = 5
        _tts_tasks: list[asyncio.Task] = []
        # Igual que el path WS: el audio no debe leer tablas/código/LaTeX.
        # response_text conserva el texto raw completo para el display.
        _tts_cleaner = make_stream_tts_cleaner()

        response_text = ""
        token_buffer = ""

        async def _flush(buf: str) -> None:
            clean = _tts_cleaner(buf.strip()) if buf.strip() else ""
            if clean:
                # Despachar TTS en background para no bloquear la generación de tokens
                task = asyncio.create_task(self.tts.speak_streaming(clean))
                _tts_tasks.append(task)

        for token in llm.generate_stream(
            prompt,
            force_cloud=force_cloud,
            force_local=force_local,
            preset=preset,
            **_reasoning_kw(reasoning),
        ):
            response_text += token
            token_buffer += token

            if any(c in token for c in _SENTENCE_ENDS):
                await _flush(token_buffer)
                token_buffer = ""
            elif len(token_buffer.split()) >= _WORD_FLUSH_THRESHOLD:
                await _flush(token_buffer)
                token_buffer = ""

        await _flush(token_buffer)

        # Esperar a que todo el audio termine de reproducirse
        if _tts_tasks:
            await asyncio.gather(*_tts_tasks, return_exceptions=True)

        return response_text.strip()

    def _print_timing_stats(self, timings: dict, total_time: float, intent: str, complexity: str, ctx=None):
        """Imprime estadísticas de tiempos con Rich.

        Si se pasa ctx (RequestContext), también copia las métricas a ctx.timings
        para que el handler del WebSocket pueda exponerlas al frontend (HUD del
        call mode lee llm_ms desde aquí en lugar de inventar números).
        """
        if ctx is not None:
            try:
                ctx.timings = dict(timings)
                ctx.timings['total'] = total_time
                ctx.timings['intent'] = intent
                ctx.timings['complexity'] = complexity
            except Exception:
                pass
        table = Table(title=f"⏱️  Tiempos de Respuesta ({intent} - {complexity})")
        
        table.add_column("Componente", style="cyan", no_wrap=True)
        table.add_column("Tiempo", justify="right", style="green")
        table.add_column("% Total", justify="right", style="yellow")
        
        for component, duration in timings.items():
            percentage = (duration / total_time) * 100 if total_time > 0 else 0
            table.add_row(
                component.replace('_', ' ').title(),
                f"{duration:.3f}s",
                f"{percentage:.1f}%"
            )
        
        table.add_row("─" * 20, "─" * 10, "─" * 10, style="dim")
        table.add_row("TOTAL", f"{total_time:.3f}s", "100%", style="bold")
        
        avg_time = self.context.get_average_latency(intent, complexity)
        if avg_time:
            diff = total_time - avg_time
            diff_str = f"+{diff:.3f}s" if diff > 0 else f"{diff:.3f}s"
            color = "red" if diff > 0.5 else "green" if diff < -0.5 else "yellow"
            table.add_row("Promedio histórico", f"{avg_time:.3f}s", f"[{color}]{diff_str}[/{color}]", style="dim")
        
        self.console.print(table)

    async def handle_input_stream(
        self,
        user_input: str,
        ctx: Optional[RequestContext] = None,
        force_mode: Optional[str] = None,
        speak: bool = False,
    ):
        """Async generator that yields response chunks for WebSocket streaming."""
        if ctx is None:
            ctx = RequestContext(
                user_role=self.user_role,
                user_name=self.user_name,
                authenticated=self.authenticated,
                session_id=self.session_id,
                force_mode=force_mode,
                speak=speak,
            )

        full_prompt = self.context.build_prompt(user_input)
        try:
            if hasattr(self.llm, 'stream_async'):
                async for chunk in self.llm.stream_async(full_prompt):
                    if chunk:
                        yield chunk
            else:
                # Fallback: synchronous stream yielded chunk by chunk
                for chunk in self.llm.stream(full_prompt):
                    if chunk:
                        yield chunk
        except Exception:
            # Fallback to non-streaming
            response = await self.handle_input(user_input, ctx=ctx, force_mode=force_mode)
            yield response
    
    def _get_stt(self) -> Optional[STT]:
        """Carga Whisper STT la primera vez que se necesita (~150 MB)."""
        if not self._stt_initialized:
            self._stt_initialized = True
            try:
                self.stt = STT()
                self.stt_available = True
                self.console.print("[green]✓[/green] STT (Whisper) cargado")
            except Exception as e:
                self.stt_available = False
                self.console.print(f"[yellow]STT deshabilitado: {e}[/yellow]")
        return self.stt

    async def warm_stt(self) -> None:
        """Pre-carga el STT en background al startup para evitar la latencia
        de cold-load (~150-200ms) en el primer turno del call mode.

        Lo corremos en un thread porque STT() es síncrono y bloquea ~150ms
        cargando los pesos. Si Whisper no está instalado, _get_stt ya hace
        fallback silencioso a stt_available=False.
        """
        if self._stt_initialized:
            return
        try:
            await asyncio.to_thread(self._get_stt)
        except Exception as e:
            self.console.print(f"[yellow]warm_stt falló: {e}[/yellow]")

    async def listen_and_respond(self, speak: bool = True, force_mode: str = None):
        """Escucha por micrófono y responde"""
        if not self.stt_available:
            self.console.print("[red]El reconocimiento de voz no está disponible.[/red]")
            return
            
        # 1. Escuchar
        user_input = self._get_stt().listen(mode="auto")
        
        if not user_input or len(user_input.strip()) < 2:
            return # No se detectó voz o muy corto
            
        # 2. Procesar
        return await self.handle_input(user_input, speak=speak, force_mode=force_mode)
    
    def calibrate_mic(self):
        """Calibra el micrófono"""
        if self.stt_available and self._get_stt():
            self.stt.calibrate()
        else:
            self.console.print("[red]STT no disponible para calibrar.[/red]")

    def print_memory_stats(self):
        """Muestra estadísticas de la memoria persistente"""
        if not self.context.use_vector_memory or not self.context.vector_store:
            self.console.print("[yellow]Memoria vectorial no activa.[/yellow]")
            return
            
        store = self.context.vector_store
        
        # Detectar tipo de DB
        is_pg = hasattr(store, 'table_name') # PGVectorStore tiene table_name
        
        if is_pg:
            count = store.count()
            backend = "PostgreSQL (pgvector)"
            details = f"Tabla: {store.table_name}"
        else:
            count = len(getattr(store, 'documents', []))
            backend = "Local (JSON/Numpy)"
            details = f"Ruta: {getattr(store, 'persist_path', 'N/A')}"

        self.console.print(f"\n[bold green]Estadísticas de Memoria:[/bold green]")
        self.console.print(f"  • Backend: [cyan]{backend}[/cyan]")
        self.console.print(f"  • Items almacenados: [cyan]{count}[/cyan]")
        self.console.print(f"  • {details}")
        
        if not is_pg and count > 0:
            self.console.print("\n[bold]Últimas 3 memorias:[/bold]")
            last_docs = store.documents[-3:]
            last_metas = store.metadatas[-3:]
            for i, (doc, meta) in enumerate(zip(last_docs, last_metas)):
                role = meta.get('role', 'unknown')
                preview = doc[:100] + "..." if len(doc) > 100 else doc
                self.console.print(f"  {i+1}. [{role}] {preview}")
        
        self.console.print()
    
    def remove_last_memories(self, n: int = 2):
        """Elimina las últimas N memorias guardadas"""
        if not self.context.use_vector_memory or not self.context.vector_store:
            self.console.print("[yellow]Memoria vectorial no activa.[/yellow]")
            return
        
        store = self.context.vector_store
        if hasattr(store, 'remove_last'):
            store.remove_last(n)
        else:
            self.console.print("[yellow]Esta operación solo está disponible para memoria local.[/yellow]")
    
    def clear_all_memories(self):
        """Limpia toda la memoria vectorial"""
        if not self.context.use_vector_memory or not self.context.vector_store:
            self.console.print("[yellow]Memoria vectorial no activa.[/yellow]")
            return
        
        store = self.context.vector_store
        if hasattr(store, 'clear_all'):
            store.clear_all()
        else:
            self.console.print("[yellow]Esta operación solo está disponible para memoria local.[/yellow]")
    
    def _save_to_history(
        self,
        user_input: str,
        assistant_response: str,
        intent: str,
        complexity: str,
        elapsed_time: float,
        mode_used: str,
        skip_save: bool,
        user_role: Optional[str] = None
    ):
        """Guardar conversación en historial con clasificación de importancia"""
        try:
            # Clasificar importancia
            importance_level, importance_score, reasoning = self.importance_classifier.classify(
                user_input=user_input,
                assistant_response=assistant_response,
                intent=intent,
                complexity=complexity,
                response_length=len(assistant_response)
            )
            
            # Si el usuario pidió recordar, forzar a importante
            if self.force_remember_next:
                importance_level = 'critical'
                importance_score = 1.0
                reasoning = "Marcado manualmente con !recordar"
                self.force_remember_next = False
            
            # Determinar si guardar en memoria vectorial
            should_save_to_memory = (
                self.importance_classifier.should_save_to_memory(importance_level, importance_score)
                and not skip_save
            )
            
            # Guardar conversación en historial
            conversation_id = self.history.add_conversation(
                user_input=user_input,
                assistant_response=assistant_response,
                importance_level=importance_level,
                importance_score=importance_score,
                importance_reasoning=reasoning,
                saved_to_memory=should_save_to_memory,
                user_role=user_role or self.user_role,
                user_name=self.user_name,
                session_id=self.session_id,
                intent=intent,
                complexity=complexity,
                elapsed_time=elapsed_time,
                mode_used=mode_used
            )
            
            # Si es importante, forzar guardar en memoria vectorial
            if should_save_to_memory and not skip_save:
                # Ya está guardado por context.add, solo marcamos
                self.history.mark_as_memorized(conversation_id)
                self.console.print(f"[dim]💾 Conversación guardada en memoria (ID: {conversation_id}, {importance_level})[/dim]")
            else:
                self.console.print(f"[dim]📝 Conversación registrada en historial (ID: {conversation_id}, {importance_level})[/dim]")
                
        except Exception as e:
            self.console.print(f"[yellow]⚠️  Error al guardar historial: {e}[/yellow]")
    
    def get_conversation_history(self, limit: int = 20):
        """Obtener historial de conversaciones recientes"""
        if not self.history:
            return []
        return self.history.get_recent_conversations(limit=limit, user_role=self.user_role)
    
    def mark_conversation_important(self, conversation_id: int):
        """Marcar conversación como importante manualmente"""
        if not self.history:
            self.console.print("[yellow]Historial no disponible[/yellow]")
            return
        
        self.history.mark_as_important(conversation_id)
        self.console.print(f"[green]✓ Conversación {conversation_id} marcada como importante[/green]")

    # =========================================================================
    # NUEVOS HANDLERS: Knowledge Graph, Reminders, FileOps, GitOps
    # =========================================================================

    # _handle_knowledge_graph_command, _handle_reminder_*, _handle_file_*,
    # _handle_git_command → migrados a skills/builtin/

    def _get_device_hint(self, ctx) -> str:
        """Genera hint de dispositivo para el prompt del LLM."""
        from core.device_context import DeviceCapabilities
        channel_type = getattr(ctx, "channel_type", "websocket") if ctx else "websocket"
        caps = DeviceCapabilities.from_channel(channel_type)
        # Solo inyectar hint si no es desktop websocket (el caso default)
        if channel_type == "websocket":
            return ""
        return caps.get_prompt_hint()

    def _on_reminder_due(self, reminder) -> None:
        """Callback cuando un recordatorio vence."""
        self.console.print(
            f"\n[bold yellow]⏰ RECORDATORIO #{reminder.id}:[/bold yellow] {reminder.message}"
        )
        # TTS del recordatorio (no-blocking)
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.tts.speak(f"Recordatorio: {reminder.message}"))
        except Exception:
            pass

    def get_cache_stats(self) -> str:
        """Retorna estadísticas del LLM Cache."""
        stats = self.llm_cache.stats
        return (
            f"**LLM Cache Stats**\n"
            f"• Hits: {stats['hits']} | Misses: {stats['misses']}\n"
            f"• Hit rate: {stats['hit_rate_pct']}%\n"
            f"• Entradas activas: {stats['active_entries']}/{stats['max_size']}\n"
            f"• Evictions: {stats['evictions']}"
        )

    def set_envelope_store(self, store) -> None:
        self._envelope_store = store
