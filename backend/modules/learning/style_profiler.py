"""
StyleProfiler — Infiere preferencias de estilo de respuesta del usuario
a partir de su vocabulario, longitud de mensajes y correcciones explícitas.

Persiste en la tabla `preferences` del UserProfile existente.
No requiere feedback explícito: aprende pasivamente de los mensajes.
"""
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from modules.memory_extended.user_profile import UserProfile

# Señales de preferencia de longitud
_WANTS_BRIEF = [
    "sé más breve", "más corto", "resumido", "en pocas palabras",
    "solo lo esencial", "no tanto detalle", "más conciso",
]
_WANTS_DETAILED = [
    "explica más", "más detalle", "profundiza", "amplía",
    "con más profundidad", "más completo", "elabora",
]

# Vocabulario técnico → usuario técnico
_TECHNICAL_VOCAB = [
    "función", "clase", "método", "instancia", "módulo", "parámetro",
    "argumento", "retorna", "callback", "async", "await", "endpoint",
    "query", "schema", "pipeline", "embedding", "vector", "token",
    "orm", "api", "sdk", "dockerfile", "daemon", "socket", "buffer",
]

# Vocabulario simple → usuario no técnico
_SIMPLE_VOCAB_SIGNALS = [
    "qué es", "cómo se hace", "no entiendo", "en simple",
    "para qué sirve", "sin tecnicismos",
]


class StyleProfiler:
    """
    Detecta y persiste preferencias de estilo en UserProfile.preferences.

    Claves almacenadas:
    - response_length:  "brief" | "normal" | "detailed"
    - technical_depth:  "basic" | "technical"
    - corrections_count: int (número de correcciones detectadas)
    """

    def __init__(self, user_profile: Optional["UserProfile"] = None):
        self.profile = user_profile

    # ------------------------------------------------------------------
    # Análisis pasivo de mensajes entrantes
    # ------------------------------------------------------------------

    def infer_from_input(self, user_input: str):
        """Analiza el input y actualiza preferencias en UserProfile si cambian."""
        if not self.profile or not user_input:
            return

        lower = user_input.lower()

        # Preferencia de longitud
        if any(p in lower for p in _WANTS_BRIEF):
            self.profile.set_preference("response_length", "brief")

        elif any(p in lower for p in _WANTS_DETAILED):
            self.profile.set_preference("response_length", "detailed")

        # Profundidad técnica (solo si el mensaje tiene términos específicos)
        technical_hits = sum(1 for w in _TECHNICAL_VOCAB if w in lower)
        if technical_hits >= 2:
            self.profile.set_preference("technical_depth", "technical")
        elif any(p in lower for p in _SIMPLE_VOCAB_SIGNALS):
            self.profile.set_preference("technical_depth", "basic")

    def infer_from_correction(self, user_input: str):
        """Cuenta correcciones explícitas (señal de calidad de respuesta)."""
        if not self.profile:
            return
        lower = user_input.lower()
        correction_signals = [
            "no, lo que quería", "me refería a", "no era eso",
            "eso no es lo que", "no entendiste",
        ]
        if any(s in lower for s in correction_signals):
            try:
                count = int(self.profile.get_preference("corrections_count") or 0)
                self.profile.set_preference("corrections_count", str(count + 1))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Generación de instrucciones para el prompt
    # ------------------------------------------------------------------

    def get_style_instructions(self) -> str:
        """
        Devuelve instrucciones de estilo para añadir al system prompt.
        Solo devuelve algo si hay preferencias no-default.
        """
        if not self.profile:
            return ""

        parts = []
        length = self.profile.get_preference("response_length") or "normal"
        depth  = self.profile.get_preference("technical_depth")  or "technical"

        if length == "brief":
            parts.append("El usuario prefiere respuestas muy concisas. Ve al grano, sin explicaciones largas.")
        elif length == "detailed":
            parts.append("El usuario quiere respuestas detalladas y completas.")

        if depth == "basic":
            parts.append("Evita tecnicismos; usa lenguaje accesible.")
        elif depth == "technical":
            parts.append("El usuario tiene perfil técnico; usa terminología precisa sin sobre-explicar.")

        corrections = int(self.profile.get_preference("corrections_count") or 0)
        if corrections >= 3:
            parts.append("Nota: el usuario ha corregido varias respuestas; confirma que entendiste bien antes de responder.")

        if not parts:
            return ""
        return "[Preferencias del usuario]\n" + " ".join(parts) + "\n"
