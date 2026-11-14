"""
prompt_cache — utilidades para Anthropic prompt caching (cache_control).

El system prompt de Zephyro tiene un prefix estable (identity, capabilities,
skills_summary) de ~4-8K tokens que cambia raramente. Marcandolo como
`cache_control: ephemeral`, Anthropic lo cachea con TTL 5min y reduce ~90%
el costo de prompt en hits dentro de la ventana.

Este modulo:
    - Estructura los bloques (build_structured_system)
    - Aplica el formato Anthropic SDK directo (use_with_anthropic_sdk)
    - Detecta cuando el provider activo soporta caching

Solo aplica cuando el provider es Anthropic (Claude). Con otros providers
(OpenAI, Gemini, DeepSeek, Ollama) el prompt vuelve a formato plano.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("zephyro.prompt_cache")


# ── Sanitización de paths del usuario en bloques cacheables ────────────

_USER_HOME_RE = re.compile(
    re.escape(os.path.expanduser("~")), re.IGNORECASE
)
_USER_REPOS_RE = re.compile(
    r"[A-Z]:[/\\]+zephyro_repos[/\\]+[\w\-\.]+", re.IGNORECASE
)


def sanitize_for_cache(text: str) -> str:
    """
    Reemplaza paths que contienen el username del usuario por placeholders
    estables. Sin esto, el prefix cambia entre máquinas y no aprovecha el cache.
    """
    text = _USER_HOME_RE.sub("<HOME>", text)
    text = _USER_REPOS_RE.sub("<REPO>", text)
    return text


# ── Bloque structured ──────────────────────────────────────────────────

@dataclass
class CacheableBlock:
    """Un bloque que potencialmente se cachea."""
    text: str
    cacheable: bool = False  # True → marcar con cache_control: ephemeral
    name: str = ""           # Para logs/debugging

    def to_anthropic(self) -> dict[str, Any]:
        block: dict[str, Any] = {"type": "text", "text": self.text}
        if self.cacheable:
            block["cache_control"] = {"type": "ephemeral"}
        return block


@dataclass
class StructuredSystemPrompt:
    """
    Bloques que componen el system prompt completo. Los primeros son
    cacheables (estables), los últimos no (varían per-request).

    Orden: identity → capabilities → skills_summary → session_specific
    """
    blocks: list[CacheableBlock] = field(default_factory=list)

    def add_cacheable(self, text: str, name: str = "") -> "StructuredSystemPrompt":
        if text and text.strip():
            self.blocks.append(CacheableBlock(
                text=sanitize_for_cache(text), cacheable=True, name=name,
            ))
        return self

    def add_dynamic(self, text: str, name: str = "") -> "StructuredSystemPrompt":
        if text and text.strip():
            self.blocks.append(CacheableBlock(
                text=text, cacheable=False, name=name,
            ))
        return self

    def to_anthropic_content(self) -> list[dict[str, Any]]:
        """
        Devuelve el array `content` para `system` en messages.create() del SDK.
        """
        return [b.to_anthropic() for b in self.blocks]

    def to_plain_text(self) -> str:
        """Fallback para providers sin caching: concatenación simple."""
        return "\n\n".join(b.text for b in self.blocks if b.text.strip())

    def estimated_cacheable_tokens(self) -> int:
        """Aprox tokens cacheables (~4 chars por token)."""
        chars = sum(len(b.text) for b in self.blocks if b.cacheable)
        return chars // 4

    def __len__(self) -> int:
        return len(self.blocks)


# ── Builder helper ────────────────────────────────────────────────────

def build_structured_system(
    *,
    identity: str = "",
    capabilities: str = "",
    skills_summary: str = "",
    session_specific: str = "",
    extra_dynamic: Optional[list[tuple[str, str]]] = None,
) -> StructuredSystemPrompt:
    """
    Conveniencia: construye un StructuredSystemPrompt con los bloques estandar.

    Args:
        identity:         prefix estable (rol, personalidad)
        capabilities:     bloque estable de capacidades
        skills_summary:   resumen de skills activas (cambia con hot-reload)
        session_specific: contexto de sesión (varía per-request, NO cachear)
        extra_dynamic:    [(name, text), ...] bloques dinámicos extra
    """
    sp = StructuredSystemPrompt()
    sp.add_cacheable(identity, "identity")
    sp.add_cacheable(capabilities, "capabilities")
    sp.add_cacheable(skills_summary, "skills_summary")
    sp.add_dynamic(session_specific, "session")
    if extra_dynamic:
        for name, text in extra_dynamic:
            sp.add_dynamic(text, name)
    return sp


# ── Provider detection ────────────────────────────────────────────────

def supports_caching(provider: Optional[str]) -> bool:
    """
    True si el provider soporta cache_control. Por ahora solo Anthropic
    via SDK directo. LangChain ChatAnthropic puede o no propagar el flag
    según la versión.
    """
    if not provider:
        return False
    return provider.lower() in {"anthropic", "claude_cli"}
