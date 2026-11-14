"""Verifica que youtube music sea reconocido como sitio web."""

import pytest


def test_web_app_urls_has_youtube_music():
    """WEB_APP_URLS debe contener las variantes de YouTube Music."""
    from core.app_config import WEB_APP_URLS
    variants = ["youtube music", "youtube_music", "yt music", "music youtube"]
    for v in variants:
        assert v in WEB_APP_URLS, f"Falta '{v}' en WEB_APP_URLS"
        assert WEB_APP_URLS[v] == "https://music.youtube.com", \
            f"URL incorrecta para '{v}': {WEB_APP_URLS.get(v)}"


def test_site_keywords_has_youtube_music():
    """SITE_KEYWORDS debe incluir youtube music."""
    from core.app_config import SITE_KEYWORDS
    assert "youtube music" in SITE_KEYWORDS, \
        f"Falta 'youtube music' en SITE_KEYWORDS: {SITE_KEYWORDS}"
    assert "youtube_music" in SITE_KEYWORDS, \
        f"Falta 'youtube_music' en SITE_KEYWORDS: {SITE_KEYWORDS}"


def test_media_search_urls_has_youtube_music():
    """_MEDIA_SEARCH_URLS ya lo tiene — verificar que no se rompió."""
    from core.router import _MEDIA_SEARCH_URLS
    assert "youtube_music" in _MEDIA_SEARCH_URLS
    assert "music.youtube.com" in _MEDIA_SEARCH_URLS["youtube_music"]


def test_auto_verbs_source():
    """AUTO_VERBS existe y contiene verbos clave."""
    from core.app_config import AUTO_VERBS, AUTO_VERBS_PATTERN
    assert len(AUTO_VERBS) >= 30, f"AUTO_VERBS tiene {len(AUTO_VERBS)}, esperado >= 30"
    assert "abra" in AUTO_VERBS
    assert "lance" in AUTO_VERBS
    assert "ejecute" in AUTO_VERBS
    assert "inicie" in AUTO_VERBS
    assert "busque" in AUTO_VERBS
    assert "reproduce" in AUTO_VERBS
    assert "open" in AUTO_VERBS
    assert AUTO_VERBS_PATTERN.startswith("(?:"), \
        f"AUTO_VERBS_PATTERN debería ser un grupo no capturador: {AUTO_VERBS_PATTERN[:20]}"


def test_native_apps_sync_with_supported():
    """NATIVE_APPS debe estar sincronizado con self_state."""
    from core.app_config import NATIVE_APPS
    assert len(NATIVE_APPS) >= 14, f"NATIVE_APPS tiene {len(NATIVE_APPS)} apps"
    native_set = set(NATIVE_APPS)
    key_apps = ["firefox", "chrome", "discord", "spotify", "terminal"]
    missing = [a for a in key_apps if a not in native_set]
    assert not missing, f"Apps clave faltantes en NATIVE_APPS: {missing}"
