"""
Reasoning policy: pick how hard the cloud model should think for one user turn.

Pure and cheap (regex only, no LLM, well under 1 ms). Two steps:
  1. classify the turn into a coarse *kind* (chat, quant, code, ...) from the text
     plus optional router hints;
  2. map the kind to a level with KIND_LEVEL, a table calibrated with
     tests/bench_reasoning_effort.py (quality vs time-to-first-token per level).

The level is provider-neutral; LLMManager translates it:
  - Claude (Sonnet 5.5 cannot disable thinking): output_config.effort from
    CLAUDE_EFFORT_BY_LEVEL. "high" is also the API default and is not sent, so a
    None or high decision keeps today's request.
  - DeepSeek: thinking stays off unless DEEPSEEK_THINKING_BY_LEVEL maps the level
    to a reasoning_effort.

Errors are asymmetric: a hard turn answered at "low" can be WRONG, an easy turn at
"high" only costs ~1 s. When a rule is unsure it must lean up, never down.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Optional

from core.verbs import TABLE_WORTHY_PATTERN

Level = Literal["low", "medium", "high"]

# Kind → level. Edit this table (not the regexes) when the bench says a kind
# keeps its quality at a lower level.
KIND_LEVEL: dict[str, Level] = {
    "forced": "high",
    "chat": "low",
    "explain_deep": "medium",
    "comparison": "medium",
    "planning": "medium",
    "code_explain": "medium",
    "complex_hint": "medium",
    "arith": "medium",
    "quant": "high",
    "premise": "high",
    "calendar": "high",
    "logic": "high",
    "code": "high",
    "planning_constrained": "high",
    "design": "high",
}

_RANK = {"low": 0, "medium": 1, "high": 2}

# Calibrated with bench_reasoning_effort.py on the dev set (2026-10-26, 2 reps):
# Sonnet effort low and medium have the SAME time-to-first-token (0.75 s vs 0.73 s;
# high 1.37 s, 2.9 s on code, 4.3 s on planning), but the blind judge rated low worse
# than high on 3/5 explanations (medium 1/5). Low buys nothing, so Claude never
# goes below medium.
CLAUDE_EFFORT_BY_LEVEL: dict[str, str] = {
    "low": "medium",
    "medium": "medium",
    "high": "high",
}

# DeepSeek thinking (reasoning_effort) per level; None = thinking off (today's default).
# Thinking fixed the verifiable misses of thinking-off (39/42 → 42/42: weekday and
# business-day code) at +0.7 s TTFT, with no clear win on chat/explanations.
# reasoning_effort low matched high on every verifiable case and starts sooner.
DEEPSEEK_THINKING_BY_LEVEL: dict[str, Optional[str]] = {
    "low": None,
    "medium": None,
    "high": "low",
}


@dataclass(frozen=True)
class ReasoningDecision:
    level: Level
    kind: str
    reason: str

    @property
    def claude_effort(self) -> str:
        return CLAUDE_EFFORT_BY_LEVEL[self.level]

    @property
    def deepseek_thinking(self) -> Optional[str]:
        return DEEPSEEK_THINKING_BY_LEVEL[self.level]


def _fold(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace.

    Voice transcripts often come without accents, and the bounded `.{0,n}` gaps in
    the patterns below must not stop at a newline.
    """
    decomposed = unicodedata.normalize("NFD", text.lower())
    folded = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", folded).strip()


# Language names end in symbols (c#, c++): \b fails there, so use look-arounds.
_LANGS = r"python|javascript|typescript|java|c\+\+|c#|rust|golang|kotlin|php|sql|bash|powershell|regex"
_RE_LANG = re.compile(rf"(?<!\w)(?:{_LANGS})(?!\w)")
# Unambiguous code artifacts. "clase", "metodo", "programa", "test" also mean a
# yoga class or a study method: they only count next to a programming term.
_CODE_ARTIFACT = (
    r"funcion|script|algoritmo|expresion regular|query|consulta sql|endpoint|codigo|"
    r"test unitario|pruebas unitarias"
)
_CODE_ARTIFACT_WEAK = r"clase|metodo|programa|tests?|implementacion"
_RE_CODE_CONTEXT = re.compile(
    rf"(?<!\w)(?:{_LANGS})(?!\w)|\b(codigo|programacion|software|api|backend|frontend|"
    r"lista|array|string|diccionario|objeto|variable|devuelva|retorne)\b"
)
_CODE_VERB = (
    # No bare "program": "para empezar a programar, ¿JS o Python?" is a comparison.
    # No "hace" after "que": "qué hace este código" asks for an explanation.
    r"escrib|(?<!que )hace(me)?|arma(me)?|implement|codific|arregl|correg|debug|"
    r"refactor|optimiz|complet|genera(me)?|crea(me)?|desarroll|pasa(me)? a|necesito"
)
_RE_CODE_WRITE = re.compile(
    rf"\b({_CODE_VERB})\w*\b.{{0,60}}\b({_CODE_ARTIFACT})\b"
    rf"|\b({_CODE_ARTIFACT})\b.{{0,40}}\b(que|para)\b.{{0,40}}\b(devuelva|calcule|reciba|cuente|ordene|valide)"
    rf"|(?<!\w)(?:{_LANGS})(?!\w).{{0,40}}\b(que|para)\b.{{0,40}}\b(devuelva|calcule|reciba|cuente|ordene|valide)"
)
_RE_CODE_WRITE_WEAK = re.compile(rf"\b({_CODE_VERB})\w*\b.{{0,60}}\b({_CODE_ARTIFACT_WEAK})\b")
# snake_case identifier called with parens: fusionar_intervalos(xs)
_RE_CODE_IDENT = re.compile(r"\b[a-z]+_[a-z0-9_]+\s*\(")
_RE_CODE_BLOCK = re.compile(
    r"```|\bdef \w+\(|\bfunction \w*\(|=>\s*\{|\bconsole\.log\(|\bimport \w+|\{ *return\b"
)
_RE_CODE_FIX = re.compile(
    r"\b(arregl|correg|debug|falla|bug|error|no (anda|funciona|compila)|rompe|traceback|exception)"
)
_RE_CODE_ASK = re.compile(r"\b(que hace|explica|explicame|revisa|revisame)\w*")

_MONTHS = r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
_RE_CALENDAR = re.compile(
    rf"\bque dia( de la semana)?\b.{{0,60}}(\b\d{{1,2}} de ({_MONTHS})\b|\bdentro de\b|\bsemanas?\b|\bmeses\b)"
    rf"|\bcuant[oa]s (dias( habiles)?|semanas|meses|anos)\b.{{0,40}}"
    rf"\b(hay|faltan|pasan|quedan|van|entre|desde|hasta)\b"
    rf"|\b(es|fue|sera|era) bisiesto\b"
)

# Cardinals as spoken ("sesenta minutos"). "un/una" are left out: every sentence has one.
_NUMBER_WORDS = (
    r"dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|"
    r"dieci\w+|veinte|veinti\w+|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|"
    r"cien|ciento|\w+cientos|mil|millon(es)?|medio|media|mitad|doble|triple|tercio|cuarto|docena"
)
_RE_NUMBER = re.compile(rf"\d+(?:[.,:]\d+)*\s*%?|\b({_NUMBER_WORDS})\b")
_RE_QUANT_ASK = re.compile(
    r"\b(cuant[oa]s?|a que hora|que hora|calcul|porcentaje|promedio|probabilidad|chances|"
    r"velocidad|tarda|demora|rinde|alcanza|valia|costaba|resolve|resolveme)"
)
# Traps that make a problem hard by themselves, with a single number in sight.
_RE_QUANT_TRAP = re.compile(
    r"\b(divisibles?|multiplos?|primos?|interes compuesto|se llena|se vacia|"
    r"sube .{0,20} baja|aumenta .{0,20} (baja|disminuye)|descuento .{0,30} descuento|"
    r"juntas?|a la vez)\b"
)
# Plain "N op N": "cuánto es 2 más 2".
_RE_ARITH = re.compile(
    r"^\W*(cuanto (es|da)|calcula(me)?)?\W*[\d.,]+ *(mas|menos|por|dividido|x|\+|-|\*|/) *[\d.,]+\W*$"
)

_RE_LOGIC = re.compile(
    r"\b(acertijo|adivinanza|enigma|paradoja|truco|logica)\b"
    r"|\bmas (alt|baj|grande|chic|viej|joven|rapid|lent)\w* que\b.{0,80}"
    r"\bmas (alt|baj|grande|chic|viej|joven|rapid|lent)\w* que\b"
    r"|\b(todas?|todos?) (las|los) \w+ (estan|son) (mal|falsas?|falsos?)\b"
    r"|\b(siempre miente|siempre dice la verdad|caballeros? y (bribones|mentirosos))\b"
    r"|\b(pas|adelant)\w* al (que va )?(segundo|ultimo|primero)\b"
    r"|\b(segund[oa]|tercer[oa]?|penultim[oa]) mas (alt|baj|grande|chic|viej|joven)"
)
# "Why did X win/get/discover Y for Z?" smuggles in a claim that may be false.
_RE_PREMISE = re.compile(
    r"\bpor que\b.{0,60}\b(recibio|gano|ganaron|invento|descubrio|creo|escribio|fundo)\b.{0,60}\bpor\b"
)

_RE_PLAN = re.compile(
    r"\b(plan|planific|organiz|cronograma|rutina|agenda|itinerario|horario|turnos|guardias|"
    r"asigna|reparti|distribui)\w*"
)
# Constraint groups: several hits of one group (hoy + lunes) are ONE constraint.
_PLAN_CONSTRAINT_GROUPS = (
    # No bare "semana": "3 días por semana" is a frequency, not a deadline.
    re.compile(r"\b(lunes|martes|miercoles|jueves|viernes|sabado|domingo|hoy|manana)\b"),
    re.compile(r"\b\d+\s*(h|hs|horas?|minutos?|dias?)\b|\b(horas? libres|por dia)\b"),
    re.compile(r"\b(parcial|examen|final|entrega|deadline|vence|antes de|despues de)\w*"),
    re.compile(r"\b(prioridad|urgente|me falta|voy bien|voy mas o menos|no puedo|prefiero)\w*"),
    re.compile(r"\b(presupuesto|disponibilidad|descansos?|vacaciones|respetando|sin que|restriccion)\w*"),
)
_RE_DESIGN = re.compile(
    r"\b(arquitectura|disen\w+ (un|el|la|mi)|base de datos|esquema de|stack|escalab\w*|microservicio\w*|"
    r"backend|infraestructura)\b"
)
_RE_DESIGN_ASK = re.compile(r"\b(recomend|conviene|como (armo|hago|encaro|estructuro)|que usarias|elegir|por que)\w*")

# Comparisons TABLE_WORTHY_PATTERN misses ("¿qué es mejor, X o Y?").
_RE_COMPARE_EXTRA = re.compile(r"\b(que|cual) (es|seria|me conviene) mejor\b|\bque (me )?conviene\b")

_RE_EXPLAIN_DEEP = re.compile(
    r"\b(paso a paso|en detalle|detallad\w*|a fondo|en profundidad|diferencias? entre|"
    r"que diferencia|como funciona .{0,40} (por dentro|internamente))\b"
)


def _signals(t: str, *, rich_hint: Optional[str], detail_level: Optional[str],
             complexity: Optional[str]) -> list[tuple[str, str]]:
    """Every (kind, reason) the turn matches. The caller keeps the hardest one, so an
    early, weaker rule ("explicame Python") can never hide a harder one."""
    found: list[tuple[str, str]] = []
    has_block = bool(_RE_CODE_BLOCK.search(t))
    if (_RE_CODE_WRITE.search(t) or _RE_CODE_IDENT.search(t)
            or (_RE_CODE_WRITE_WEAK.search(t) and _RE_CODE_CONTEXT.search(t))
            or (has_block and _RE_CODE_FIX.search(t))):
        found.append(("code", "code write/fix request"))
    if has_block or (_RE_LANG.search(t) and _RE_CODE_ASK.search(t)):
        found.append(("code_explain", "code shown or asked about"))
    if _RE_CALENDAR.search(t):
        found.append(("calendar", "calendar arithmetic"))
    if _RE_LOGIC.search(t):
        found.append(("logic", "logic puzzle markers"))
    if _RE_PREMISE.search(t):
        found.append(("premise", "why-question carrying a claim"))
    numbers = len(_RE_NUMBER.findall(t))
    if _RE_ARITH.search(t):
        found.append(("arith", "plain arithmetic"))
    elif numbers >= 2 and (_RE_QUANT_ASK.search(t) or _RE_QUANT_TRAP.search(t)):
        found.append(("quant", f"word problem ({numbers} numbers)"))
    elif numbers >= 1 and _RE_QUANT_TRAP.search(t):
        found.append(("quant", "quantitative trap markers"))
    if _RE_DESIGN.search(t) and _RE_DESIGN_ASK.search(t):
        found.append(("design", "system design with trade-offs"))
    if _RE_PLAN.search(t):
        hits = [{m.group(0) for m in g.finditer(t)} for g in _PLAN_CONSTRAINT_GROUPS]
        groups = sum(1 for h in hits if h)
        terms = sum(len(h) for h in hits)
        # Constraints interact when they are of different kinds (a deadline AND hours),
        # or when there are several of one kind (availability, rests AND holidays).
        if groups >= 2 or terms >= 3:
            found.append(("planning_constrained", f"plan with {terms} constraints in {groups} groups"))
        else:
            found.append(("planning", "plan request"))
    if rich_hint == "table" or TABLE_WORTHY_PATTERN.search(t) or _RE_COMPARE_EXTRA.search(t):
        found.append(("comparison", "comparison"))
    if detail_level == "detailed" or _RE_EXPLAIN_DEEP.search(t):
        found.append(("explain_deep", "detailed explanation"))
    if complexity == "complex":
        found.append(("complex_hint", "router complexity=complex"))
    return found


POLICY_ENV = "ZEPHYRO_REASONING_POLICY"
POLICY_MODES = ("off", "shadow", "on")
DEFAULT_POLICY_MODE = "shadow"


def policy_mode() -> str:
    """off: no decision at all · shadow: decide and log only · on: apply the decision.

    The THINK toggle (user_forced) is applied in shadow mode too: the user asked for it.
    """
    mode = os.getenv(POLICY_ENV, DEFAULT_POLICY_MODE).strip().lower()
    return mode if mode in POLICY_MODES else DEFAULT_POLICY_MODE


def pick_reasoning(
    user_input: str,
    *,
    intent: Optional[str] = None,
    complexity: Optional[str] = None,
    rich_hint: Optional[str] = None,
    detail_level: Optional[str] = None,
    user_forced: bool = False,
) -> ReasoningDecision:
    """Decide the reasoning level for one turn. Router hints are optional."""
    if user_forced:
        return ReasoningDecision(KIND_LEVEL["forced"], "forced", "user asked to think (THINK toggle)")
    found = _signals(_fold(user_input or ""), rich_hint=rich_hint,
                     detail_level=detail_level, complexity=complexity)
    if not found:
        return ReasoningDecision(KIND_LEVEL["chat"], "chat", "no reasoning markers")
    kind, reason = max(found, key=lambda kr: _RANK[KIND_LEVEL[kr[0]]])  # first wins ties
    return ReasoningDecision(KIND_LEVEL[kind], kind, reason)
