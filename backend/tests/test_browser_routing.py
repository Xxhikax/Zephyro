#!/usr/bin/env python3
"""
Test de routing de navegador: verifica que los comandos de browser/webapp
se enrutan correctamente al Firefox nativo (no a Playwright).

Escenarios probados:
  1. "abre firefox"       → AppLauncher (nativo), sin automation_intent
  2. "abre youtube"       → NAVIGATE → open_url_in_browser (nativo)
  3. "abre instagram"     → NAVIGATE → open_url_in_browser (nativo)
  4. "busca gatos en youtube" → SEARCH → URL nativa en Firefox
  5. "reproduce lofi en youtube" → PLAY_MEDIA → YouTube search URL en Firefox
  6. "abre firefox y entra a youtube" → NAVIGATE con navigate_target
"""
import sys
import os
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

# Setup path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.router import IntentRouter
from modules.automation.app_launcher import AppLauncher
from modules.automation.app_controller import AppController

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
INFO = "\033[94mℹ\033[0m"
results = []


def check(name: str, condition: bool, detail: str = ""):
    tag = PASS if condition else FAIL
    msg = f"  {tag} {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append(condition)
    return condition


# ─── Mocks ───────────────────────────────────────────────────────────────────

def make_router() -> IntentRouter:
    """Crea un router con LLM mockeado (solo usamos CAPA 0/1 heurística)."""
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent":"information"}')
    mock_context = MagicMock()
    mock_context.get_last_automation = MagicMock(return_value=None)
    mock_context.get_average_latency = MagicMock(return_value=0.5)
    router = IntentRouter(mock_llm, mock_context)
    return router


def make_router_with_browser_context() -> IntentRouter:
    """Router con contexto previo de 'firefox' abierto."""
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent":"information"}')
    mock_context = MagicMock()
    mock_context.get_last_automation = MagicMock(return_value={
        "analysis": {"target": "firefox", "intent": "automation"}
    })
    mock_context.get_average_latency = MagicMock(return_value=0.5)
    router = IntentRouter(mock_llm, mock_context)
    return router


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: Router - "abre firefox"
# ═══════════════════════════════════════════════════════════════════════════════
async def test_router_abre_firefox():
    print("\n═══ TEST 1: Router → 'abre firefox' ═══")
    router = make_router()
    analysis = await router.classify_and_parse("abre firefox")

    check("intent = automation", analysis.get("intent") == "automation",
          f"got: {analysis.get('intent')}")
    check("automation_intent = None (nativo)", analysis.get("automation_intent") is None,
          f"got: {analysis.get('automation_intent')}")
    check("target = firefox", analysis.get("target") == "firefox",
          f"got: {analysis.get('target')}")
    check("action = open", analysis.get("action") == "open",
          f"got: {analysis.get('action')}")
    print(f"  {INFO} Análisis completo: {analysis}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: Router - "abre youtube"
# ═══════════════════════════════════════════════════════════════════════════════
async def test_router_abre_youtube():
    print("\n═══ TEST 2: Router → 'abre youtube' ═══")
    router = make_router()
    analysis = await router.classify_and_parse("abre youtube")

    check("intent = automation", analysis.get("intent") == "automation",
          f"got: {analysis.get('intent')}")
    check("automation_intent = NAVIGATE", analysis.get("automation_intent") == "NAVIGATE",
          f"got: {analysis.get('automation_intent')}")
    check("target = youtube", analysis.get("target") == "youtube",
          f"got: {analysis.get('target')}")
    print(f"  {INFO} Análisis completo: {analysis}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Router - "abre instagram"
# ═══════════════════════════════════════════════════════════════════════════════
async def test_router_abre_instagram():
    print("\n═══ TEST 3: Router → 'abre instagram' ═══")
    router = make_router()
    analysis = await router.classify_and_parse("abre instagram")

    check("intent = automation", analysis.get("intent") == "automation",
          f"got: {analysis.get('intent')}")
    check("automation_intent = NAVIGATE", analysis.get("automation_intent") == "NAVIGATE",
          f"got: {analysis.get('automation_intent')}")
    check("target = instagram", analysis.get("target") == "instagram",
          f"got: {analysis.get('target')}")
    print(f"  {INFO} Análisis completo: {analysis}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: Router - "busca gatos en youtube"
# ═══════════════════════════════════════════════════════════════════════════════
async def test_router_busca_youtube():
    print("\n═══ TEST 4: Router → 'busca gatos en youtube' ═══")
    router = make_router()
    analysis = await router.classify_and_parse("busca gatos en youtube")

    check("intent = automation", analysis.get("intent") == "automation",
          f"got: {analysis.get('intent')}")
    check("automation_intent = SEARCH", analysis.get("automation_intent") == "SEARCH",
          f"got: {analysis.get('automation_intent')}")
    print(f"  {INFO} Análisis completo: {analysis}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Router - "reproduce lofi en youtube"
# ═══════════════════════════════════════════════════════════════════════════════
async def test_router_reproduce_youtube():
    print("\n═══ TEST 5: Router → 'reproduce lofi en youtube' ═══")
    router = make_router()
    analysis = await router.classify_and_parse("reproduce lofi en youtube")

    check("intent = automation", analysis.get("intent") == "automation",
          f"got: {analysis.get('intent')}")
    check("automation_intent = PLAY_MEDIA", analysis.get("automation_intent") == "PLAY_MEDIA",
          f"got: {analysis.get('automation_intent')}")
    check("provider = youtube", analysis.get("provider") == "youtube",
          f"got: {analysis.get('provider')}")
    print(f"  {INFO} Análisis completo: {analysis}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: Router - "abre firefox y entra a youtube"
# ═══════════════════════════════════════════════════════════════════════════════
async def test_router_firefox_y_youtube():
    print("\n═══ TEST 6: Router → 'abre firefox y entra a youtube' ═══")
    router = make_router()
    analysis = await router.classify_and_parse("abre firefox y entra a youtube")

    check("intent = automation", analysis.get("intent") == "automation",
          f"got: {analysis.get('intent')}")
    check("automation_intent = NAVIGATE", analysis.get("automation_intent") == "NAVIGATE",
          f"got: {analysis.get('automation_intent')}")
    check("target = firefox", analysis.get("target") == "firefox",
          f"got: {analysis.get('target')}")
    has_nav_target = analysis.get("navigate_target") is not None
    has_search_query = analysis.get("search_query") is not None
    check("tiene navigate_target o search_query (youtube)",
          has_nav_target or has_search_query,
          f"navigate_target={analysis.get('navigate_target')}, search_query={analysis.get('search_query')}")
    print(f"  {INFO} Análisis completo: {analysis}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 7: AppController.execute - "abre firefox" → usa launcher.open_app
# ═══════════════════════════════════════════════════════════════════════════════
def test_controller_abre_firefox():
    print("\n═══ TEST 7: AppController.execute → 'abre firefox' (nativo) ═══")
    analysis = {
        "intent": "automation",
        "automation_intent": None,
        "action": "open",
        "target": "firefox",
    }

    controller = AppController.__new__(AppController)
    controller.launcher = MagicMock()
    controller.launcher.open_app = MagicMock(return_value="✓ Firefox abierto")
    controller._browser = MagicMock()
    controller.planner = MagicMock()

    result = asyncio.get_event_loop().run_until_complete(controller.execute(analysis))

    check("Llamó launcher.open_app", controller.launcher.open_app.called,
          f"called={controller.launcher.open_app.called}")
    check("NO llamó browser (Playwright)", not controller._browser.navigate_to.called,
          "browser.navigate_to should not be called")
    check("Resultado contiene ✓", "✓" in result, f"got: {result}")
    print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 8: AppController.execute - "abre youtube" → NAVIGATE → open_url_in_browser
# ═══════════════════════════════════════════════════════════════════════════════
def test_controller_abre_youtube():
    print("\n═══ TEST 8: AppController.execute → 'abre youtube' (nativo) ═══")
    analysis = {
        "intent": "automation",
        "automation_intent": "NAVIGATE",
        "action": "open",
        "target": "youtube",
    }

    controller = AppController.__new__(AppController)
    controller.launcher = MagicMock(spec=AppLauncher)
    controller.launcher.WEB_APP_URLS = AppLauncher.WEB_APP_URLS
    controller.launcher.open_url_in_browser = MagicMock(
        return_value="✓ Abriendo youtube.com en nueva pestaña de firefox"
    )
    controller._browser = MagicMock()
    controller.planner = MagicMock()

    result = asyncio.get_event_loop().run_until_complete(controller.execute(analysis))

    check("Llamó launcher.open_url_in_browser", controller.launcher.open_url_in_browser.called,
          f"called={controller.launcher.open_url_in_browser.called}")
    if controller.launcher.open_url_in_browser.called:
        url_arg = controller.launcher.open_url_in_browser.call_args[0][0]
        check("URL = https://youtube.com", url_arg == "https://youtube.com",
              f"got: {url_arg}")
    check("NO lanzó Playwright", not controller._browser.navigate_to.called,
          "browser.navigate_to should not be called")
    print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 9: AppLauncher._open_app_direct("firefox") → _open_browser_direct
# ═══════════════════════════════════════════════════════════════════════════════
def test_launcher_open_firefox_direct():
    print("\n═══ TEST 9: AppLauncher._open_app_direct('firefox') ═══")
    launcher = AppLauncher.__new__(AppLauncher)
    launcher.daemon_available = False
    launcher.is_wayland = False

    with patch.object(launcher, '_open_browser_direct', return_value="✓ Abriendo firefox") as mock_browser, \
         patch.object(launcher, '_open_discord_direct') as mock_discord:

        result = launcher._open_app_direct("firefox")

        check("Llamó _open_browser_direct", mock_browser.called,
              f"called={mock_browser.called}")
        check("NO llamó _open_discord_direct", not mock_discord.called)
        check("Resultado contiene ✓", "✓" in result, f"got: {result}")
        print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 10: AppLauncher._open_app_direct("youtube") → open_url_in_browser
# ═══════════════════════════════════════════════════════════════════════════════
def test_launcher_open_youtube_direct():
    print("\n═══ TEST 10: AppLauncher._open_app_direct('youtube') ═══")
    launcher = AppLauncher.__new__(AppLauncher)
    launcher.daemon_available = False
    launcher.is_wayland = False
    launcher.WEB_APP_URLS = AppLauncher.WEB_APP_URLS

    with patch.object(launcher, 'open_url_in_browser',
                      return_value="✓ Abriendo youtube.com en nueva pestaña") as mock_url:

        result = launcher._open_app_direct("youtube")

        check("Llamó open_url_in_browser", mock_url.called,
              f"called={mock_url.called}")
        if mock_url.called:
            url_arg = mock_url.call_args[0][0]
            check("URL = https://youtube.com", url_arg == "https://youtube.com",
                  f"got: {url_arg}")
        print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 11: AppLauncher._open_browser_direct - Firefox no corriendo
# ═══════════════════════════════════════════════════════════════════════════════
def test_browser_direct_not_running():
    print("\n═══ TEST 11: _open_browser_direct (Firefox NO corriendo) ═══")
    launcher = AppLauncher.__new__(AppLauncher)

    with patch('shutil.which', return_value="/usr/bin/firefox"), \
         patch.object(launcher, 'is_browser_running', return_value=False), \
         patch('subprocess.Popen') as mock_popen:

        result = launcher._open_browser_direct("firefox")

        check("Llamó subprocess.Popen", mock_popen.called)
        if mock_popen.called:
            cmd = mock_popen.call_args[0][0]
            check("Comando = ['/usr/bin/firefox'] (sin URL → homepage)",
                  cmd == ["/usr/bin/firefox"],
                  f"got: {cmd}")
        check("Resultado = 'Abriendo firefox'", "Abriendo firefox" in result,
              f"got: {result}")
        print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 12: AppLauncher._open_browser_direct - Firefox ya corriendo
# ═══════════════════════════════════════════════════════════════════════════════
def test_browser_direct_already_running():
    print("\n═══ TEST 12: _open_browser_direct (Firefox YA corriendo) ═══")
    launcher = AppLauncher.__new__(AppLauncher)

    with patch('shutil.which', return_value="/usr/bin/firefox"), \
         patch.object(launcher, 'is_browser_running', return_value=True), \
         patch('subprocess.Popen') as mock_popen:

        result = launcher._open_browser_direct("firefox")

        check("Llamó subprocess.Popen", mock_popen.called)
        if mock_popen.called:
            cmd = mock_popen.call_args[0][0]
            check("Comando = ['/usr/bin/firefox', 'about:newtab']",
                  cmd == ["/usr/bin/firefox", "about:newtab"],
                  f"got: {cmd}")
        check("Resultado = 'Nueva pestaña'", "pestaña" in result.lower(),
              f"got: {result}")
        print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 13: AppLauncher.open_url_in_browser - Firefox ya corriendo
# ═══════════════════════════════════════════════════════════════════════════════
def test_open_url_already_running():
    print("\n═══ TEST 13: open_url_in_browser('https://youtube.com') - Firefox corriendo ═══")
    launcher = AppLauncher.__new__(AppLauncher)

    with patch('shutil.which', return_value="/usr/bin/firefox"), \
         patch.object(launcher, 'is_browser_running', return_value=True), \
         patch('subprocess.Popen') as mock_popen:

        result = launcher.open_url_in_browser("https://youtube.com")

        check("Llamó subprocess.Popen", mock_popen.called)
        if mock_popen.called:
            cmd = mock_popen.call_args[0][0]
            check("Comando = ['/usr/bin/firefox', 'https://youtube.com']",
                  cmd == ["/usr/bin/firefox", "https://youtube.com"],
                  f"got: {cmd}")
        check("Resultado menciona 'nueva pestaña'", "nueva pestaña" in result.lower(),
              f"got: {result}")
        print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 14: Full E2E - "abre youtube" sin daemon
# ═══════════════════════════════════════════════════════════════════════════════
async def test_e2e_abre_youtube():
    print("\n═══ TEST 14: E2E 'abre youtube' (sin daemon, Firefox corriendo) ═══")
    router = make_router()
    analysis = await router.classify_and_parse("abre youtube")

    # Create a real AppController with mocked subprocess
    with patch('subprocess.Popen') as mock_popen, \
         patch('shutil.which', return_value="/usr/bin/firefox"), \
         patch('subprocess.run') as mock_run:

        # Mock pgrep to say firefox IS running
        mock_run.return_value = MagicMock(returncode=0)

        launcher = AppLauncher.__new__(AppLauncher)
        launcher.daemon_available = False
        launcher.is_wayland = False
        launcher.WEB_APP_URLS = AppLauncher.WEB_APP_URLS

        controller = AppController.__new__(AppController)
        controller.launcher = launcher
        controller._browser = MagicMock()
        controller.planner = MagicMock()

        result = asyncio.get_event_loop().run_until_complete(
            controller.execute(analysis)
        )

        check("Popen fue llamado (abrió algo)", mock_popen.called)
        if mock_popen.called:
            cmd = mock_popen.call_args[0][0]
            check("Usó firefox nativo", cmd[0] == "/usr/bin/firefox",
                  f"got: {cmd}")
            check("URL = https://youtube.com", "youtube.com" in str(cmd),
                  f"got: {cmd}")
        check("NO lanzó Playwright", not controller._browser.navigate_to.called)
        check("Resultado OK", "✓" in result, f"got: {result}")
        print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 15: AppController.execute - PLAY_MEDIA → yt-dlp resolves direct video URL
# ═══════════════════════════════════════════════════════════════════════════════
def test_controller_play_media_youtube():
    print("\n═══ TEST 15: AppController.execute → PLAY_MEDIA youtube (yt-dlp) ═══")
    analysis = {
        "intent": "automation",
        "automation_intent": "PLAY_MEDIA",
        "provider": "youtube",
        "action": "play",
        "target": "gold in the ceiling",
        "search_query": "gold in the ceiling",
    }

    controller = AppController.__new__(AppController)
    controller.launcher = MagicMock(spec=AppLauncher)
    controller.launcher.open_url_in_browser = MagicMock(
        return_value="✓ Abriendo youtube.com en nueva pestaña de firefox"
    )
    controller._browser = MagicMock()
    controller.planner = MagicMock()
    # Mock yt-dlp to return a video ID
    controller._ytdlp_resolve_youtube = AsyncMock(return_value="https://www.youtube.com/watch?v=eEhMmaqGKL0")

    result = asyncio.get_event_loop().run_until_complete(controller.execute(analysis))

    check("Llamó launcher.open_url_in_browser", controller.launcher.open_url_in_browser.called,
          f"called={controller.launcher.open_url_in_browser.called}")
    if controller.launcher.open_url_in_browser.called:
        url_arg = controller.launcher.open_url_in_browser.call_args[0][0]
        check("URL es video directo (/watch?v=)", "/watch?v=" in url_arg,
              f"got: {url_arg}")
    check("NO lanzó Playwright", not controller._browser.navigate_to.called)
    print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 16: AppController.execute - SEARCH → native Firefox con search URL
# ═══════════════════════════════════════════════════════════════════════════════
def test_controller_search_native():
    print("\n═══ TEST 16: AppController.execute → SEARCH (nativo) ═══")
    analysis = {
        "intent": "automation",
        "automation_intent": "SEARCH",
        "provider": "generic",
        "action": "open",
        "target": "tutorial python",
        "search_query": "tutorial python",
    }

    controller = AppController.__new__(AppController)
    controller.launcher = MagicMock(spec=AppLauncher)
    controller.launcher.open_url_in_browser = MagicMock(
        return_value="✓ Abriendo duckduckgo.com en nueva pestaña de firefox"
    )
    controller._browser = MagicMock()
    controller.planner = MagicMock()

    result = asyncio.get_event_loop().run_until_complete(controller.execute(analysis))

    check("Llamó launcher.open_url_in_browser", controller.launcher.open_url_in_browser.called,
          f"called={controller.launcher.open_url_in_browser.called}")
    if controller.launcher.open_url_in_browser.called:
        url_arg = controller.launcher.open_url_in_browser.call_args[0][0]
        check("URL contiene duckduckgo", "duckduckgo.com" in url_arg,
              f"got: {url_arg}")
        check("URL contiene query", "tutorial" in url_arg and "python" in url_arg,
              f"got: {url_arg}")
    check("NO lanzó Playwright", not controller._browser.navigate_to.called)
    print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 17: E2E - "reproduce gold in the ceiling en youtube" (Firefox corriendo)
# ═══════════════════════════════════════════════════════════════════════════════
async def test_e2e_play_media_youtube():
    print("\n═══ TEST 17: E2E 'reproduce gold in the ceiling en youtube' ═══")
    router = make_router()
    analysis = await router.classify_and_parse("reproduce gold in the ceiling en youtube")

    check("automation_intent = PLAY_MEDIA", analysis.get("automation_intent") == "PLAY_MEDIA",
          f"got: {analysis.get('automation_intent')}")
    check("provider = youtube", analysis.get("provider") == "youtube",
          f"got: {analysis.get('provider')}")

    with patch('subprocess.Popen') as mock_popen, \
         patch('shutil.which', return_value="/usr/bin/firefox"), \
         patch('subprocess.run') as mock_run:

        mock_run.return_value = MagicMock(returncode=0)  # Firefox running

        launcher = AppLauncher.__new__(AppLauncher)
        launcher.daemon_available = False
        launcher.is_wayland = False
        launcher.WEB_APP_URLS = AppLauncher.WEB_APP_URLS

        controller = AppController.__new__(AppController)
        controller.launcher = launcher
        controller._browser = MagicMock()
        controller.planner = MagicMock()
        # Mock yt-dlp → returns a direct video URL
        controller._ytdlp_resolve_youtube = AsyncMock(return_value="https://www.youtube.com/watch?v=abc123xyz")

        result = asyncio.get_event_loop().run_until_complete(
            controller.execute(analysis)
        )

        check("Popen fue llamado", mock_popen.called)
        if mock_popen.called:
            cmd = mock_popen.call_args[0][0]
            check("Usó firefox nativo", cmd[0] == "/usr/bin/firefox",
                  f"got: {cmd}")
            check("URL es video directo (/watch?v=)", "/watch?v=" in str(cmd),
                  f"got: {cmd}")
        check("NO lanzó Playwright", not controller._browser.navigate_to.called)
        check("Resultado OK", "✓" in result, f"got: {result}")
        print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 18: _build_play_media_url_fallback genera URLs correctas
# ═══════════════════════════════════════════════════════════════════════════════
def test_build_media_urls():
    print("\n═══ TEST 18: _build_play_media_url_fallback / _build_search_url ═══")
    yt_url = AppController._build_play_media_url_fallback("gold in the ceiling", "youtube")
    check("YouTube media URL correcta",
          "youtube.com/results?search_query=gold" in yt_url,
          f"got: {yt_url}")

    ytm_url = AppController._build_play_media_url_fallback("lofi beats", "youtube_music")
    check("YouTube Music URL correcta",
          "music.youtube.com/search?q=lofi" in ytm_url,
          f"got: {ytm_url}")

    search_url = AppController._build_search_url("tutorial python")
    check("DuckDuckGo search URL correcta",
          "duckduckgo.com/?q=tutorial" in search_url,
          f"got: {search_url}")

    google_url = AppController._build_search_url("tutorial python", "google")
    check("Google search URL correcta",
          "google.com/search?q=tutorial" in google_url,
          f"got: {google_url}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 19: Case-insensitive split — "Reproduce X" (capital R)
# ═══════════════════════════════════════════════════════════════════════════════
async def test_router_case_insensitive_reproduce():
    print("\n═══ TEST 19: Router → 'Reproduce gold in the ceiling en youtube' (capital R) ═══")
    router = make_router()
    analysis = await router.classify_and_parse("Reproduce gold in the ceiling en youtube")

    check("intent = automation", analysis.get("intent") == "automation",
          f"got: {analysis.get('intent')}")
    check("automation_intent = PLAY_MEDIA", analysis.get("automation_intent") == "PLAY_MEDIA",
          f"got: {analysis.get('automation_intent')}")
    check("provider = youtube", analysis.get("provider") == "youtube",
          f"got: {analysis.get('provider')}")
    # The actual search_query should be "gold in the ceiling", NOT "youtube" or "gold in the ceiling en youtube"
    sq = analysis.get("search_query", "")
    check("search_query = 'gold in the ceiling' (song name extracted)",
          sq is not None and "gold" in sq and "ceiling" in sq,
          f"got: {sq}")
    check("search_query does NOT contain 'youtube'",
          sq is not None and "youtube" not in sq.lower(),
          f"got: {sq}")
    print(f"  {INFO} Análisis completo: {analysis}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 20: Provider suffix stripping — various patterns
# ═══════════════════════════════════════════════════════════════════════════════
async def test_router_provider_suffix_stripping():
    print("\n═══ TEST 20: Router → provider suffix stripping ═══")
    router = make_router()

    # "reproduce lofi beats en spotify"
    a1 = await router.classify_and_parse("reproduce lofi beats en spotify")
    sq1 = a1.get("search_query", "")
    check("'lofi beats en spotify' → search_query has 'lofi'",
          sq1 is not None and "lofi" in sq1.lower(),
          f"got: {sq1}")
    check("search_query no contiene 'spotify'",
          sq1 is not None and "spotify" not in sq1.lower(),
          f"got: {sq1}")

    # "busca tutorial python en google"
    a2 = await router.classify_and_parse("busca tutorial python en google")
    sq2 = a2.get("search_query", "")
    check("'busca tutorial python en google' → search_query has 'tutorial'",
          sq2 is not None and "tutorial" in sq2.lower(),
          f"got: {sq2}")
    check("search_query no contiene 'google'",
          sq2 is not None and "google" not in sq2.lower(),
          f"got: {sq2}")

    # "Pon The Black Keys en youtube" (capital, different verb)
    a3 = await router.classify_and_parse("Pon The Black Keys en youtube")
    sq3 = a3.get("search_query", "")
    check("'Pon The Black Keys en youtube' → search_query has 'black keys'",
          sq3 is not None and "black" in sq3.lower() and "keys" in sq3.lower(),
          f"got: {sq3}")
    check("search_query no contiene 'youtube'",
          sq3 is not None and "youtube" not in sq3.lower(),
          f"got: {sq3}")

    print(f"  {INFO} Todas las variantes verificadas")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 21: E2E capital-R reproduce → correct YouTube search URL
# ═══════════════════════════════════════════════════════════════════════════════
async def test_e2e_capital_reproduce():
    print("\n═══ TEST 21: E2E 'Reproduce gold in the ceiling en youtube' (capital R) ═══")
    router = make_router()
    analysis = await router.classify_and_parse("Reproduce gold in the ceiling en youtube")

    with patch('subprocess.Popen') as mock_popen, \
         patch('shutil.which', return_value="/usr/bin/firefox"), \
         patch('subprocess.run') as mock_run:

        mock_run.return_value = MagicMock(returncode=0)

        launcher = AppLauncher.__new__(AppLauncher)
        launcher.daemon_available = False
        launcher.is_wayland = False
        launcher.WEB_APP_URLS = AppLauncher.WEB_APP_URLS

        controller = AppController.__new__(AppController)
        controller.launcher = launcher
        controller._browser = MagicMock()
        controller.planner = MagicMock()
        # Mock yt-dlp
        controller._ytdlp_resolve_youtube = AsyncMock(return_value="https://www.youtube.com/watch?v=eEhMmaqGKL0")

        result = asyncio.get_event_loop().run_until_complete(
            controller.execute(analysis)
        )

        check("Popen fue llamado", mock_popen.called)
        if mock_popen.called:
            cmd = mock_popen.call_args[0][0]
            url_str = str(cmd)
            check("URL es video directo (/watch?v=)", "/watch?v=" in url_str,
                  f"got: {cmd}")
            check("URL contiene 'gold' related ID", "eEhMmaqGKL0" in url_str,
                  f"got: {cmd}")
        check("Resultado OK", "✓" in result, f"got: {result}")
        print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 22: Controller fallback — PLAY_MEDIA with query = provider name
# ═══════════════════════════════════════════════════════════════════════════════
def test_controller_play_media_fallback():
    print("\n═══ TEST 22: Controller PLAY_MEDIA fallback (query=provider name) ═══")
    # Simulate a case where router didn't extract search_query properly
    analysis = {
        "intent": "automation",
        "automation_intent": "PLAY_MEDIA",
        "provider": "youtube",
        "action": "play",
        "target": "youtube",
        # No search_query! Bug scenario.
    }

    controller = AppController.__new__(AppController)
    controller.launcher = MagicMock(spec=AppLauncher)
    controller.launcher.WEB_APP_URLS = AppLauncher.WEB_APP_URLS
    controller.launcher.open_url_in_browser = MagicMock(
        return_value="✓ Abriendo youtube.com en nueva pestaña de firefox"
    )
    controller._browser = MagicMock()
    controller.planner = MagicMock()

    result = asyncio.get_event_loop().run_until_complete(controller.execute(analysis))

    check("Llamó open_url_in_browser", controller.launcher.open_url_in_browser.called)
    if controller.launcher.open_url_in_browser.called:
        url_arg = controller.launcher.open_url_in_browser.call_args[0][0]
        # Should navigate to youtube.com, NOT search for "youtube"
        check("Fallback: abre youtube.com directamente (no search_query=youtube)",
              "youtube.com" in url_arg and "search_query=youtube" not in url_arg,
              f"got: {url_arg}")
    check("Resultado contiene ✓", "✓" in result, f"got: {result}")
    print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 23: PLAY_MEDIA fallback when yt-dlp returns None (unavailable/timeout)
# ═══════════════════════════════════════════════════════════════════════════════
def test_controller_play_media_ytdlp_unavailable():
    print("\n═══ TEST 23: PLAY_MEDIA fallback cuando yt-dlp no disponible ═══")
    analysis = {
        "intent": "automation",
        "automation_intent": "PLAY_MEDIA",
        "provider": "youtube",
        "action": "play",
        "target": "gold in the ceiling",
        "search_query": "gold in the ceiling",
    }

    controller = AppController.__new__(AppController)
    controller.launcher = MagicMock(spec=AppLauncher)
    controller.launcher.open_url_in_browser = MagicMock(
        return_value="✓ Abriendo youtube.com en nueva pestaña de firefox"
    )
    controller._browser = MagicMock()
    controller.planner = MagicMock()
    # yt-dlp returns None (not installed, timeout, error)
    controller._ytdlp_resolve_youtube = AsyncMock(return_value=None)

    result = asyncio.get_event_loop().run_until_complete(controller.execute(analysis))

    check("Llamó open_url_in_browser", controller.launcher.open_url_in_browser.called)
    if controller.launcher.open_url_in_browser.called:
        url_arg = controller.launcher.open_url_in_browser.call_args[0][0]
        check("Fallback: URL es search page (/results?search_query=)",
              "youtube.com/results?search_query=" in url_arg,
              f"got: {url_arg}")
        check("URL contiene la canción",
              "gold" in url_arg,
              f"got: {url_arg}")
    check("Resultado OK", "✓" in result, f"got: {result}")
    print(f"  {INFO} Resultado: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 24: Router suffix stripping handles typo 'yotube'
# ═══════════════════════════════════════════════════════════════════════════════
async def test_router_typo_yotube():
    print("\n═══ TEST 24: Router → suffix stripping con typo 'yotube' ═══")
    router = make_router()
    analysis = await router.classify_and_parse("Reproduce gold in the ceiling en yotube")

    check("intent = automation", analysis.get("intent") == "automation",
          f"got: {analysis.get('intent')}")
    check("automation_intent = PLAY_MEDIA", analysis.get("automation_intent") == "PLAY_MEDIA",
          f"got: {analysis.get('automation_intent')}")
    sq = analysis.get("search_query", "")
    check("search_query = 'gold in the ceiling' (stripped 'yotube')",
          sq is not None and "gold" in sq and "ceiling" in sq,
          f"got: {sq}")
    check("search_query no contiene 'yotube'",
          sq is not None and "yotube" not in sq.lower(),
          f"got: {sq}")
    print(f"  {INFO} Análisis: {analysis}")
# ═══════════════════════════════════════════════════════════════════════════════
async def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║   Test de Routing: Firefox Nativo vs Playwright             ║")
    print("╚═══════════════════════════════════════════════════════════════╝")

    await test_router_abre_firefox()
    await test_router_abre_youtube()
    await test_router_abre_instagram()
    await test_router_busca_youtube()
    await test_router_reproduce_youtube()
    await test_router_firefox_y_youtube()
    test_controller_abre_firefox()
    test_controller_abre_youtube()
    test_launcher_open_firefox_direct()
    test_launcher_open_youtube_direct()
    test_browser_direct_not_running()
    test_browser_direct_already_running()
    test_open_url_already_running()
    await test_e2e_abre_youtube()
    test_controller_play_media_youtube()
    test_controller_search_native()
    await test_e2e_play_media_youtube()
    test_build_media_urls()
    await test_router_case_insensitive_reproduce()
    await test_router_provider_suffix_stripping()
    await test_e2e_capital_reproduce()
    test_controller_play_media_fallback()
    test_controller_play_media_ytdlp_unavailable()
    await test_router_typo_yotube()

    passed = sum(results)
    total = len(results)
    failed = total - passed

    print(f"\n{'═' * 60}")
    if failed == 0:
        print(f"  \033[92m✓ TODOS LOS TESTS PASARON: {passed}/{total}\033[0m")
    else:
        print(f"  \033[91m✗ FALLARON {failed}/{total} tests\033[0m")
    print(f"{'═' * 60}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
