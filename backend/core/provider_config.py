"""
ProviderConfig — registro central de providers/modelos disponibles.

Persiste en `storage/providers.json`. API keys siguen viviendo en `.env`
como fuente de verdad, con `keyring` (Windows Credential Manager) como
cache cifrado opcional.

El singleton es accesible desde llm_manager.py, opencode_bridge.py, y la
IntrospectionSkill.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from core.model_catalog import ANTHROPIC_LEGACY_MODELS, ANTHROPIC_MODELS

log = logging.getLogger("zephyro.providers")


# ── Defaults ────────────────────────────────────────────────────────────

# Tareas que se asignan a providers
TASK_KINDS = (
    "chat",                # respuestas conversacionales en cloud mode
    "introspection_t2",    # Tier 2 de IntrospectionSkill (default DeepSeek)
    "introspection_t3",    # Tier 3 (Anthropic SDK directo)
    "introspection_t4",    # Tier 4 (Claude CLI)
    "skill_proposer",      # destila TaskEnvelopes en drafts (Parte B)
    "diagnostic_t1",       # DiagnosticAgent Tier 1 — DeepSeek flash (rápido)
    "diagnostic_t2",       # DiagnosticAgent Tier 2 — Sonnet (problemas moderados)
    "diagnostic_t3_context",   # DiagnosticAgent Tier 3 — DeepSeek v4 pro (contexto extenso)
    "diagnostic_t3_reasoning", # DiagnosticAgent Tier 3 — Opus + thinking (razonamiento)
)


# Catálogo de providers conocidos. La UI puede ofrecer estos defaults; el
# usuario configura solo los que use.
_KNOWN_PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "backend": "anthropic_sdk",
        "models": [ANTHROPIC_MODELS["fast"], ANTHROPIC_MODELS["balanced"],
                   ANTHROPIC_MODELS["power"], ANTHROPIC_MODELS["frontier"]],
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url": None,
    },
    # IDs con prefijo "deepseek/" son naming de OpenCode (models.dev), no de la
    # API directa (esa vive en core/model_catalog.py). deepseek-r1 ya no existe.
    "deepseek": {
        "backend": "opencode_cli",
        "models": [
            "deepseek/deepseek-v4-pro",
            "deepseek/deepseek-v4-flash",
        ],
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
    },
    "openrouter": {
        "backend": "opencode_cli",
        "models": [
            "openrouter/anthropic/claude-sonnet-4",
            "openrouter/google/gemini-2.5-pro",
            "openrouter/meta-llama/llama-3.3-70b",
        ],
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "openai": {
        "backend": "openai_sdk",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "api_key_env": "OPENAI_API_KEY",
        "base_url": None,
    },
    "gemini": {
        "backend": "google_sdk",
        "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
        "api_key_env": "GOOGLE_API_KEY",
        "base_url": None,
    },
    "claude_cli": {
        "backend": "claude_cli",
        "models": [ANTHROPIC_MODELS["balanced"], ANTHROPIC_MODELS["power"]],
        "api_key_env": None,  # hereda sesión Pro/Max
        "base_url": None,
    },
    "ollama_local": {
        "backend": "ollama",
        "models": ["llama3.2:1b", "llama3.2:3b", "qwen2.5-coder:7b"],
        "api_key_env": None,
        "base_url": "http://localhost:11434",
    },
}


_SONNET = ANTHROPIC_MODELS["balanced"]
_OPUS = ANTHROPIC_MODELS["power"]

_DEFAULT_TASK_ASSIGNMENT: dict[str, dict[str, Optional[str]]] = {
    "chat":              {"provider": "anthropic", "model": _SONNET},
    "introspection_t2":  {"provider": "deepseek",  "model": "deepseek/deepseek-v4-pro"},
    "introspection_t3":  {"provider": "anthropic", "model": _SONNET},
    "introspection_t4":  {"provider": "claude_cli", "model": None},
    "skill_proposer":    {"provider": "anthropic", "model": _SONNET},
    "diagnostic_t1":          {"provider": "deepseek",  "model": "deepseek/deepseek-v4-flash"},
    "diagnostic_t2":          {"provider": "anthropic", "model": _SONNET},
    "diagnostic_t3_context":  {"provider": "deepseek",  "model": "deepseek/deepseek-v4-pro"},
    "diagnostic_t3_reasoning":{"provider": "anthropic", "model": _OPUS},
}

# IDs que ya no existen en ningún provider: se quitan de un providers.json viejo.
_RETIRED_MODEL_IDS: frozenset[str] = frozenset({"deepseek/deepseek-r1"})


def _remap_model_id(model: Optional[str]) -> Optional[str]:
    """Legacy → vigente; retirado → None; resto sin cambio."""
    if not model:
        return model
    if model in _RETIRED_MODEL_IDS:
        return None
    return ANTHROPIC_LEGACY_MODELS.get(model, model)


# ── Dataclasses ─────────────────────────────────────────────────────────

@dataclass
class ProviderEntry:
    name: str
    backend: str  # anthropic_sdk | opencode_cli | claude_cli | openai_sdk | google_sdk | ollama
    enabled: bool = False
    api_key_env: Optional[str] = None
    base_url: Optional[str] = None
    default_model: Optional[str] = None
    models: list[str] = field(default_factory=list)
    last_test_at: Optional[float] = None
    last_test_ok: Optional[bool] = None
    last_test_error: Optional[str] = None

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        d = asdict(self)
        # No exponer hint de la API key real al frontend
        if redact:
            d["api_key_set"] = bool(self.api_key_env and os.getenv(self.api_key_env))
            d["api_key_hint"] = self._key_hint()
        return d

    def _key_hint(self) -> Optional[str]:
        if not self.api_key_env:
            return None
        val = os.getenv(self.api_key_env)
        if not val:
            return None
        # mostrar últimos 4 chars
        return f"***{val[-4:]}" if len(val) > 4 else "****"


# ── Storage ─────────────────────────────────────────────────────────────

class ProviderConfig:
    """
    Singleton persistente. Lock para evitar race conditions cuando la UI
    actualiza mientras un request está en curso.
    """

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self.storage_path = storage_path or Path("storage/providers.json")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._providers: dict[str, ProviderEntry] = {}
        self._task_assignment: dict[str, dict[str, Optional[str]]] = dict(_DEFAULT_TASK_ASSIGNMENT)
        self._load_or_seed()

    # ── Persistence ─────────────────────────────────────────────────

    def _load_or_seed(self) -> None:
        if self.storage_path.exists():
            try:
                data = json.loads(self.storage_path.read_text(encoding="utf-8"))
                for name, entry_data in data.get("providers", {}).items():
                    self._providers[name] = ProviderEntry(**entry_data)
                self._task_assignment.update(data.get("task_assignment", {}))
                if self._migrate_legacy_models():
                    self._save()
                return
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                log.warning("[providers] no pude cargar %s: %s — re-seeding", self.storage_path, exc)

        # Seed inicial: importar de .env los providers conocidos cuya API key esté seteada
        for name, defaults in _KNOWN_PROVIDERS.items():
            api_key_env = defaults.get("api_key_env")
            has_key = bool(api_key_env and os.getenv(api_key_env))
            self._providers[name] = ProviderEntry(
                name=name,
                backend=defaults["backend"],
                enabled=has_key or defaults.get("api_key_env") is None,
                api_key_env=api_key_env,
                base_url=defaults.get("base_url"),
                default_model=defaults["models"][0] if defaults["models"] else None,
                models=list(defaults["models"]),
            )
        self._save()

    def _migrate_legacy_models(self) -> bool:
        """
        Un providers.json existente se carga tal cual, así que los seeds nuevos
        de ``_KNOWN_PROVIDERS`` nunca llegaban al usuario. Acá: remapeamos IDs
        legacy (Sonnet 4.6 → Sonnet 5, etc.), sacamos los retirados y agregamos
        los del catálogo que falten. Devuelve True si cambió algo.
        """
        changed = False
        for name, entry in self._providers.items():
            new_models: list[str] = []
            for m in entry.models:
                r = _remap_model_id(m)
                if r and r not in new_models:
                    new_models.append(r)
            for m in _KNOWN_PROVIDERS.get(name, {}).get("models", []):
                if m not in new_models:
                    new_models.append(m)
            if new_models != entry.models:
                entry.models = new_models
                changed = True
            new_default = _remap_model_id(entry.default_model)
            if new_default is None and new_models:
                new_default = new_models[0]
            if new_default != entry.default_model:
                entry.default_model = new_default
                changed = True

        for task, assign in list(self._task_assignment.items()):
            new_model = _remap_model_id(assign.get("model"))
            if new_model is None and assign.get("model"):
                # Modelo retirado sin equivalente: volver al default del catálogo
                new_model = _DEFAULT_TASK_ASSIGNMENT.get(task, {}).get("model")
            if new_model != assign.get("model"):
                # Copia: los dicts de _DEFAULT_TASK_ASSIGNMENT se comparten por
                # referencia (shallow copy en __init__) y no hay que mutarlos.
                self._task_assignment[task] = {**assign, "model": new_model}
                changed = True

        if changed:
            log.info("[providers] providers.json migrado a IDs de modelo vigentes")
        return changed

    def _save(self) -> None:
        try:
            data = {
                "providers": {
                    name: asdict(entry) for name, entry in self._providers.items()
                },
                "task_assignment": self._task_assignment,
                "updated_at": time.time(),
            }
            self.storage_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            log.error("[providers] no pude guardar: %s", exc)

    # ── Public API ──────────────────────────────────────────────────

    def list_providers(self) -> list[dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._providers.values()]

    def get(self, name: str) -> Optional[ProviderEntry]:
        with self._lock:
            return self._providers.get(name)

    def update(
        self,
        name: str,
        *,
        enabled: Optional[bool] = None,
        default_model: Optional[str] = None,
        models: Optional[list[str]] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> ProviderEntry:
        """
        Actualiza un provider. Si `api_key` se pasa, la persiste en `.env`
        (y opcionalmente en keyring si está disponible).
        """
        with self._lock:
            entry = self._providers.get(name)
            if entry is None:
                # Permitir crear providers custom si están en _KNOWN_PROVIDERS
                defaults = _KNOWN_PROVIDERS.get(name)
                if defaults is None:
                    raise KeyError(f"Provider desconocido: {name}")
                entry = ProviderEntry(
                    name=name,
                    backend=defaults["backend"],
                    api_key_env=defaults.get("api_key_env"),
                    base_url=defaults.get("base_url"),
                    default_model=defaults["models"][0] if defaults["models"] else None,
                    models=list(defaults["models"]),
                )
                self._providers[name] = entry

            if enabled is not None:
                entry.enabled = enabled
            if default_model is not None:
                entry.default_model = default_model
                if default_model not in entry.models:
                    entry.models.append(default_model)
            if models is not None:
                entry.models = list(models)
            if base_url is not None:
                entry.base_url = base_url
            if api_key is not None and entry.api_key_env:
                _persist_api_key(entry.api_key_env, api_key)
                # Actualizar el env de este proceso para que el SDK lo vea
                os.environ[entry.api_key_env] = api_key

            self._save()
            return entry

    def disable(self, name: str) -> None:
        with self._lock:
            entry = self._providers.get(name)
            if entry is not None:
                entry.enabled = False
                self._save()

    def record_test_result(
        self, name: str, ok: bool, error: Optional[str] = None
    ) -> None:
        with self._lock:
            entry = self._providers.get(name)
            if entry is None:
                return
            entry.last_test_at = time.time()
            entry.last_test_ok = ok
            entry.last_test_error = error
            self._save()

    # ── Task assignment ─────────────────────────────────────────────

    def get_task(self, task: str) -> dict[str, Optional[str]]:
        with self._lock:
            return dict(self._task_assignment.get(task, {}))

    def assign_task(
        self, task: str, *, provider: str, model: Optional[str] = None
    ) -> None:
        if task not in TASK_KINDS:
            raise KeyError(f"task desconocido: {task}")
        with self._lock:
            if provider not in self._providers:
                raise KeyError(f"provider desconocido: {provider}")
            self._task_assignment[task] = {"provider": provider, "model": model}
            self._save()

    def task_assignments(self) -> dict[str, dict[str, Optional[str]]]:
        with self._lock:
            return {k: dict(v) for k, v in self._task_assignment.items()}


# ── API key persistence ─────────────────────────────────────────────────

def _persist_api_key(env_name: str, value: str) -> None:
    """Persistir API key en .env (fuente de verdad) + keyring (cache cifrado)."""
    _update_env_file(env_name, value)
    try:
        import keyring  # type: ignore
        keyring.set_password("zephyro", env_name, value)
    except Exception as exc:
        log.debug("[providers] keyring no disponible: %s", exc)


def _update_env_file(env_name: str, value: str) -> None:
    """Actualiza `.env` con `KEY=value`. Crea el archivo si no existe."""
    env_path = _find_env_file()
    if env_path is None:
        # Crear .env nuevo en la raíz del backend
        env_path = Path(__file__).resolve().parents[1] / ".env"

    lines: list[str] = []
    found = False
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{env_name}=") or line.startswith(f"{env_name} ="):
                    lines.append(f"{env_name}={value}")
                    found = True
                else:
                    lines.append(line)
        except OSError as exc:
            log.error("[providers] no pude leer %s: %s", env_path, exc)
            return

    if not found:
        lines.append(f"{env_name}={value}")

    try:
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        log.error("[providers] no pude escribir %s: %s", env_path, exc)


def _find_env_file() -> Optional[Path]:
    """Buscar .env hacia arriba desde la raíz del backend."""
    base = Path(__file__).resolve().parents[1]
    for parent in (base, base.parent):
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return None


# ── Singleton ──────────────────────────────────────────────────────────

_instance: Optional[ProviderConfig] = None
_instance_lock = threading.Lock()


def get_provider_config() -> ProviderConfig:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ProviderConfig()
    return _instance


def reset_provider_config() -> None:
    """Solo para tests."""
    global _instance
    with _instance_lock:
        _instance = None
