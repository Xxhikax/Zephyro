import os
import logging
import re
from functools import lru_cache

import anthropic

from core import verbs as _verbs

logger = logging.getLogger(__name__)

VALID_INTENTS = {"greeting", "farewell", "question", "command", "statement", "acknowledgment"}

# ── Regex pre-compilados al importar el módulo (no se recompilan en cada llamada) ──
_RE_ELONGATED_TAIL = re.compile(r"^[aeiouáéíóúy]+$")
_RE_FIRST_WORD_STRIP = re.compile(r"^\W+|\W+$")
_RE_WORDS = re.compile(r"\w+")
# Verbos de cortesía + infinitivo: "puedes abrir", "podrías buscar", "necesito cerrar"
_RE_POLITE_COMMAND = re.compile(
    r"^(?:puedes?|podr[ií]as?|necesito|quiero|quer[ií]a|me\s+gustar[ií]a)\s+"
    r"(?:que\s+)?(?:\w+\s+)?" + _verbs.verbs_pattern(),
    re.IGNORECASE,
)

# ── Vocabulario ────────────────────────────────────────────────────────────────

# Single-word tokens: matched only when they are the FIRST word of the message.
_GREETING_SINGLE = frozenset({"hola", "hey", "buenas", "buenos", "hi", "hello", "howdy"})
_FAREWELL_SINGLE = frozenset({"adiós", "adios", "chao", "chau", "bye", "goodbye"})

# Multi-word phrases: matched via str.startswith on the full lowercased input.
_GREETING_MULTI = frozenset({"qué tal", "que tal", "good morning", "good afternoon"})
_FAREWELL_MULTI = frozenset({"hasta luego", "hasta mañana", "nos vemos", "see you"})

# Acknowledgments: palabras de confirmación/agradecimiento que van al template
# en vez de al LLM completo.
_ACK_TOKENS = frozenset({
    "gracias", "thanks", "thank", "ok", "okay", "dale", "perfecto", "listo",
    "genial", "bien", "chevere", "chévere", "entendido", "de acuerdo", "claro",
    "sí gracias", "si gracias", "okey",
})

# If any of these action verbs appears in the message the greeting/farewell
# detection is suppressed — the user is giving a command that starts with or
# contains a social word (e.g. "hey abre el browser", "escribe hello world").
# Derivados de la fuente única core/verbs.py — incluyen voseo automáticamente.
_ACTION_OVERRIDE = frozenset(_verbs.all_action_verbs())

_QUESTION_STARTERS = frozenset({
    "qué", "que", "cómo", "como", "cuándo", "cuando",
    "dónde", "donde", "por qué", "porque", "quién", "quien",
    "what", "how", "when", "where", "why", "who", "which",
})
_COMMAND_VERBS = frozenset(_verbs.all_action_verbs() | {"create", "send", "get"})

_SYSTEM_PROMPT = (
    "Classify the user's message intent with ONE word. "
    "Valid intents: greeting, farewell, question, command, statement, acknowledgment. "
    "Respond with only that one word, lowercase."
)


def _is_elongated(word: str, base: str) -> bool:
    """True if word is base with trailing vowel/y repetition (e.g. 'holaaaa', 'heyyyy')."""
    if not word.startswith(base):
        return False
    tail = word[len(base):]
    return len(tail) > 0 and bool(_RE_ELONGATED_TAIL.match(tail))


@lru_cache(maxsize=256)
def classify_by_rules(text: str) -> str | None:
    """
    Clasifica el intent usando solo reglas locales. Retorna None si el input
    es genuinamente ambiguo y necesita fallback a Haiku.

    El LRU cache de 256 entradas cubre inputs repetidos (saludos, sí/no de
    confirmación en call mode) sin re-ejecutar la lógica en cada turno.
    """
    t = text.strip().lower()
    if not t:
        return None

    parts = t.split()
    first_word = _RE_FIRST_WORD_STRIP.sub("", parts[0]) if parts else ""

    word_set = frozenset(_RE_WORDS.findall(t))
    has_action = bool(word_set & _ACTION_OVERRIDE)

    if not has_action:
        # ── Saludos ───────────────────────────────────────────────────────────
        if (first_word in _GREETING_SINGLE
                or any(_is_elongated(first_word, g) for g in _GREETING_SINGLE)
                or any(t.startswith(g) for g in _GREETING_MULTI)):
            return "greeting"

        # ── Despedidas ────────────────────────────────────────────────────────
        if (first_word in _FAREWELL_SINGLE
                or any(_is_elongated(first_word, f) for f in _FAREWELL_SINGLE)
                or any(t.startswith(f) for f in _FAREWELL_MULTI)):
            return "farewell"

        # ── Acknowledgments (gracias, ok, dale, perfecto…) ───────────────────
        # Exacto de 1-3 palabras para evitar falsos positivos en frases largas
        if len(parts) <= 3 and (t in _ACK_TOKENS or first_word in _ACK_TOKENS):
            return "acknowledgment"

    # ── Preguntas ─────────────────────────────────────────────────────────────
    if t.endswith("?"):
        return "question"
    if first_word in _QUESTION_STARTERS:
        return "question"
    if t.startswith("por qué") or t.startswith("por que"):
        return "question"

    # ── Comandos directos (verbo imperativo) ─────────────────────────────────
    if first_word in _COMMAND_VERBS:
        return "command"

    # ── Comandos con prefijo cortés (puedes abrir X, necesito buscar Y) ──────
    if _RE_POLITE_COMMAND.match(t):
        return "command"

    return None  # ambiguo → fallback a Haiku


async def classify_with_haiku(text: str) -> str:
    client = anthropic.AsyncAnthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        max_retries=0,
        timeout=5.0,
    )
    from core.model_catalog import ANTHROPIC_CLASSIFIER_THINKING_OFF, ANTHROPIC_MODELS
    # Haiku 5.5: temperature no-default → 400, y el thinking (ON por default)
    # se comería los 20 tokens. Por eso thinking off y sin temperature.
    msg = await client.messages.create(
        model=ANTHROPIC_MODELS["fast"],
        max_tokens=20,
        thinking=ANTHROPIC_CLASSIFIER_THINKING_OFF,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    text_blocks = [b for b in msg.content if getattr(b, "type", "") == "text"]
    raw = (text_blocks[0].text if text_blocks else "").strip().lower()
    intent = raw.split()[0] if raw else "statement"
    logger.info(f"[intent_detector] haiku → '{intent}' for: {text[:60]!r}")
    return intent if intent in VALID_INTENTS else "statement"


async def classify_with_local(text: str) -> str | None:
    """Clasifica el intent con el modelo local rápido (ZEPHYRO_MODEL_FAST) en ~0.3s.
    Devuelve None si Ollama no está, falló, o el output no es un intent válido → el
    caller cae a Haiku."""
    from core.llm_helpers import call_local_fast
    prompt = f"{_SYSTEM_PROMPT}\n\nMessage: {text}\nIntent:"
    raw = await call_local_fast(prompt, max_tokens=8)
    if not raw:
        return None
    intent = raw.strip().lower().split()[0] if raw.strip() else ""
    if intent in VALID_INTENTS:
        logger.debug(f"[intent_detector] local → '{intent}' for: {text[:60]!r}")
        return intent
    return None


async def detect_intent(text: str) -> str:
    rule_result = classify_by_rules(text)
    if rule_result:
        logger.debug(f"[intent_detector] rules → '{rule_result}' for: {text[:60]!r}")
        return rule_result
    # Local-first: el modelo chico local resuelve el intent (~0.3s) antes de pagar un
    # round-trip a Haiku. Cae a Haiku si Ollama no está disponible.
    try:
        local_result = await classify_with_local(text)
        if local_result:
            return local_result
    except Exception as e:
        logger.debug(f"[intent_detector] local error ({e}), fallback → haiku")
    try:
        return await classify_with_haiku(text)
    except Exception as e:
        logger.warning(f"[intent_detector] haiku error ({e}), fallback → 'statement'")
        return "statement"
