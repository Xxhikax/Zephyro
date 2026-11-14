# -*- coding: utf-8 -*-
"""Regresión: la puntuación de una frase dictada no convierte una app en dominio.

Bug real (2026-09-29): Bruno dictó "Puedes abrir Discord." y el STT dejó el punto
final pegado al target. La heurística vieja (`"." in target` → es un dominio)
resolvió `https://discord.` y abrió una pestaña de Edge en vez de la app nativa.
Evidencia en logs/backend.log:

    10:31:15 [INFO] app_controller: NAVIGATE → navegador nativo: https://discord.

El antipatrón estaba repetido en 7 lugares entre router.py y app_controller.py.
Ahora todos usan `looks_like_domain` / `normalize_target` de core/app_config.py.

NOTA: acá NO se instancia AppController — `BrowserConfig`/`AppController` hacen CDP
attach al Edge real del usuario y lo matan (ver .claude/rules/tests-safety.md).
`_resolve_navigate_url` se ejercita con un `self` falso, que es todo lo que usa.
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.app_config import (  # noqa: E402
    NATIVE_APPS, WEB_APP_URLS, looks_like_domain, normalize_target,
)
from modules.automation.app_controller import AppController  # noqa: E402


# ── Helpers de core/app_config ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("discord.", "discord"),
    ("Discord.", "discord"),
    ("  spotify!  ", "spotify"),
    ("youtube,", "youtube"),
    ("¿discord?", "discord"),           # también limpia la apertura dictada
    ("discord", "discord"),
    ("", ""),
])
def test_normalize_target(raw, expected):
    assert normalize_target(raw) == expected


@pytest.mark.parametrize("value,expected", [
    # El bug: una app dictada con punto final NO es un dominio
    ("discord.", False),
    ("spotify.", False),
    ("x.", False),
    ("discord", False),
    (".com", False),
    ("mi sitio.com", False),            # con espacio no es un host
    ("", False),
    # Dominios de verdad, que tienen que seguir funcionando
    ("discord.com", True),
    ("youtube.com", True),
    ("onpe.gob.pe", True),
    ("mail.google.com", True),
    # Con path o esquema: se valida el host
    ("onpe.gob.pe/resultados", True),
    ("facebook.com/reels", True),
    ("https://youtube.com/feed", True),
])
def test_looks_like_domain(value, expected):
    assert looks_like_domain(value) is expected


# ── _resolve_navigate_url ────────────────────────────────────────────────────

def _fake_controller():
    """`self` mínimo: _resolve_navigate_url solo usa launcher y _ensure_url."""
    return SimpleNamespace(
        launcher=SimpleNamespace(WEB_APP_URLS=WEB_APP_URLS),
        _ensure_url=AppController._ensure_url,
    )


def _resolve(analysis):
    return AppController._resolve_navigate_url(_fake_controller(), analysis)


@pytest.mark.parametrize("target", ["discord.", "discord", "Discord.", "spotify.", "vlc."])
def test_native_apps_never_resolve_to_a_url(target):
    """El caso del bug + el guard de apps nativas."""
    assert _resolve({"target": target}) is None


def test_discord_is_native_and_not_a_web_app():
    """Si esto cambia, el guard de arriba deja de tener sentido."""
    assert "discord" in NATIVE_APPS
    assert "discord" not in WEB_APP_URLS


@pytest.mark.parametrize("target,expected", [
    ("onpe.gob.pe", "https://onpe.gob.pe"),
    ("onpe.gob.pe.", "https://onpe.gob.pe"),     # dictado, con punto final
    ("https://ejemplo.com", "https://ejemplo.com"),
])
def test_real_domains_still_resolve(target, expected):
    assert _resolve({"target": target}) == expected


def test_known_web_app_still_resolves():
    url = _resolve({"target": "youtube"})
    assert url and "youtube" in url


def test_sub_path_is_preserved():
    url = _resolve({"target": "instagram", "sub_path": "reels"})
    assert url and url.endswith("/reels")


@pytest.mark.parametrize("navigate_target", ["discord.", "discord", "spotify."])
def test_navigate_target_from_the_llm_extractor_is_guarded_too(navigate_target):
    """`navigate_target` lo llena el extractor LLM de CAPA 1.5, que copia el
    target tal como lo dictó el usuario. Antes esta rama hacía _ensure_url() a
    ciegas y era la vía más probable del bug en vivo."""
    assert _resolve({"navigate_target": navigate_target}) is None


@pytest.mark.parametrize("navigate_target,expected", [
    ("https://duckduckgo.com/?q=hola", "https://duckduckgo.com/?q=hola"),
    ("onpe.gob.pe", "https://onpe.gob.pe"),
])
def test_navigate_target_with_a_real_url_still_navigates(navigate_target, expected):
    """Las URLs pre-armadas por el router (búsquedas) no se rompen."""
    assert _resolve({"navigate_target": navigate_target}) == expected


def test_search_query_domain_is_cleaned_too():
    assert _resolve({"target": "", "search_query": "ejemplo.com."}) == "https://ejemplo.com"


def test_garbage_target_resolves_to_nothing():
    assert _resolve({"target": "una cosa cualquiera"}) is None


# ── Fallback de open nativo ──────────────────────────────────────────────────

@pytest.mark.parametrize("target,expected", [
    ("discord.", "discord"),
    ("Discord.", "discord"),
    ("spotify!", "spotify"),
    ("vlc", "vlc"),
])
async def test_native_open_receives_a_clean_app_name(target, expected):
    """Cuando NAVIGATE no resuelve URL, el analysis cae en la rama
    `action in ("open","start")` → launcher.open_app(). Si ahí llega "discord."
    con el punto, resolve_alias() no lo reconoce y no se abre nada."""
    from unittest.mock import MagicMock

    launcher = MagicMock()
    launcher.WEB_APP_URLS = WEB_APP_URLS
    launcher.open_app = MagicMock(return_value="✓ abierto")
    ctrl = SimpleNamespace(
        launcher=launcher,
        _ensure_url=AppController._ensure_url,
        # `_execute_inner` llama al launcher a través de este puente async
        # (`asyncio.to_thread`) para no congelar el event loop: `open_app`
        # puede bloquear hasta 8 s esperando el renderer de una app Electron.
        _open_app_async=AppController._open_app_async.__get__(
            SimpleNamespace(launcher=launcher)
        ),
        _publish_app_launched=lambda *a, **kw: None,
        browser=None,
    )

    await AppController._execute_inner(ctrl, {
        "intent": "automation", "action": "open", "target": target,
    })
    launcher.open_app.assert_called_once_with(expected)
