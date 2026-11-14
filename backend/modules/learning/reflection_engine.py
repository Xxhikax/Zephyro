"""
ReflectionEngine — Cada N interacciones llama al cloud LLM para generar
un resumen de lo aprendido sobre el usuario: preferencias, patrones,
áreas de mejora. El resultado se inyecta como contexto en futuros prompts.
Mantiene un historial rolling de las últimas 5 reflexiones para dar continuidad
y detectar cambios en el comportamiento del usuario entre sesiones.
"""
import json
import time
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any

REFLECTIONS_FILE = Path(__file__).parent.parent.parent / "storage" / "reflections.json"
_MAX_HISTORY = 5

_REFLECTION_PROMPT_TEMPLATE = """Eres el sistema de meta-aprendizaje de Zephyro, un asistente personal.
Analiza las siguientes conversaciones recientes y extrae insights sobre el usuario y su uso del asistente.

CONVERSACIONES RECIENTES:
{conversations}

PERFIL ACTUAL DEL USUARIO:
{user_profile}

Genera un resumen conciso (máximo 5 líneas) con:
1. Preferencias observadas (longitud de respuesta, nivel técnico, tono)
2. Temas recurrentes
3. Patrones de uso (qué hace bien el asistente, qué le cuesta)
4. Una instrucción de comportamiento para mejorar futuras respuestas

Responde SOLO con el resumen, sin encabezados ni formato extra. Escribe como si fuera una nota interna."""

_REFLECTION_PROMPT_TEMPLATE_WITH_BUGS = """Eres el sistema de meta-aprendizaje de Zephyro, un asistente personal.
Analiza las siguientes conversaciones recientes y extrae insights sobre el usuario y los errores del sistema.

CONVERSACIONES RECIENTES:
{conversations}

PERFIL ACTUAL DEL USUARIO:
{user_profile}

DATOS DEL SISTEMA (bug reports recientes):
{bug_summary}

Genera un resumen conciso (máximo 6 líneas) con:
1. Preferencias observadas (longitud de respuesta, nivel técnico, tono)
2. Temas recurrentes
3. Patrones de uso (qué hace bien el asistente, qué le cuesta)
4. Una instrucción de comportamiento para mejorar futuras respuestas
5. Instrucciones para corregir los patrones de error detectados en los bug reports

Responde SOLO con el resumen, sin encabezados ni formato extra. Escribe como si fuera una nota interna."""


class ReflectionEngine:
    """
    Genera reflexiones periódicas sobre el uso del asistente.
    La reflexión más reciente se inyecta al inicio del system prompt.
    """

    def __init__(self, reflect_every: int = 30):
        self.reflect_every = reflect_every
        self._last_reflected_at: int = 0
        self._current_reflection: str = ""
        self._last_reflection_time: float = 0.0
        self._history: List[Dict[str, Any]] = []  # Rolling history of past reflections
        REFLECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self):
        try:
            if REFLECTIONS_FILE.exists():
                data = json.loads(REFLECTIONS_FILE.read_text(encoding="utf-8"))
                self._current_reflection = data.get("latest", "")
                self._last_reflected_at  = data.get("last_interaction_count", 0)
                self._last_reflection_time = data.get("timestamp", 0.0)
                self._history = data.get("history", [])
        except Exception as e:
            print(f"[Reflection] No se pudo cargar reflexión previa: {e}")

    def _save(self, reflection: str, interaction_count: int):
        try:
            # Añadir al historial rolling antes de sobreescribir latest
            if self._current_reflection:
                self._history.append({
                    "text": self._current_reflection,
                    "interaction_count": self._last_reflected_at,
                    "timestamp": self._last_reflection_time,
                })
                self._history = self._history[-_MAX_HISTORY:]  # Mantener solo las últimas N
            data = {
                "latest":                  reflection,
                "last_interaction_count":  interaction_count,
                "timestamp":               time.time(),
                "history":                 self._history,
            }
            REFLECTIONS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[Reflection] Error guardando reflexión: {e}")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def get_current_reflection(self) -> str:
        """Devuelve la reflexión vigente para inyectar en el prompt (puede ser vacía)."""
        return self._current_reflection

    def maybe_reflect(
        self,
        interaction_count: int,
        recent_conversations: List[Dict[str, Any]],
        user_profile_summary: str,
        llm_fn: Callable[[str], str],
        bug_summary: Optional[str] = None,
    ) -> Optional[str]:
        """
        Genera una nueva reflexión si se alcanzó el umbral de interacciones.
        `llm_fn` debe ser una función síncrona que toma un prompt str y devuelve str.
        Devuelve el texto de la reflexión, o None si no es el momento.
        """
        if interaction_count - self._last_reflected_at < self.reflect_every:
            return None
        if not recent_conversations:
            return None

        prompt = self._build_prompt(recent_conversations, user_profile_summary, bug_summary)
        try:
            reflection = llm_fn(prompt)
            if reflection and len(reflection.strip()) > 20:
                self._current_reflection = reflection.strip()
                self._last_reflected_at  = interaction_count
                self._save(self._current_reflection, interaction_count)
                print(f"[Reflection] Nueva reflexión generada ({len(self._current_reflection)} chars).")
                return self._current_reflection
        except Exception as e:
            print(f"[Reflection] Error generando reflexión: {e}")
        return None

    def _build_prompt(
        self,
        conversations: List[Dict[str, Any]],
        user_profile_summary: str,
        bug_summary: Optional[str] = None,
    ) -> str:
        conv_lines = []
        for c in conversations[-20:]:   # Máximo últimas 20
            u = c.get("user_input", "")[:200]
            r = c.get("response",   "")[:300]
            conv_lines.append(f"Bruno: {u}\nZephyro: {r}")
        conv_text = "\n\n".join(conv_lines) or "(sin conversaciones)"
        profile   = user_profile_summary or "(sin perfil)"

        if bug_summary:
            return _REFLECTION_PROMPT_TEMPLATE_WITH_BUGS.format(
                conversations=conv_text,
                user_profile=profile,
                bug_summary=bug_summary,
            )
        return _REFLECTION_PROMPT_TEMPLATE.format(
            conversations=conv_text,
            user_profile=profile,
        )

    def get_history(self) -> List[Dict[str, Any]]:
        """Devuelve el historial de reflexiones anteriores (más antiguas primero)."""
        return list(self._history)

    def format_for_system_prompt(self) -> str:
        """Formatea la reflexión para prepender al system prompt.
        Incluye un delta del historial si hay entradas previas con temas distintos."""
        if not self._current_reflection:
            return ""
        result = f"[Contexto aprendido sobre el usuario]\n{self._current_reflection}\n"
        # Si hay historial, añadir nota sobre la reflexión anterior para continuidad
        if self._history:
            prev = self._history[-1].get("text", "")
            if prev and prev != self._current_reflection:
                # Solo incluir si difiere — añadir como nota compacta
                prev_short = prev[:200].replace("\n", " ")
                result += f"[Reflexión anterior resumida: {prev_short}]\n"
        return result
