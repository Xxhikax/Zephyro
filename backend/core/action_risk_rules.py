"""
Clasificador determinístico de riesgo para acciones de automatización.

READ        → navegar, buscar, reproducir, scroll, captura: se ejecuta libre.
REVERSIBLE  → tipear en un buscador, click genérico, cerrar una app común.
HIGH        → submit/pago/compra/envío/borrado, credenciales, matar procesos
              críticos, actualizar el sistema: SIEMPRE requiere confirmación.

Espejo de core/command_classifier.py para el camino browser/app. Función
pura, sin side effects. Ninguna capa probabilística (LLM, Laya) puede bajar
un HIGH de acá: sólo puede agregar confirmaciones, nunca quitarlas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

# Fuente única de los procesos protegidos: vive en el ejecutor, que es el
# único que ve el nombre real del proceso que va a matar. Acá se usa para
# clasificar el TARGET que escribió el usuario — una aproximación, y de ahí
# que `kill_process` vuelva a chequear la lista de su lado.
from core.platform import PROTECTED_PROCESSES as _PROTECTED_PROCESSES


class ActionRisk(str, Enum):
    READ = "read"
    REVERSIBLE = "reversible"
    HIGH = "high"


_ORDER = {ActionRisk.READ: 0, ActionRisk.REVERSIBLE: 1, ActionRisk.HIGH: 2}


@dataclass(frozen=True)
class RiskVerdict:
    level: ActionRisk
    reason: str = ""

    @property
    def needs_confirmation(self) -> bool:
        return self.level is ActionRisk.HIGH


# ── Botones/links cuyo click tiene efecto externo o irreversible ─────────
_IRREVERSIBLE_CLICK_RE = re.compile(
    r"\b(?:"
    r"compr(?:ar|a|á)|buy|checkout|pag(?:ar|a|á|o)|pay|payment|"
    r"transfer(?:ir|encia)?|donar|donate|suscrib\w*|subscribe|"
    r"enviar|env[ií]a|send|submit|publicar|publish|post|tweet|twittear|"
    r"confirmar|confirm|aceptar|accept|reservar|book|"
    r"eliminar|elimin[aá]|borrar|borr[aá]|delete|remove|vaciar|"
    r"cancelar\s+(?:suscripci[oó]n|cuenta|pedido)|unsubscribe|"
    r"iniciar\s+sesi[oó]n|log\s*in|sign\s*in|autorizar|authorize|allow"
    r")\b",
    re.IGNORECASE,
)

# ── Campos donde tipear expone credenciales o datos de pago ──────────────
_SENSITIVE_FIELD_RE = re.compile(
    r"password|passwd|contrase[nñ]a|clave|pin\b|otp|2fa|token|"
    r"card|tarjeta|cvv|cvc|cbu|cvu|iban|cc-number|security.?code",
    re.IGNORECASE,
)

_MIN_SAFE_KILL_TARGET = 3

# ── Apps que sostienen trabajo sin guardar ───────────────────────────────
# `close_app` termina en `core.platform.kill_process`, que hace
# `proc.kill()` — TerminateProcess, sin WM_CLOSE y sin diálogo de
# recuperación. Cerrar una app ES reversible; el documento NO lo es, y esa
# distinción es la que faltaba: medido el 2026-10-01, "cerrá word" salía
# REVERSIBLE y se ejecutaba sin preguntar.
_UNSAVED_WORK_APPS = frozenset({
    # Office
    "word", "winword", "excel", "powerpoint", "powerpnt", "outlook",
    "onenote", "access", "publisher", "visio",
    # Navegadores: formularios a medio llenar, drafts, uploads en curso
    "chrome", "msedge", "edge", "firefox", "brave", "opera", "vivaldi",
    "chromium", "safari",
    # Editores e IDEs
    # `wordpad` se anuncia en NATIVE_APPS y faltaba acá, así que "cerrá
    # wordpad" salía REVERSIBLE con un documento abierto (Codex, 2026-10-02).
    "notepad", "wordpad", "notepad++", "sublime_text", "sublime", "vim", "nvim",
    "emacs", "idea", "idea64", "pycharm", "pycharm64", "webstorm",
    "rider", "clion", "goland", "phpstorm", "rubymine", "datagrip",
    "studio64", "eclipse", "netbeans", "atom", "obsidian", "typora",
    # Diseño y modelado
    "photoshop", "illustrator", "indesign", "figma", "blender", "gimp",
    "inkscape", "krita", "audacity", "davinci", "resolve", "premiere",
    # PDF y lectores con anotaciones
    "acrobat", "acrord32", "sumatrapdf", "foxitreader",
})

# Universo completo de automation_intent que emiten el router, el extractor
# LLM y el orchestrator (verificado por grep el 2026-10-01). Es cerrado: lo
# que NO esté acá es una forma que nadie sabe clasificar, y pedir
# confirmación es la respuesta correcta a eso.
_KNOWN_INTENTS = frozenset({
    "NAVIGATE", "PLAYBACK_CONTROL", "PLAY_MEDIA", "SEARCH",
    "BROWSE_AND_EXTRACT", "SEQUENCE", "MULTI_ACTION", "INTERACT", "OPEN_APP",
})

# Intents que solo leen o abren contenido: no requieren confirmación.
# OPEN_APP y PLAYBACK_CONTROL quedan AFUERA a propósito — tienen efecto
# sobre el escritorio del usuario (ver `_classify_desktop_effect`).
_READ_INTENTS = frozenset({
    "NAVIGATE", "PLAY_MEDIA", "SEARCH", "BROWSE_AND_EXTRACT",
})

_READ_INTERACT = frozenset({"scroll", "screenshot", "extract_text", "get_text"})
_CLICK_ACTIONS = frozenset({"click", "find_by_text"})


def _max(a: RiskVerdict, b: RiskVerdict) -> RiskVerdict:
    return b if _ORDER[b.level] > _ORDER[a.level] else a


def _classify_click(target: str) -> RiskVerdict:
    if _IRREVERSIBLE_CLICK_RE.search(target):
        return RiskVerdict(ActionRisk.HIGH, f"click en «{target}» puede tener efecto irreversible")
    return RiskVerdict(ActionRisk.REVERSIBLE, "click genérico")


def _classify_type(selector: str, text: str) -> RiskVerdict:
    if _SENSITIVE_FIELD_RE.search(selector) or _SENSITIVE_FIELD_RE.search(text):
        return RiskVerdict(ActionRisk.HIGH, "tipeo en un campo de credenciales o pago")
    return RiskVerdict(ActionRisk.REVERSIBLE, "tipeo de texto")


def _split_type_target(target: str) -> tuple[str, str]:
    """Mismo contrato que WebAutomationPlanner: 'texto en selector'."""
    if " en " in target:
        text, selector = target.split(" en ", 1)
        return selector, text
    return "active", target


def _mentions(name: str, vocabulary: frozenset[str]) -> bool:
    """True si `name` contiene alguna entrada de `vocabulary` como palabra.

    Por palabra, no por substring: «password» no menciona a «word», y
    «monitoreo» no menciona a «monitor». El match por substring es
    exactamente lo que hace que un target genérico alcance más de lo que el
    usuario nombró.
    """
    tokens = set(re.split(r"[^a-z0-9+]+", name))
    tokens.discard("")
    return bool(tokens & vocabulary)


def _could_reach(name: str, vocabulary: frozenset[str]) -> bool:
    """¿Puede un target ALCANZAR algo de `vocabulary` en el ejecutor?

    `kill_process` matchea por SUBSTRING sobre el nombre real del proceso, así
    que el gate tiene que preguntarse lo mismo, en la misma dirección. Antes
    comparaba solo por token y por eso subestimaba: `close "pad"` salía
    REVERSIBLE mientras el ejecutor alcanzaba `notepad.exe` Y `wordpad.exe`
    (Codex, 2026-10-02) — el gate y el ejecutor otra vez en universos
    distintos.

    La dirección importa y es UNA sola: el ejecutor hace
    `target in nombre_del_proceso`, así que la pregunta es si el target es
    substring de algún nombre conocido. Al revés sobre-estima — "passwordmanager"
    contiene "word" pero ningún proceso se llama así, y pedir confirmación para
    cerrarlo sería ruido.

    Los nombres de proceso específicos (`winword`, `powerpnt`, `acrord32`,
    `idea64`) ya están en el vocabulario, así que un target más preciso que el
    alias lo cubre `_mentions` por token.

    Los targets de menos de `_MIN_SAFE_KILL_TARGET` caracteres ya son HIGH por
    ambigüedad, así que acá el substring no se dispara con una o dos letras.
    """
    return any(name in entry for entry in vocabulary)


def _classify_close(target: str) -> RiskVerdict:
    name = target.strip().lower().removesuffix(".exe")
    if len(name) < _MIN_SAFE_KILL_TARGET:
        return RiskVerdict(ActionRisk.HIGH, f"cerrar «{target}» es demasiado ambiguo")
    if _mentions(name, _PROTECTED_PROCESSES) or _could_reach(name, _PROTECTED_PROCESSES):
        return RiskVerdict(ActionRisk.HIGH, f"cerrar «{target}» puede cerrar el sistema o a JARVIS")
    if _mentions(name, _UNSAVED_WORK_APPS) or _could_reach(name, _UNSAVED_WORK_APPS):
        return RiskVerdict(
            ActionRisk.HIGH,
            f"cerrar «{target}» puede perder trabajo sin guardar",
        )
    return RiskVerdict(ActionRisk.REVERSIBLE, "cerrar app")


def _classify_desktop_effect(automation_intent: str, analysis: Mapping[str, Any]) -> RiskVerdict:
    """Acciones que tocan el escritorio o el reproductor del usuario.

    No son READ: abrir una app le mueve ventanas, y PLAYBACK_CONTROL con
    `close` llega a `stop_music`, que navega la pestaña activa a
    `about:blank`. No piden confirmación (serían insoportables), pero
    quedan registradas como lo que son.
    """
    if automation_intent == "PLAYBACK_CONTROL":
        if str(analysis.get("control_action") or "") == "close":
            return RiskVerdict(ActionRisk.REVERSIBLE, "cerrar el reproductor activo")
        return RiskVerdict(ActionRisk.REVERSIBLE, "control del reproductor activo")
    return RiskVerdict(ActionRisk.REVERSIBLE, "abrir una app en el escritorio")


def classify_action(analysis: Mapping[str, Any] | None) -> RiskVerdict:
    """Clasifica un analysis de automatización (formato router/extractor)."""
    if not isinstance(analysis, Mapping):
        return RiskVerdict(ActionRisk.READ)

    automation_intent = analysis.get("automation_intent")

    # Compuestos: el riesgo es el del paso más riesgoso. Los items de
    # MULTI_ACTION se ejecutan con intent="automation" forzado (orchestrator).
    if automation_intent in ("SEQUENCE", "MULTI_ACTION"):
        items = analysis.get("steps") or analysis.get("actions") or []
        verdict = RiskVerdict(ActionRisk.READ)
        for item in items:
            if isinstance(item, Mapping) and automation_intent == "MULTI_ACTION":
                item = {"intent": "automation", **item}
            verdict = _max(verdict, classify_action(item))
        return verdict

    target = str(analysis.get("target") or "")
    intent = analysis.get("intent")
    action = str(analysis.get("action") or "").replace("browser_", "")

    # ── close / update: SIEMPRE, sin importar el automation_intent ───────
    # Antes esto vivía detrás de `if automation_intent not in
    # _DISPATCHED_INTENTS`, con el argumento de que esos intents se
    # despachan por su propio branch en AppController. Es falso para
    # NAVIGATE: `_execute_inner` (app_controller.py:103-125) hace el
    # `return` DENTRO de `if url`, así que cuando `_resolve_navigate_url`
    # devuelve None (app nativa, no URL) cae al despacho por intent/action
    # y llega a `close_app` (app_controller.py:281-284).
    #
    # El router siempre emite un automation_intent, así que la condición
    # vieja hacía que `_classify_close`, `_PROTECTED_PROCESSES` y la regla
    # `update → HIGH` fueran INALCANZABLES desde el camino regex. Medido el
    # 2026-10-01: `close word` + NAVIGATE salía READ. Los tests no lo vieron
    # porque construyen el analysis a mano, sin automation_intent.
    if intent == "automation":
        if action == "close":
            return _classify_close(target)
        if action == "update":
            return RiskVerdict(ActionRisk.HIGH, "actualizar el sistema")

    if automation_intent == "INTERACT":
        # El extractor LLM usa interact_*; el router regex usa action/target.
        interact_action = str(analysis.get("interact_action") or action or "")
        interact_target = str(analysis.get("interact_target") or target)
        if interact_action in _READ_INTERACT:
            return RiskVerdict(ActionRisk.READ)
        if interact_action == "type":
            text = analysis.get("interact_text")
            if text is None:
                selector, text = _split_type_target(interact_target)
            else:
                selector = interact_target
            return _classify_type(selector, str(text))
        # click, find_by_text y cualquier interacción no reconocida.
        return _classify_click(interact_target)

    if intent in ("search", "web_search"):
        if action in _CLICK_ACTIONS:
            return _classify_click(target)
        if action == "type":
            selector, text = _split_type_target(target)
            return _classify_type(selector, text)

    if automation_intent in _READ_INTENTS:
        return RiskVerdict(ActionRisk.READ)

    if automation_intent in ("OPEN_APP", "PLAYBACK_CONTROL"):
        return _classify_desktop_effect(automation_intent, analysis)

    # ── Default: falla CERRADO ───────────────────────────────────────────
    # Un automation_intent que no está en el universo conocido es una forma
    # que ninguna regla sabe leer. Antes caía a READ y se ejecutaba libre, o
    # sea que agregar un intent nuevo lo hacía nacer sin guardrail.
    if automation_intent and automation_intent not in _KNOWN_INTENTS:
        return RiskVerdict(
            ActionRisk.HIGH,
            f"«{automation_intent}» no es una acción que las reglas de riesgo "
            f"sepan clasificar",
        )
    return RiskVerdict(ActionRisk.REVERSIBLE, "acción sin regla específica")
