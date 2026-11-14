"""
Tests del flujo "reproducí el álbum X de Y" (álbum por nombre vía artista).

Bug origen (sesión call-mode 2026-06-12): "Produce el álbum El Camino de The
Black Keys" buscaba "el camino the black keys" + filtro Álbumes, no encontraba
el álbum en esa lista (resultados irrelevantes) y el código clickeaba la PRIMERA
tarjeta → habría reproducido el álbum equivocado.

Fix: separar álbum/artista en el parser y, al ejecutar, ir a la página del
artista y buscar el álbum por NOMBRE (Tier 1); fallback a búsqueda con match por
nombre (Tier 2); jamás reproducir un álbum que no coincide.

Todos los tests son mockeados: NUNCA lanzan un browser real.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── 1. Parser: separación álbum / artista ─────────────────────────────────────

def test_split_album_artist_basic():
    from core.router import _split_album_artist
    album, artist = _split_album_artist("produce el álbum el camino de the black keys", "", "el álbum ")
    assert album == "el camino"
    assert artist == "the black keys"


def test_split_album_artist_uses_last_de():
    """Nombre de álbum que contiene 'de' → separa por el ÚLTIMO ' de '."""
    from core.router import _split_album_artist
    album, artist = _split_album_artist(
        "reproduce el álbum el lado oscuro de la luna de pink floyd", "", "el álbum "
    )
    assert album == "el lado oscuro de la luna"
    assert artist == "pink floyd"


def test_split_album_artist_no_separator():
    from core.router import _split_album_artist
    album, artist = _split_album_artist("reproduce el álbum el camino", "", "el álbum ")
    assert album is None and artist is None


def test_extract_media_entities_sets_album_artist():
    from core.router import _extract_media_entities
    r = _extract_media_entities("produce el álbum El Camino de The Black Keys")
    assert r is not None
    assert r["media_type"] == "album"
    assert r["album"] == "el camino"
    assert r["artist"] == "the black keys"
    # provider default sigue funcionando
    assert r["provider"] == "youtube_music"


def test_extract_media_entities_album_without_artist():
    from core.router import _extract_media_entities
    r = _extract_media_entities("reproduce el álbum harlequin en youtube music")
    assert r is not None
    assert r["media_type"] == "album"
    # Sin "de {artista}" → no se separa; album/artist None, search_query combinado
    assert r["album"] is None
    assert r["artist"] is None
    assert "harlequin" in r["search_query"]


# ── 2. Dispatch en AppController ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_routes_named_album_when_artist_present():
    from modules.automation.app_controller import AppController

    ac = AppController.__new__(AppController)  # sin __init__: no toca daemons
    ac._browser = MagicMock()
    ac._browser.play_named_album = AsyncMock(return_value="Reproduciendo el álbum 'el camino' de the black keys en YouTube Music")
    ac._browser.play_album = AsyncMock()

    analysis = {
        "intent": "automation",
        "automation_intent": "PLAY_MEDIA",
        "provider": "youtube_music",
        "media_type": "album",
        "search_query": "el camino the black keys",
        "album": "el camino",
        "artist": "the black keys",
    }
    result = await ac._execute_inner(analysis)

    ac._browser.play_named_album.assert_awaited_once_with(
        "the black keys", "el camino", fallback_query="el camino the black keys"
    )
    ac._browser.play_album.assert_not_called()
    assert "el camino" in result


@pytest.mark.asyncio
async def test_dispatch_falls_back_to_play_album_without_artist():
    from modules.automation.app_controller import AppController

    ac = AppController.__new__(AppController)
    ac._browser = MagicMock()
    ac._browser.play_named_album = AsyncMock()
    ac._browser.play_album = AsyncMock(return_value="Reproduciendo álbum 'harlequin' en YouTube Music")

    analysis = {
        "automation_intent": "PLAY_MEDIA",
        "provider": "youtube_music",
        "media_type": "album",
        "search_query": "harlequin",
        "album": None,
        "artist": None,
    }
    result = await ac._execute_inner(analysis)

    ac._browser.play_album.assert_awaited_once_with("harlequin")
    ac._browser.play_named_album.assert_not_called()


# ── 3. _play_named_album_for_artist (cascada) ─────────────────────────────────

def _make_controller():
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig
    bc = BrowserController(BrowserConfig())
    bc._ensure_browser = AsyncMock()
    bc._handle_cookie_popups = AsyncMock()
    bc._wait_video_playing = AsyncMock()
    bc._log_music_play = MagicMock()
    bc._take_debug_screenshot = AsyncMock()
    bc.page = MagicMock()
    bc.page.goto = AsyncMock()
    bc.page.wait_for_load_state = AsyncMock()
    bc.page.url = "https://music.youtube.com/watch?v=abc&list=OLAK5uy_x"
    return bc


@pytest.mark.asyncio
async def test_named_album_tier1_artist_page_match():
    """Tier 1: página del artista → álbum encontrado por nombre → reproduce."""
    from modules.automation.browser_controller import BrowserController

    bc = _make_controller()
    bc._goto_artist_page = AsyncMock(return_value=True)
    bc.page.evaluate = AsyncMock(return_value="https://music.youtube.com/browse/MPREb_elcamino")
    bc._start_album_playback = AsyncMock(return_value=True)
    bc._play_yt_music_album = AsyncMock()  # no debe llamarse

    with patch("modules.automation.browser_controller.asyncio.sleep", new=AsyncMock()):
        result = await bc._play_named_album_for_artist(
            "the black keys", "el camino", "el camino the black keys"
        )

    bc._goto_artist_page.assert_awaited_once_with("the black keys")
    bc._start_album_playback.assert_awaited_once()
    bc._play_yt_music_album.assert_not_called()
    # Navegó al álbum matcheado
    assert bc.page.goto.await_args.args[0] == "https://music.youtube.com/browse/MPREb_elcamino"
    assert "el camino" in result and "the black keys" in result


@pytest.mark.asyncio
async def test_named_album_tier2_when_album_not_in_discography():
    """Tier 1 no halla el álbum en la discografía → fallback a búsqueda."""
    bc = _make_controller()
    bc._goto_artist_page = AsyncMock(return_value=True)
    bc.page.evaluate = AsyncMock(return_value=None)  # no match en discografía
    bc._start_album_playback = AsyncMock()
    bc._play_yt_music_album = AsyncMock(return_value="Reproduciendo álbum 'el camino' en YouTube Music")

    with patch("modules.automation.browser_controller.asyncio.sleep", new=AsyncMock()):
        result = await bc._play_named_album_for_artist(
            "the black keys", "el camino", "el camino the black keys"
        )

    bc._play_yt_music_album.assert_awaited_once_with(
        "el camino the black keys", match_title="el camino"
    )
    bc._start_album_playback.assert_not_called()
    assert "el camino" in result


@pytest.mark.asyncio
async def test_named_album_tier2_when_artist_page_not_found():
    """No se halla la página del artista → fallback a búsqueda con match."""
    bc = _make_controller()
    bc._goto_artist_page = AsyncMock(return_value=False)
    bc.page.evaluate = AsyncMock()
    bc._play_yt_music_album = AsyncMock(return_value="Reproduciendo álbum 'el camino' en YouTube Music")

    with patch("modules.automation.browser_controller.asyncio.sleep", new=AsyncMock()):
        await bc._play_named_album_for_artist("the black keys", "el camino", None)

    # fallback_query None → usa "{album} {artist}"
    bc._play_yt_music_album.assert_awaited_once_with(
        "el camino the black keys", match_title="el camino"
    )
    bc.page.evaluate.assert_not_called()


# ── 4. _play_yt_music_album con match_title ───────────────────────────────────

@pytest.mark.asyncio
async def test_play_album_match_title_navigates_to_matched_card():
    bc = _make_controller()
    bc._click_first_visible = AsyncMock(return_value=MagicMock())  # chip
    bc.page.wait_for_selector = AsyncMock()
    bc.page.evaluate = AsyncMock(return_value="https://music.youtube.com/browse/MPRE_elcamino")
    bc._start_album_playback = AsyncMock(return_value=True)

    with patch("modules.automation.browser_controller.asyncio.sleep", new=AsyncMock()):
        result = await bc._play_yt_music_album("el camino the black keys", match_title="el camino")

    # Navegó a la tarjeta matcheada, no clickeó album_card a ciegas
    assert bc.page.goto.await_args.args[0] == "https://music.youtube.com/browse/MPRE_elcamino"
    bc._start_album_playback.assert_awaited_once()
    assert "el camino" in result


@pytest.mark.asyncio
async def test_play_album_match_title_no_match_returns_honest_failure():
    bc = _make_controller()
    bc._click_first_visible = AsyncMock(return_value=MagicMock())  # chip
    bc.page.wait_for_selector = AsyncMock()
    bc.page.evaluate = AsyncMock(return_value=None)  # álbum no está en resultados
    bc._start_album_playback = AsyncMock()

    with patch("modules.automation.browser_controller.asyncio.sleep", new=AsyncMock()):
        result = await bc._play_yt_music_album("inexistente xyz", match_title="inexistente")

    assert "No encontré el álbum" in result
    bc._start_album_playback.assert_not_called()


@pytest.mark.asyncio
async def test_play_album_legacy_first_card_preserved():
    """Sin match_title se conserva el comportamiento de 'primera tarjeta'."""
    bc = _make_controller()
    # chip + album_card → ambos via _click_first_visible
    bc._click_first_visible = AsyncMock(return_value=MagicMock())
    bc.page.wait_for_selector = AsyncMock()
    bc.page.wait_for_url = AsyncMock()
    bc.page.evaluate = AsyncMock()  # no debe usarse para match en este path
    bc._start_album_playback = AsyncMock(return_value=True)

    with patch("modules.automation.browser_controller.asyncio.sleep", new=AsyncMock()):
        result = await bc._play_yt_music_album("the black keys álbum")

    # Se usó el path de tarjeta (chip + card = 2 llamadas a _click_first_visible)
    assert bc._click_first_visible.await_count == 2
    bc._start_album_playback.assert_awaited_once()
    assert "Reproduciendo álbum" in result
