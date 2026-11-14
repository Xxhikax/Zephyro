"""
Tests unitarios para las extensiones de YouTube Music:
play_artist / play_album / play_recommendations + clasificación de media_type.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------
# Router: detección de media_type
# ---------------------------------------------------------------------

def test_router_detects_song_default():
    from core.router import _extract_media_entities
    out = _extract_media_entities("reproduce Gold in the Ceiling en youtube music")
    assert out is not None
    assert out["provider"] == "youtube_music"
    assert out["media_type"] == "song"
    assert "gold" in out["search_query"]


def test_router_detects_album():
    from core.router import _extract_media_entities
    out = _extract_media_entities("pon el álbum El Camino en youtube music")
    assert out is not None
    assert out["media_type"] == "album"
    assert "el camino" in out["search_query"]


def test_router_detects_artist():
    from core.router import _extract_media_entities
    out = _extract_media_entities("pon música de The Black Keys en youtube music")
    assert out is not None
    assert out["media_type"] == "artist"
    assert "black keys" in out["search_query"].lower()


def test_router_detects_recommendation_no_seed():
    from core.router import _extract_media_entities
    out = _extract_media_entities("recomiéndame música en youtube music")
    assert out is not None
    assert out["media_type"] == "recommendation"


def test_router_detects_recommendation_with_seed():
    from core.router import _extract_media_entities
    out = _extract_media_entities("pon música de Black Keys en youtube music")
    # "música de X" matchea artist (orden de patterns), no recommendation
    assert out is not None
    assert out["media_type"] == "artist"


def test_router_detects_pon_algo_recommendation():
    from core.router import _extract_media_entities
    out = _extract_media_entities("pon algo en youtube music")
    assert out is not None
    assert out["media_type"] == "recommendation"


# ---------------------------------------------------------------------
# BrowserController: _log_music_play
# ---------------------------------------------------------------------

def _make_bc(user_profile=None):
    from modules.automation.browser_controller import BrowserController
    from core.browser_config import BrowserConfig
    return BrowserController(BrowserConfig(), user_profile=user_profile)


def test_log_music_play_calls_user_profile_add_interest():
    profile = MagicMock()
    bc = _make_bc(user_profile=profile)
    bc._log_music_play("artist", "The Black Keys")
    profile.add_interest.assert_called_once_with("música:artist:the black keys")


def test_log_music_play_silent_without_profile():
    bc = _make_bc(user_profile=None)
    # No debe lanzar
    bc._log_music_play("album", "El Camino")


def test_log_music_play_silent_when_profile_raises():
    profile = MagicMock()
    profile.add_interest.side_effect = RuntimeError("db down")
    bc = _make_bc(user_profile=profile)
    bc._log_music_play("artist", "X")  # no propaga


# ---------------------------------------------------------------------
# BrowserController: flujo de play_artist / play_album / play_recommendations
# ---------------------------------------------------------------------

@pytest.fixture
def bc_with_mocked_page():
    """BrowserController con page mockeada y _ensure_browser anulado."""
    bc = _make_bc(user_profile=MagicMock())
    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.wait_for_load_state = AsyncMock()
    fake_page.wait_for_url = AsyncMock()
    fake_page.wait_for_selector = AsyncMock()
    fake_page.evaluate = AsyncMock(return_value={"paused": False, "ready": 4})
    fake_page.keyboard = MagicMock()
    fake_page.keyboard.press = AsyncMock()
    bc.page = fake_page
    return bc


@pytest.mark.asyncio
async def test_play_yt_music_artist_navigates_search_then_clicks(bc_with_mocked_page):
    bc = bc_with_mocked_page
    # Mock locator que devuelve un href válido y permite click
    fake_loc = MagicMock()
    fake_loc.wait_for = AsyncMock()
    fake_loc.get_attribute = AsyncMock(return_value="channel/UCxyz")
    fake_loc.scroll_into_view_if_needed = AsyncMock()
    fake_loc.click = AsyncMock()
    fake_locator = MagicMock(); fake_locator.first = fake_loc
    bc.page.locator = MagicMock(return_value=fake_locator)

    with patch.object(bc, "_ensure_browser", new=AsyncMock()), \
         patch.object(bc, "_handle_cookie_popups", new=AsyncMock()), \
         patch.object(bc, "_click_yt_music_play_main", new=AsyncMock(return_value=True)), \
         patch.object(bc, "_click_yt_music_shuffle", new=AsyncMock(return_value=True)) as shuf_mock, \
         patch.object(bc, "_wait_video_playing", new=AsyncMock(return_value=True)):
        result = await bc._play_yt_music_artist("The Black Keys", mode="shuffle")

    assert "Black Keys" in result
    bc.page.goto.assert_awaited()
    url_called = bc.page.goto.await_args.args[0]
    assert "music.youtube.com/search?q=" in url_called
    fake_loc.click.assert_awaited()  # se clicó la artist card
    shuf_mock.assert_awaited()  # mode=shuffle
    bc.user_profile.add_interest.assert_called_with("música:artist:the black keys")


@pytest.mark.asyncio
async def test_play_yt_music_album_filters_albums(bc_with_mocked_page):
    bc = bc_with_mocked_page
    with patch.object(bc, "_ensure_browser", new=AsyncMock()), \
         patch.object(bc, "_handle_cookie_popups", new=AsyncMock()), \
         patch.object(bc, "_click_first_visible", new=AsyncMock(return_value=MagicMock())) as click_mock, \
         patch.object(bc, "_click_yt_music_play_main", new=AsyncMock(return_value=True)) as play_mock, \
         patch.object(bc, "_wait_video_playing", new=AsyncMock(return_value=True)):
        result = await bc._play_yt_music_album("El Camino")

    assert "El Camino" in result
    play_mock.assert_awaited()
    url_called = bc.page.goto.await_args.args[0]
    assert "music.youtube.com/search?q=" in url_called
    bc.user_profile.add_interest.assert_called_with("música:album:el camino")


@pytest.mark.asyncio
async def test_play_yt_music_recommendations_with_seed_delegates_to_artist(bc_with_mocked_page):
    bc = bc_with_mocked_page
    with patch.object(bc, "_play_yt_music_artist", new=AsyncMock(return_value="OK")) as artist_mock:
        result = await bc._play_yt_music_recommendations(seed="Black Keys")
    artist_mock.assert_awaited_once_with("Black Keys", mode="shuffle")
    assert result == "OK"


@pytest.mark.asyncio
async def test_play_yt_music_recommendations_no_seed_goes_home(bc_with_mocked_page):
    bc = bc_with_mocked_page
    with patch.object(bc, "_ensure_browser", new=AsyncMock()), \
         patch.object(bc, "_handle_cookie_popups", new=AsyncMock()), \
         patch.object(bc, "_click_first_visible", new=AsyncMock(return_value=MagicMock())), \
         patch.object(bc, "_click_yt_music_play_main", new=AsyncMock(return_value=True)), \
         patch.object(bc, "_wait_video_playing", new=AsyncMock(return_value=True)):
        result = await bc._play_yt_music_recommendations(seed=None)

    url_called = bc.page.goto.await_args.args[0]
    assert url_called.rstrip("/") == "https://music.youtube.com"
    assert "recomendaciones" in result.lower()
    bc.user_profile.add_interest.assert_called_with("música:mix:yt_music_home")


# ---------------------------------------------------------------------
# Router: búscame / busca
# ---------------------------------------------------------------------

def test_router_buscame_album_with_artist():
    from core.router import _extract_media_entities
    out = _extract_media_entities("búscame el álbum El Camino de The Black Keys en youtube music")
    assert out is not None
    assert out["media_type"] == "album"
    assert out["provider"] == "youtube_music"
    sq = out["search_query"].lower()
    assert "camino" in sq, f"search_query debe contener 'camino', got {sq!r}"
    # El artista también puede quedar en el query (es útil para la búsqueda)
    assert "black keys" in sq or "camino" in sq


def test_router_buscame_album_strips_buscame():
    from core.router import _extract_media_entities
    out = _extract_media_entities("búscame el álbum Rumours de Fleetwood Mac en youtube music")
    assert out is not None
    assert out["media_type"] == "album"
    sq = out["search_query"].lower()
    # "búscame" no debe aparecer en el search_query
    assert "búscame" not in sq and "buscame" not in sq
    assert "rumours" in sq


# ---------------------------------------------------------------------
# BrowserController: _play_best_album_for_artist
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_play_best_album_for_artist_navigates_to_artist_then_album(bc_with_mocked_page):
    bc = bc_with_mocked_page

    # Simular tarjeta de artista
    fake_artist_loc = MagicMock()
    fake_artist_loc.wait_for = AsyncMock()
    fake_artist_loc.get_attribute = AsyncMock(return_value="channel/UCxyz")
    fake_artist_loc.scroll_into_view_if_needed = AsyncMock()
    fake_artist_loc.click = AsyncMock()
    fake_artist_locator = MagicMock()
    fake_artist_locator.first = fake_artist_loc

    # Simular tarjeta de álbum (MPRE)
    fake_album_loc = MagicMock()
    fake_album_loc.wait_for = AsyncMock()
    fake_album_loc.get_attribute = AsyncMock(return_value="browse/MPREb_xyz")
    fake_album_loc.scroll_into_view_if_needed = AsyncMock()
    fake_album_loc.click = AsyncMock()
    fake_album_locator = MagicMock()
    fake_album_locator.first = fake_album_loc

    call_count = {"n": 0}

    def locator_side_effect(sel):
        call_count["n"] += 1
        if "channel/UC" in sel:
            return fake_artist_locator
        if "browse/MPRE" in sel:
            return fake_album_locator
        return fake_artist_locator

    bc.page.locator = MagicMock(side_effect=locator_side_effect)

    with patch.object(bc, "_ensure_browser", new=AsyncMock()), \
         patch.object(bc, "_handle_cookie_popups", new=AsyncMock()), \
         patch.object(bc, "_click_yt_music_play_main", new=AsyncMock(return_value=True)), \
         patch.object(bc, "_wait_video_playing", new=AsyncMock(return_value=True)):
        result = await bc._play_best_album_for_artist("The Black Keys")

    assert "Black Keys" in result or "popular" in result.lower()
    bc.page.goto.assert_awaited()
    url_called = bc.page.goto.await_args_list[0].args[0]
    assert "music.youtube.com/search?q=" in url_called
    bc.user_profile.add_interest.assert_called_with("música:album_best:the black keys")
