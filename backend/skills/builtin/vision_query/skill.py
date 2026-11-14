"""Vision Query skill — answer questions about the user's screen.

When the user asks something like "mira mi pantalla", "qué ves", or
"ayúdame con esto que tengo en pantalla", this skill captures a screenshot
and sends it (together with the user's question) to Claude Haiku Vision via
the orchestrator's `vision_capture` + `vision_analyze` modules.

This exists because the existing `/api/vision/screen-query` REST endpoint
is never invoked from voice/chat — the orchestrator would otherwise route
the input to the main LLM, which truthfully replies "I don't have screen
capture", confusing the user.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill


# Match both explicit ("mira mi pantalla") and implicit ("mira esto",
# "qué opinas de esto") screen-reference phrasings. Tight enough that
# casual chatter doesn't trigger it.
_VISION_QUERY_RE = re.compile(
    r'\b(?:'
    # ── Explicit references to the screen ───────────────────────────────
    r'(?:mira|m[ií]rame|ve|vea|f[ií]j[aá]te|chec[aá]|revis[aá]|analiz[aá])\s+'
        r'(?:mi\s+|en\s+(?:mi\s+)?|la\s+)?pantalla\b'
    r'|qu[eé]\s+(?:ves|hay|tengo|aparece|se\s+ve)\s+(?:en\s+)?'
        r'(?:mi\s+|la\s+)?pantalla\b'
    r'|descr[ií]be(?:me)?\s+(?:lo\s+que\s+ves|mi\s+pantalla|la\s+pantalla)\b'
    r'|captur[aá]\s+(?:mi\s+)?pantalla\b'
    r'|puedes?\s+ver\s+(?:mi\s+|la\s+)?pantalla\b'
    r'|lee\s+(?:lo\s+que\s+(?:ves|aparece)|mi\s+pantalla)\b'
    # ── Implicit ("esto" deíctico = lo visible) ─────────────────────────
    r'|(?:ayud[aá]|ay[uú]dame)\s+con\s+(?:esto|lo\s+que\s+ves|esto\s+que\s+tengo|esto\s+que\s+ves)\b'
    r'|mira\s+esto(?:\s+que\s+(?:tengo|ves))?\b'
    r'|qu[eé]\s+opin[aá]s\s+(?:de\s+|sobre\s+)?(?:esto|lo\s+que\s+ves|esto\s+que\s+ves)\b'
    r'|ve\s+esto(?:\s+que\s+(?:tengo|ves))?\b'
    r')',
    re.IGNORECASE,
)


# Cache TTL — keep recent answers so a repeated question within a minute
# doesn't trigger a second capture + Haiku call.
_CACHE_TTL_SEC = 60


class VisionQuerySkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # cache: input_lower → (answer, expires_at)
        self._cache: dict[str, tuple[str, float]] = {}

    def matches_command(self, user_input: str) -> bool:
        return bool(_VISION_QUERY_RE.search(user_input.lower()))

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        # Cache lookup — same question within TTL → return cached answer.
        key = user_input.lower().strip()
        now = time.time()
        cached = self._cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "Sistema de visión no disponible (orchestrator no inicializado)."

        capture = getattr(orchestrator, "vision_capture", None)
        analyze = getattr(orchestrator, "vision_analyze", None)
        if capture is None or analyze is None:
            return "Sistema de visión no disponible (módulos no cargados)."

        # Capture in a worker thread — capture() is sync and can block on display.
        try:
            image_path = await asyncio.to_thread(capture.capture)
        except Exception as exc:
            return f"No pude capturar la pantalla: {exc}. Verifica permisos."

        if not image_path or not Path(image_path).exists():
            return "No pude capturar la pantalla (archivo vacío). Intenta de nuevo."

        prompt = (
            f'El usuario pregunta: "{user_input}"\n\n'
            "Esta es una captura de su pantalla. Responde de forma concisa y "
            "directa en español. Si ves la interfaz de Zephyro/Tauri/JARVIS "
            "(terminal, chat, HUD verde phosphor), ignórala y concéntrate en "
            "el contenido del resto de la pantalla.\n\n"
            "Responde solo lo que el usuario preguntó. Máximo 5 oraciones."
        )

        try:
            result = await asyncio.to_thread(
                analyze.analyze, image_path, prompt, True, "anthropic"
            )
        except Exception as exc:
            return f"Error analizando la captura: {exc}"
        finally:
            try:
                Path(image_path).unlink(missing_ok=True)
            except Exception:
                pass

        if not result:
            return "El analizador de visión no devolvió respuesta. Intenta de nuevo."
        if isinstance(result, str) and result.startswith("[") and "Error" in result:
            return result  # propagate error message from analyze

        answer = str(result).strip()
        self._cache[key] = (answer, now + _CACHE_TTL_SEC)
        # Trim cache opportunistically.
        if len(self._cache) > 32:
            stale = [k for k, (_, exp) in self._cache.items() if exp <= now]
            for k in stale:
                self._cache.pop(k, None)
        return answer

    def _get_orchestrator(self):
        """Resolve orchestrator via web_app singleton. Returns None if unavailable."""
        try:
            import web_app
            app = getattr(web_app, "app", None)
            if app is None:
                return None
            state = getattr(app, "state", None)
            if state is None:
                return None
            return getattr(state, "orchestrator", None)
        except Exception:
            return None
