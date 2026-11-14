import asyncio
import os
import shutil
import subprocess
import sys
import logging
from typing import Dict, Any, Optional

from modules.automation.app_launcher import AppLauncher
from modules.automation.browser_controller import BrowserController
from modules.automation.planner import WebAutomationPlanner
from core.browser_config import BrowserConfig
from core.app_config import PLAYWRIGHT_CAPABLE_SITES

logger = logging.getLogger(__name__)


def _resolve_ytdlp_path() -> Optional[str]:
    """Encuentra el binario yt-dlp. shutil.which busca en PATH, pero cuando el
    backend corre con `venv/Scripts/python.exe` (sin activar el venv), Scripts/
    NO está en PATH y yt-dlp.exe vive ahí. Lo buscamos junto a sys.executable."""
    found = shutil.which("yt-dlp")
    if found:
        return found
    exe = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
    cand = os.path.join(os.path.dirname(sys.executable), exe)
    return cand if os.path.exists(cand) else None


class AppController:
    """
    Controlador optimizado para CachyOS (Arch Linux).
    Usa AppLauncher (daemon) para apps y BrowserController para web.
    """

    def __init__(self, config: Optional[BrowserConfig] = None):
        self.launcher = AppLauncher()
        self._browser: Optional[BrowserController] = None
        self._browser_config = config
        self.planner = WebAutomationPlanner()
        # QUIÉN controla la última pestaña que abrimos. Playwright solo puede
        # actuar sobre las pestañas que abrió ÉL; una abierta de forma nativa
        # vive en el Edge del usuario, fuera de su alcance. Sin este dato, un
        # "hacé click en seguir" después de un "abre instagram" nativo correría
        # contra OTRA página en OTRO perfil, en silencio — que es peor que
        # fallar. `None` = todavía no navegamos a nada.
        self._tab_control: Optional[str] = None   # "native" | "owned"
        self._tab_url: Optional[str] = None

    @property
    def browser(self) -> BrowserController:
        """Lazy-init: Playwright solo se carga cuando se necesita (INTERACT)."""
        if self._browser is None:
            logger.info("Inicializando BrowserController (Playwright) bajo demanda")
            self._browser = BrowserController(config=self._browser_config)
        return self._browser

    async def execute(self, analysis: Dict[str, Any]) -> str:
        """Ejecuta una accion basada en el analisis de intencion."""
        result = await self._execute_inner(analysis)
        # Aviso único cuando el perfil dedicado de automatización recién se
        # creó: el usuario debe iniciar sesión una vez en sus sitios.
        if self._browser is not None and self._browser._automation_profile_first_run:
            self._browser._automation_profile_first_run = False
            result = (
                "Nota: se creó un perfil de Edge dedicado para automatización. "
                "Inicia sesión una vez en tus sitios (Instagram, YouTube Music, etc.) "
                "en esa ventana para que queden guardados.\n" + result
            )
        return result

    async def _execute_inner(self, analysis: Dict[str, Any]) -> str:
        """Despacho interno de intents de automatización."""
        intent = analysis.get("intent")
        automation_intent = analysis.get("automation_intent")
        action = analysis.get("action", "open")
        target = analysis.get("target") or analysis.get("data", {}).get("message") or ""
        target = str(target).lower()
        search_query = analysis.get("search_query")
        provider = analysis.get("provider", "generic")
        logger.debug(f"Analisis recibido: {analysis}")

        # 0. SEQUENCE → ejecutar pasos en serie con fire-and-forget
        if automation_intent == "SEQUENCE":
            steps = analysis.get("steps", []) or []
            if not steps:
                return "Secuencia vacía."
            results: list[str] = []
            for i, step in enumerate(steps, 1):
                try:
                    # Cada step ya tiene su propio automation_intent (PLAY_MEDIA,
                    # NAVIGATE, etc.) gracias a parse_browser_action.
                    res = await self.execute(step)
                    results.append(f"[{i}/{len(steps)}] {res}")
                except Exception as e:
                    # No abortar la secuencia: si un paso falla, anotarlo y seguir
                    # con los siguientes para que el usuario vea qué pasó con cada
                    # acción de su instrucción compuesta.
                    logger.warning(f"SEQUENCE step {i} fail: {e}")
                    _target_dbg = step.get("target") or step.get("action") or "?"
                    results.append(f"[{i}/{len(steps)}] Error en '{_target_dbg}': {e}")
                # Fire-and-forget: 0.5s entre pasos para que el SO procese el
                # launch anterior (calculator/Popen) antes del siguiente; 0.3s
                # resultaba muy ajustado en Windows cuando el primer paso abre
                # una app nativa y el segundo abre el navegador.
                if i < len(steps):
                    await asyncio.sleep(0.5)
            return "\n".join(results)

        # 1. NAVIGATE → Playwright para sitios interaction-capable, nativo para el resto
        if automation_intent == "NAVIGATE":
            url = self._resolve_navigate_url(analysis)
            if url:
                # Normalizado solo acá: `target` puede ser el texto a tipear en un
                # INTERACT, donde la puntuación es parte del contenido.
                from core.app_config import normalize_target as _norm_nav
                target = _norm_nav(target)
                # NAVEGAR es navegar: va al navegador del usuario, con SU
                # sesión. Antes, `target in PLAYWRIGHT_CAPABLE_SITES` mandaba
                # instagram/youtube/reddit SIEMPRE por Playwright — pagando en
                # CADA navegación una capacidad (control del DOM) que hace falta
                # A VECES. Y como el Edge real no arranca con el puerto de
                # depuración, el attach falla y todo caía en el perfil
                # `EdgeAutomation`: otra ventana, otra sesión, y la sensación
                # de que el comando se ignoró.
                #
                # La decisión ya no mira el DOMINIO, mira el PEDIDO: Playwright
                # entra en INTERACT y PLAYBACK_CONTROL, que son los que
                # necesitan tocar la página.
                logger.info(f"NAVIGATE → navegador nativo: {url}")
                result_msg = await self._open_url_async(url)
                self._tab_control, self._tab_url = "native", url
                # Emit event para que VerificationAgent revise si el target
                # original era una app nativa pero terminó como URL (caso bug).
                self._publish_navigate_url(target, url, result_msg)
                return result_msg

        # 1.5 PLAYBACK_CONTROL → controles en tiempo real sobre el reproductor activo
        if automation_intent == "PLAYBACK_CONTROL":
            ctl = analysis.get("control_action", "")
            if ctl == "pause":
                return await self.browser.pause_music()
            if ctl == "resume":
                return await self.browser.resume_music()
            if ctl == "next":
                return await self.browser.next_track()
            if ctl == "prev":
                return await self.browser.prev_track()
            if ctl in ("mute", "unmute"):
                return await self.browser.mute_music()    # toggle: alterna silencio
            if ctl == "close":
                return await self.browser.stop_music()
            return f"Acción de control no reconocida: {ctl}"

        # 2. PLAY_MEDIA → try yt-dlp for direct URL, fall back to Playwright
        if automation_intent == "PLAY_MEDIA":
            query = search_query or target
            media_type = analysis.get("media_type", "song")
            provider_names = {"youtube", "spotify", "youtube_music", "generic", "firefox"}
            if query and query.lower().strip() in provider_names:
                provider_url = self.launcher.WEB_APP_URLS.get(provider, "")
                if not provider_url:
                    provider_url = self.launcher.WEB_APP_URLS.get(query.lower().strip(), "")
                if provider_url:
                    logger.info(f"PLAY_MEDIA sin query específico → navegar a {provider_url}")
                    return await self._open_url_async(provider_url)

            # Despacho extendido para YouTube Music: artistas, álbumes, recomendaciones
            if provider == "youtube_music" and media_type in ("artist", "album", "recommendation"):
                if media_type == "artist":
                    logger.info(f"PLAY_MEDIA(artist) → Playwright: {query!r}")
                    self._tab_control = "owned"
                    return await self.browser.play_artist(query)
                if media_type == "album":
                    _album = analysis.get("album")
                    _artist = analysis.get("artist")
                    if _album and _artist:
                        # "{álbum} de {artista}": ir a la página del artista y
                        # buscar el álbum por nombre (más confiable que filtrar
                        # "Álbumes" con el string combinado).
                        logger.info(
                            f"PLAY_MEDIA(named album) → Playwright: "
                            f"album={_album!r} artist={_artist!r}"
                        )
                        self._tab_control = "owned"
                        return await self.browser.play_named_album(
                            _artist, _album, fallback_query=query
                        )
                    logger.info(f"PLAY_MEDIA(album) → Playwright: {query!r}")
                    self._tab_control = "owned"
                    return await self.browser.play_album(query)
                seed = query if query else None
                logger.info(f"PLAY_MEDIA(recommendation) → Playwright, seed={seed!r}")
                self._tab_control = "owned"
                return await self.browser.play_recommendations(seed=seed)

            # Try yt-dlp for a direct video/track URL (sólo canciones)
            direct_url = None
            if provider in ("youtube", "generic"):
                direct_url = await self._ytdlp_resolve_youtube(query)
            elif provider == "youtube_music":
                direct_url = await self._ytdlp_resolve_youtube(query, music=True)
            if direct_url:
                logger.info(f"PLAY_MEDIA → navegador nativo (URL directa): {direct_url}")
                return await self._open_url_async(direct_url)
            # yt-dlp no disponible o falló → Playwright para búsqueda interactiva
            playwright_provider = provider if provider in ("youtube", "youtube_music") else "youtube"
            logger.info(f"PLAY_MEDIA → Playwright: {query!r}, provider={playwright_provider}")
            self._tab_control = "owned"
            return await self.browser.play_media(query, playwright_provider)

        # 3. SEARCH → construct search URL and open in native browser
        if automation_intent == "SEARCH":
            query = search_query or target
            url = self._build_search_url(query)
            logger.info(f"SEARCH → navegador nativo: {url}")
            return await self._open_url_async(url)

        # 4. INTERACT → needs Playwright for DOM control (click, type, scroll)
        if automation_intent == "INTERACT":
            _blocked = self._needs_controlled_tab("tocar la página")
            if _blocked:
                return _blocked
            logger.info(f"INTERACT → Playwright: {action}, provider: {provider}")
            try:
                plan = self.planner.build_plan(analysis)
                if not plan:
                    return "No se pudo generar un plan de ejecucion."

                for step in plan:
                    result = await self._execute_atomic_action(step)
                    if "Error" in result:
                        return result
                return "Comando ejecutado completamente"
            except Exception as e:
                logger.error(f"Error ejecutando plan: {e}")
                return f"Error en planificacion: {e}"

        # 5. BROWSE_AND_EXTRACT → navegar headless + extraer + sintetizar con LLM
        #    Modelo HÍBRIDO: si la página oficial no rinde (falla, vacía o contenido fino
        #    tipo homepage/JS/landing), emite el sentinel __BROWSE_FALLBACK__ para que el
        #    orchestrator caiga a búsqueda web (Tavily) y redacte igual una respuesta.
        if automation_intent == "BROWSE_AND_EXTRACT":
            url = analysis.get("url", "")
            extract_query = analysis.get("extract_query", "")
            target = analysis.get("target", "")
            logger.info(f"BROWSE_AND_EXTRACT → {url} (query: {extract_query})")

            # Umbral mínimo de texto útil para considerar que la página respondió.
            _MIN_USEFUL_CHARS = 300

            def _fallback(reason: str) -> str:
                logger.info(
                    f"BROWSE_AND_EXTRACT fallback ({reason}) → búsqueda web para: {extract_query!r}"
                )
                return f"__BROWSE_FALLBACK__\n{extract_query}\n{target}"

            try:
                from modules.search.web_search import WebSearch
                ws = WebSearch()
                browse_result = await ws.deep_browse(url, extract_query)

                if not browse_result.get("success"):
                    return _fallback(browse_result.get("error_type") or "browse_failed")

                content = browse_result.get("content", "")
                title = browse_result.get("title", "")
                if not content or len(content.strip()) < _MIN_USEFUL_CHARS:
                    return _fallback("thin_content")

                # Retornar el contenido con metadata para que el orchestrator lo pase al LLM
                return (
                    f"__BROWSE_EXTRACT__\n"
                    f"URL: {url}\n"
                    f"Título: {title}\n"
                    f"Query: {extract_query}\n"
                    f"Contenido:\n{content}"
                )
            except Exception as e:
                logger.error(f"Error en BROWSE_AND_EXTRACT: {e}")
                return _fallback("exception")

        # 2. Logica para intents sin automation_intent
        if intent == "automation":
            if "hola" in target or "ayudarte" in target:
                return "Parece que el comando de voz no fue claro. Podrias repetirlo?"

            if action in ("open", "start"):
                # El target puede traer la puntuación de la frase dictada: el
                # extractor LLM de CAPA 1.5 copia lo que dijo el usuario tal cual,
                # así que "Puedes abrir Discord." llega como "discord." y
                # resolve_alias() no lo reconoce → intentaría lanzar un binario
                # inexistente. El camino regex del router ya entrega limpio; esto
                # cubre el otro.
                from core.app_config import normalize_target as _norm_open
                app_target = _norm_open(target) or target
                result_msg = await self._open_app_async(app_target)
                # 300 ms de sleep síncrono + una enumeración completa de
                # procesos, en CADA apertura de app.
                await asyncio.to_thread(self._publish_app_launched, app_target, result_msg)
                return result_msg
            elif action == "close":
                # kill_process ahora pide WM_CLOSE y espera hasta 5 s a que la
                # app guarde y cierre sola, así que bloquea más que antes.
                return await asyncio.to_thread(self.close_app, target)
            elif action == "update":
                return self.update_system()

        if intent in ("search", "web_search"):
            clean_action = action.replace("browser_", "")

            # DOM interactions → Playwright (lazy init)
            if clean_action in ("click", "type", "scroll", "get_text", "screenshot"):
                logger.info(f"Web interaction '{clean_action}' → Playwright")
                if clean_action == "click":
                    return await self.browser.click(target)
                if clean_action == "type":
                    if " en " in target:
                        parts = target.split(" en ")
                        text, selector = parts[0], parts[1]
                    else:
                        text, selector = target, "active"
                    return await self.browser.type_text(selector, text)
                if clean_action == "scroll":
                    direction = "up" if "up" in target or "arriba" in target else "down"
                    return await self.browser.scroll(direction)
                if clean_action == "get_text":
                    return await self.browser.get_text()
                if clean_action == "screenshot":
                    return await self.browser.screenshot()

            # Navigate / search → native Firefox (no Playwright)
            if clean_action == "navigate":
                url = self._ensure_url(target) if target else None
                if url:
                    logger.info(f"Legacy navigate → navegador nativo: {url}")
                    return await self._open_url_async(url)

            # Default: search in native browser
            query = search_query or target
            if query:
                url = self._build_search_url(query)
                logger.info(f"Legacy search → navegador nativo: {url}")
                return await self._open_url_async(url)
            return "No especificaste que buscar"

        if intent == "media":
            return self.control_media(action, target)

        return "No estoy seguro de como ejecutar esa accion."

    def _resolve_navigate_url(self, analysis: Dict[str, Any]) -> Optional[str]:
        """Resolve target URL from analysis for native browser navigation."""
        from core.app_config import (
            NATIVE_APPS, looks_like_domain, normalize_target,
        )

        # normalize_target quita la puntuación de cierre que deja el STT: sin
        # esto "Puedes abrir Discord." llegaba como "discord." y la heurística de
        # dominio lo mandaba a https://discord. (bug real, 2026-09-29).
        # navigate_target se normaliza igual: lo llena el extractor LLM de CAPA 1.5,
        # que copia el target tal cual lo dictó el usuario, puntuación incluida.
        navigate_target = normalize_target(analysis.get("navigate_target", ""))
        target = normalize_target(analysis.get("target", ""))
        search_query = analysis.get("search_query")
        sub_path = analysis.get("sub_path")  # from compound commands

        # Una app nativa NUNCA se resuelve como URL, aunque el intent llegue como
        # NAVIGATE. Red de seguridad para que este bug no vuelva por otra vía —
        # cubre las dos fuentes de target (regex del router y extractor LLM).
        for _candidate in (target, navigate_target):
            if (_candidate and _candidate in NATIVE_APPS
                    and _candidate not in self.launcher.WEB_APP_URLS):
                logger.info(
                    "NAVIGATE descartado: '%s' es una app nativa, no un sitio", _candidate
                )
                return None

        def _with_sub_path(base_url: str) -> str:
            """Append sub_path to base_url for compound navigation (e.g. instagram.com/reels)."""
            if sub_path:
                return f"{base_url.rstrip('/')}/{sub_path.lstrip('/')}"
            return base_url

        # Priority 1: explicit navigate_target from router (pre-built URL, e.g. search URL)
        if navigate_target:
            url = self.launcher.WEB_APP_URLS.get(navigate_target)
            if url:
                return _with_sub_path(url)
            # Solo si es una URL/dominio de verdad. Antes se hacía _ensure_url()
            # a ciegas, así que una palabra suelta del extractor LLM ("discord.")
            # se convertía en https://discord. y abría una pestaña.
            if looks_like_domain(navigate_target):
                return _with_sub_path(self._ensure_url(navigate_target))
            logger.info(
                "navigate_target '%s' no es un dominio — no se navega", navigate_target
            )
            return None

        # Priority 2: target is a known web app
        url = self.launcher.WEB_APP_URLS.get(target)
        if url:
            return _with_sub_path(url)

        # Priority 3: target looks like a domain/URL
        if looks_like_domain(target):
            return _with_sub_path(self._ensure_url(target))

        # Priority 4: check search_query for web app or domain
        if search_query:
            _sq = normalize_target(search_query)
            url = self.launcher.WEB_APP_URLS.get(_sq)
            if url:
                return _with_sub_path(url)
            if looks_like_domain(_sq):
                return _with_sub_path(self._ensure_url(_sq))

        return None

    @staticmethod
    def _ensure_url(url: str) -> str:
        """Ensure URL has a proper scheme."""
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            return f"https://{url}"
        return url

    # ── yt-dlp resolver ──────────────────────────────────────────────────

    _YTDLP_TIMEOUT = 6  # seconds; generous enough for network, tight enough for UX

    async def _resolve_play_media_url(self, query: str, provider: str) -> str:
        """Try to resolve a direct video/track URL via yt-dlp.
        Falls back to the search-results page if yt-dlp is missing or times out."""
        if provider in ("youtube", "generic"):
            direct = await self._ytdlp_resolve_youtube(query)
            if direct:
                return direct
        elif provider == "youtube_music":
            direct = await self._ytdlp_resolve_youtube(query, music=True)
            if direct:
                return direct
        # Fallback to search page
        return self._build_play_media_url_fallback(query, provider)

    async def _ytdlp_resolve_youtube(self, query: str, music: bool = False) -> Optional[str]:
        """Use yt-dlp to get the first YouTube video ID for *query*.
        Returns a direct /watch?v= URL or None on failure."""
        ytdlp = _resolve_ytdlp_path()
        if not ytdlp:
            logger.warning("yt-dlp no encontrado (ni en PATH ni en el venv), "
                           "usando fallback Playwright")
            return None
        search_prefix = "ytsearch1:" if not music else "ytsearch1:music "
        try:
            proc = await asyncio.create_subprocess_exec(
                ytdlp, f"{search_prefix}{query}",
                "--get-id", "--no-warnings", "--no-playlist",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._YTDLP_TIMEOUT
            )
            video_id = stdout.decode().strip().split("\n")[0].strip()
            if video_id and len(video_id) >= 8:
                if music:
                    url = f"https://music.youtube.com/watch?v={video_id}&autoplay=1"
                else:
                    url = f"https://www.youtube.com/watch?v={video_id}&autoplay=1"
                logger.info(f"yt-dlp resolvió: {query!r} → {url}")
                return url
            logger.warning(f"yt-dlp no retornó video ID válido: {video_id!r}")
            return None
        except asyncio.TimeoutError:
            logger.warning(f"yt-dlp timeout ({self._YTDLP_TIMEOUT}s) para: {query!r}")
            return None
        except Exception as e:
            logger.warning(f"yt-dlp error: {e}")
            return None

    @staticmethod
    def _build_play_media_url_fallback(query: str, provider: str) -> str:
        """Build a search-results URL (fallback when yt-dlp unavailable)."""
        from urllib.parse import quote_plus
        q = quote_plus(query)
        if provider == "youtube":
            return f"https://www.youtube.com/results?search_query={q}&autoplay=1"
        elif provider == "youtube_music":
            return f"https://music.youtube.com/search?q={q}&autoplay=1"
        elif provider == "spotify":
            return f"https://open.spotify.com/search/{q}"
        else:
            return f"https://www.youtube.com/results?search_query={q}&autoplay=1"

    @staticmethod
    def _build_search_url(query: str, engine: str = "duckduckgo") -> str:
        """Build a search engine URL."""
        from urllib.parse import quote_plus
        q = quote_plus(query)
        if engine == "google":
            return f"https://www.google.com/search?q={q}"
        elif engine == "youtube":
            return f"https://www.youtube.com/results?search_query={q}"
        else:
            return f"https://duckduckgo.com/?q={q}"

    async def _execute_atomic_action(self, step: Dict[str, Any]) -> str:
        """Ejecuta una accion atomica del plan."""
        action_type = step.get("type")
        params = step.get("params", {})
        provider = step.get("provider", "generic")

        logger.debug(f"Ejecutando: {action_type} params={params}")
        logger.debug(f"Browser state: {self.browser.get_state().value}, health: {self.browser.get_health().value}")

        try:
            if action_type == "navigate":
                url = params.get("url")
                if url:
                    self._tab_control, self._tab_url = "owned", url
                    return await self.browser.navigate_to(url)
                return "Error: URL no especificada"

            elif action_type == "search":
                query = params.get("query")
                engine = params.get("engine", "duckduckgo")
                if query:
                    return await self.browser.open_and_search(query, engine)
                return "Error: consulta no especificada"

            elif action_type == "click":
                selector_type = params.get("selector_type")
                if selector_type:
                    return await self.browser.click_element(selector_type, provider)
                return "Error: selector no especificado"

            elif action_type == "type":
                selector = params.get("selector", "active")
                text = params.get("text", "")
                return await self.browser.type_text(selector, text)

            elif action_type == "wait":
                element_type = params.get("element_type")
                timeout = params.get("timeout", 10.0)
                return await self.browser.wait_for(element_type, provider, timeout)

            elif action_type == "play_media":
                return await self.browser.control_media("play")

            elif action_type == "control_media":
                action = params.get("action")
                if action:
                    return await self.browser.control_media(action)
                return "Error: accion de medios no especificada"

            elif action_type == "find_by_text":
                text = params.get("text", "")
                partial = params.get("partial", True)
                return await self.browser.find_by_text(text, partial)

            elif action_type == "close_tab":
                return await self.browser.close_tab()

            else:
                return f"Error: tipo de accion no soportado: {action_type}"

        except Exception as e:
            logger.error(f"Error en accion {action_type}: {e}")
            return f"Error en {action_type}: {e}"

    # ── Puentes async para el launcher ──────────────────────────────────
    # `_execute_inner` es una corrutina y el launcher es síncrono. Llamarlo
    # en directo congelaba el event loop entero — con un solo worker de
    # uvicorn eso es WS, chat, voz, Telegram y el health monitor a la vez.
    #
    # Lo peor estaba justo en el comando del incidente del 2026-10-01:
    # `open_app("discord")` → `_has_electron_renderer` (app_launcher.py), que
    # hace `psutil.process_iter` + `time.sleep(0.5)` en un loop de hasta
    # 8 SEGUNDOS cuando Discord arranca sin renderer (pantalla de update).
    # Arreglamos el freeze del escritorio y quedaba el del backend, en el
    # mismo "abre discord".

    async def _open_app_async(self, app_target: str) -> str:
        return await asyncio.to_thread(self.launcher.open_app, app_target)

    async def _open_url_async(self, url: str) -> str:
        # Popen es detached, pero `is_browser_running` enumera procesos antes.
        return await asyncio.to_thread(self.launcher.open_url_in_browser, url)

    def _needs_controlled_tab(self, what: str) -> Optional[str]:
        """Devuelve un mensaje de rechazo si la última pestaña NO es nuestra.

        Playwright solo alcanza las pestañas que abrió él. Si lo último que
        hicimos fue abrir una URL en el Edge del usuario, actuar igual correría
        contra otra página en otro perfil **sin avisar**: el comando diría
        "listo" y el usuario no vería ningún cambio. Decirlo es la condición
        que hace aceptable navegar de forma nativa por defecto.
        """
        if self._tab_control != "native":
            return None
        _where = self._tab_url or "la página"
        return (
            f"Esa página la abrí en tu Edge, así que no la puedo {what} desde "
            f"acá — queda fuera de mi control. Decime \"abrila bajo control\" y "
            f"la reabro en la ventana que sí manejo ({_where})."
        )

    @staticmethod
    def _app_label(target: str) -> str:
        """Nombre de la app para mostrarle al usuario, nunca el de los procesos.

        "discord" -> "Discord". Si no hay entrada conocida, se devuelve lo que
        escribió el usuario: eso siempre es mejor que una lista de .exe.
        """
        raw = (target or "").strip()
        if not raw:
            return "la aplicación"
        try:
            from core.app_config import APP_WINDOW_INFO, resolve_alias
            info = APP_WINDOW_INFO.get(resolve_alias(raw.lower())) or {}
            return info.get("title") or raw
        except Exception:
            return raw

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            import psutil
            return psutil.pid_exists(int(pid))
        except Exception:
            return False

    def _publish_app_launched(self, app_name: str, result_msg: str) -> None:
        """
        Emite SKILL_EXECUTED para que el VerificationAgent verifique la
        apertura. Best-effort: captura PID por nombre de proceso ~300ms
        después del Popen. Nunca lanza — log y sigue.
        """
        try:
            import time as _time
            from core.app_config import APP_WINDOW_INFO, resolve_alias
            from core.event_bus import get_bus, SKILL_EXECUTED

            resolved = resolve_alias((app_name or "").lower().strip())
            winfo = APP_WINDOW_INFO.get(resolved, {})
            process_name = winfo.get("process", resolved)

            # Detectar si ya estaba abierto por la firma del mensaje
            msg_lower = (result_msg or "").lower()
            already_running = ("ya estaba" in msg_lower) or ("ya está" in msg_lower)
            success_flag = (result_msg or "").startswith("✓")

            # El PID REAL que devolvió Popen gana siempre. Buscar por nombre es
            # una carrera (300 ms de sleep no garantizan nada) y además es
            # ambiguo: `get_process_pid("powershell")` matchea cualquier
            # powershell del sistema, incluidos los que lanza el propio
            # Zephyro. El fallback por nombre queda solo para los caminos que
            # todavía no devuelven PID (daemon, web apps).
            pid = getattr(self.launcher, "last_launch_pid", None)
            if pid is not None and not self._pid_alive(pid):
                # Lanzadores que delegan y terminan (el shim `code.CMD` abre
                # Code.exe y sale). Su PID muerto no es evidencia de nada:
                # reportarlo sería acusar de crash a una app que SÍ abrió.
                pid = None
            if pid is None:
                _time.sleep(0.3)
                pid = self.launcher.get_process_pid(process_name)

            get_bus().publish(SKILL_EXECUTED, {
                "skill_name": "app-launcher",
                "input_preview": f"open {app_name}",
                "response": result_msg,
                "extra": {
                    "app": app_name,
                    "process_name": process_name,
                    "pid": pid,
                    "already_running": already_running,
                    "launch_success_message": success_flag,
                },
            })
        except Exception as exc:
            logger.debug(f"_publish_app_launched failed: {exc}")

    def _publish_navigate_url(self, target: str, url: str, result_msg: str) -> None:
        """
        Emite SKILL_EXECUTED tras navegar a URL. El VerificationAgent puede
        detectar el bug "pediste app nativa pero terminé en URL".
        """
        try:
            from core.app_config import NATIVE_APPS, APP_ALIASES
            from core.event_bus import get_bus, SKILL_EXECUTED

            target_lc = (target or "").lower().strip()
            resolved = APP_ALIASES.get(target_lc, target_lc)
            # Heurística: si el target original (o su alias) está en NATIVE_APPS
            # pero el resultado abre una URL, es un fallback indeseado.
            intended_native = resolved in NATIVE_APPS and "youtube" not in target_lc

            get_bus().publish(SKILL_EXECUTED, {
                "skill_name": "app-launcher",
                "input_preview": f"navigate {target}",
                "response": result_msg,
                "extra": {
                    "app": target,
                    "action": "navigate_url",
                    "url": url,
                    "intended_native": intended_native,
                    "fell_back_to_web": True,
                },
            })
        except Exception as exc:
            logger.debug(f"_publish_navigate_url failed: {exc}")

    def close_app(self, target: str, *, force: bool = False) -> str:
        """Le PIDE a la app que cierre. Nunca mata, salvo `force=True`.

        Cerrar no es matar, y antes el sistema no distinguía: mandaba WM_CLOSE,
        esperaba 5 s y mataba lo que siguiera vivo — destruyendo justo el
        trabajo que el diálogo "¿guardar cambios?" estaba protegiendo si el
        usuario tardaba en contestar.

        Ahora el mensaje dice lo que PASÓ, no lo que se intentó. "Cerré la
        ventana de Spotify pero sigue corriendo en segundo plano" no es una
        falla: es la diferencia entre cerrar y matar, dicha en voz alta.
        """
        from core.platform import CloseOutcome, force_terminate, request_close

        res = force_terminate(target) if force else request_close(target)

        # El usuario dijo "discord", no "Discord.exe x5 + DiscordSystemHelper
        # x2". `res.matched` trae un nombre por PID, sin dedup: para una app
        # Electron son 8 nombres repetidos en la cara del usuario. El detalle
        # por proceso es para el log; acá va la etiqueta que él usó.
        _what = self._app_label(target)
        if res.matched:
            logger.info(
                f"close_app({target!r}) -> {res.outcome.value}; "
                f"procesos: {', '.join(res.matched)}"
            )

        if res.outcome is CloseOutcome.CLOSED:
            return f"Listo, cerré {_what}."
        if res.outcome is CloseOutcome.PENDING:
            # OJO: que quede una ventana NO prueba que haya un diálogo de
            # guardado. Puede ser otra ventana de la misma app, un updater, o
            # un diálogo de otro proceso. Decir "está esperando que guardes"
            # como si fuera un hecho es inventar; se nombra la posibilidad.
            return (
                f"Le pedí a {_what} que cierre y le quedó una ventana abierta. "
                f"Puede estar preguntándote si guardás los cambios — fijate "
                f"antes de insistir. Si querés que lo cierre a la fuerza, decímelo."
            )
        if res.outcome is CloseOutcome.BACKGROUND:
            return (
                f"Cerré la ventana de {_what}, pero sigue corriendo en la "
                f"bandeja — así lo cierra él. Si querés que lo termine del "
                f"todo, decímelo."
            )
        if res.outcome is CloseOutcome.NO_WINDOW:
            return (
                f"{_what} está corriendo pero no tiene ninguna ventana abierta "
                f"(está en la bandeja), así que no hay a quién pedirle que "
                f"cierre. Si querés que lo termine a la fuerza, decímelo."
            )
        if res.outcome is CloseOutcome.PROTECTED:
            # Antes esto decía "no encontré el proceso": el gate pedía
            # confirmación, el usuario confirmaba, y recibía una mentira.
            return (
                f"No cierro {', '.join(dict.fromkeys(res.protected))}: cerrarlo tumbaría a "
                f"Zephyro o al escritorio."
            )
        if res.outcome is CloseOutcome.DENIED:
            return f"No tengo permisos para cerrar {_what}."
        return f"No pude encontrar el proceso {target} activo."

    def update_system(self) -> str:
        """Actualiza el sistema."""
        from core.platform import IS_WINDOWS
        if IS_WINDOWS:
            return "Actualización del sistema no soportada desde Zephyro en Windows. Usa Windows Update."
        try:
            subprocess.Popen(["konsole", "-e", "sudo", "pacman", "-Syu"])
            return "Terminal abierta para actualizar con pacman."
        except Exception:
            return "No pude lanzar la actualizacion."

    def control_media(self, action: str, target: str) -> str:
        """Controla reproduccion de medios via playerctl (Linux)."""
        from core.platform import IS_WINDOWS
        if IS_WINDOWS:
            return "Control de medios no soportado en Windows desde Zephyro."
        try:
            if "play" in action or "pause" in action:
                subprocess.run(["playerctl", "play-pause"], check=True)
                return "Reproduccion conmutada."
            if "next" in action:
                subprocess.run(["playerctl", "next"], check=True)
                return "Siguiente pista."
            return f"Accion de medios '{action}' no reconocida."
        except Exception:
            return "Necesitas 'playerctl' instalado (sudo pacman -S playerctl)."

    async def cleanup(self):
        """Cierra el navegador y libera recursos"""
        if self._browser is not None:
            try:
                await self._browser.close()
                self._browser = None
                logger.info("Browser (Playwright) cerrado en cleanup")
            except Exception as e:
                logger.error(f"Error en cleanup: {e}")
