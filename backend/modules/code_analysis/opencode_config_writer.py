"""
Generador del `opencode.jsonc` en `%APPDATA%\\opencode\\opencode.jsonc`.

OpenCode lee su configuración de ahí. Cuando el usuario configura un
provider via UI, este módulo regenera el JSONC manteniendo los providers
ya configurados que no toca Zephyro (modo merge, no overwrite).

Soporta env-substitution: `{env="VAR_NAME"}` en lugar de la API key literal.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("zephyro.opencode_config")


# Mapping de provider name (Zephyro) → provider id (OpenCode)
_PROVIDER_ID_MAP: dict[str, str] = {
    "deepseek": "deepseek",
    "openrouter": "openrouter",
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "google",
}


def opencode_config_path() -> Path:
    """Resuelve la ruta del config segun OS."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "opencode" / "opencode.jsonc"
    # Linux/Mac
    return Path.home() / ".config" / "opencode" / "opencode.jsonc"


def read_existing(path: Path | None = None) -> dict[str, Any]:
    """Lee opencode.jsonc existente, strippeando comentarios."""
    p = path or opencode_config_path()
    if not p.exists():
        return {}
    try:
        raw = p.read_text(encoding="utf-8")
        # Strip line comments // y block /* */
        no_block = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
        no_line = re.sub(r"^\s*//.*$", "", no_block, flags=re.MULTILINE)
        return json.loads(no_line)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("[opencode_config] no pude parsear %s: %s", p, exc)
        return {}


def backup_existing(path: Path | None = None) -> Path | None:
    """Crea backup con timestamp del archivo actual antes de sobrescribir."""
    p = path or opencode_config_path()
    if not p.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = p.with_suffix(f".jsonc.bak.{ts}")
    try:
        backup.write_bytes(p.read_bytes())
        return backup
    except OSError as exc:
        log.error("[opencode_config] backup falló: %s", exc)
        return None


def write_providers(
    providers: list[dict[str, Any]],
    *,
    path: Path | None = None,
    preserve_unknown: bool = True,
) -> Path:
    """
    Escribe `opencode.jsonc` con los providers dados.

    Args:
        providers: lista de dicts con {name, base_url, api_key_env, models}.
                   `name` debe ser una key de _PROVIDER_ID_MAP.
        path: override del path (default %APPDATA%/opencode/opencode.jsonc).
        preserve_unknown: si True, mantiene providers existentes que no
                          estén en `providers` (modo merge).
    """
    p = path or opencode_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    existing = read_existing(p)
    backup_existing(p)

    out_providers: dict[str, Any] = (
        dict(existing.get("provider", {})) if preserve_unknown else {}
    )

    for prov in providers:
        name = prov.get("name", "")
        opencode_id = _PROVIDER_ID_MAP.get(name)
        if opencode_id is None:
            log.warning("[opencode_config] provider %r sin mapping a OpenCode", name)
            continue

        block: dict[str, Any] = {}
        if prov.get("base_url"):
            block["options"] = {"baseURL": prov["base_url"]}
        if prov.get("api_key_env"):
            block.setdefault("options", {})["apiKey"] = {"env": prov["api_key_env"]}

        models_block: dict[str, Any] = {}
        for model in prov.get("models", []):
            # OpenCode usa el suffix después del provider/
            short = model.split("/", 1)[1] if "/" in model else model
            models_block[short] = {}
        if models_block:
            block["models"] = models_block

        out_providers[opencode_id] = block

    config: dict[str, Any] = dict(existing)
    config["provider"] = out_providers
    config.setdefault("$schema", "https://opencode.ai/config.json")

    # Escribir como JSON (no JSONC para evitar parsers custom)
    header = (
        "// Generado por Zephyro provider_config — editar via la UI\n"
        f"// Última actualización: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    body = json.dumps(config, indent=2, ensure_ascii=False)
    p.write_text(header + body + "\n", encoding="utf-8")
    log.info("[opencode_config] escrito %s con %d providers", p, len(out_providers))
    return p
