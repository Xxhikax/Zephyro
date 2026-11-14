# modules/integrations/api_registry.py
"""
Registry de APIs externas para Zephyro.
- Pattern Registry para extensibilidad
- Router automático basado en keywords del query
- Integración con LLM para casos ambiguos
- Manejo de errores centralizado
"""
import asyncio
import datetime
import re
from typing import Optional, Callable, Dict, List, Tuple, Any


class APIRoute:
    """Define una ruta de API con sus keywords y handler."""
    def __init__(
        self,
        name: str,
        keywords: List[str],
        handler: Callable,
        description: str = "",
        requires_param: bool = True,
    ):
        self.name = name
        self.keywords = [kw.lower() for kw in keywords]
        self.handler = handler
        self.description = description
        self.requires_param = requires_param

    def matches(self, query: str) -> Tuple[bool, float]:
        """
        Verifica si la query hace match con este route.

        Returns:
            (matches: bool, confidence: float 0-1)
        """
        q = query.lower()
        matches = [kw for kw in self.keywords if kw in q]
        if not matches:
            return False, 0.0

        # Confianza basada en cuántas keywords matchean
        confidence = len(matches) / max(len(self.keywords), 1)
        return True, min(confidence, 1.0)


class APIRegistry:
    """
    Registry central de todas las integraciones de APIs externas.

    Detecta automáticamente qué API usar basándose en keywords del query
    y delega a los handlers correspondientes.

    Uso:
        registry = APIRegistry()
        result = await registry.handle("precio del bitcoin")
        # → llama a CoinGecko

        result = await registry.handle("resumen de wikipedia sobre Python")
        # → llama a Wikipedia

        result = await registry.handle("busca en internet: IA 2025")
        # → llama a WebSearch
    """

    def __init__(self):
        self._routes: List[APIRoute] = []
        self._initialized = False

    def register(self, route: APIRoute):
        """Registra una nueva ruta de API."""
        self._routes.append(route)

    def _setup_default_routes(self):
        """Configura las rutas por defecto (lazy, solo en primer uso)."""
        if self._initialized:
            return
        self._initialized = True

        # ----- CoinGecko: Crypto prices -----
        try:
            from modules.integrations.finance import get_crypto_price, get_crypto_history, get_top_cryptos

            async def handle_crypto(query: str) -> str:
                # Detectar si pide histórico
                if any(w in query.lower() for w in ["historial", "historia", "histórico", "últimos", "semana", "días", "mes"]):
                    coin = _extract_coin_name(query)
                    return await get_crypto_history(coin)
                elif any(w in query.lower() for w in ["top", "ranking", "mejores", "principales", "lista"]):
                    return await get_top_cryptos()
                else:
                    coin = _extract_coin_name(query)
                    return await get_crypto_price(coin)

            self.register(APIRoute(
                name="crypto",
                keywords=["bitcoin", "btc", "ethereum", "eth", "crypto", "criptomoneda",
                         "precio", "cotización", "cotizacion", "binance", "solana", "dogecoin",
                         "moneda digital", "blockchain", "defi", "nft"],
                handler=handle_crypto,
                description="Precios y datos de criptomonedas (CoinGecko)",
                requires_param=True,
            ))
        except ImportError as e:
            print(f"[APIRegistry] CoinGecko handler no disponible: {e}")

        # ----- WebSearch: Búsqueda web -----
        try:
            from modules.search.web_search import WebSearch

            _ws = WebSearch()

            _DEEP_SEARCH_KW = [
                "a fondo", "en profundidad", "profundo", "profunda",
                "detallado", "investigación profunda", "busqueda profunda",
                "búsqueda profunda", "busca en detalle", "investigación completa",
            ]

            async def handle_websearch(query: str, llm_fn=None) -> str:
                deep = any(kw in query.lower() for kw in _DEEP_SEARCH_KW)
                clean_query = _clean_search_query(query)
                print(f"[APIRegistry] handle_websearch: original={query!r} → clean={clean_query!r} deep={deep}")
                # Log motor status
                status = _ws.get_status()
                print(f"[APIRegistry] Engines: tavily={status['tavily']} key={status['tavily_key_preview']} ddg={status['duckduckgo']}")
                # Con LLM disponible: resumen directo; sin él: snippets crudos
                if llm_fn:
                    return await _ws.search_and_summarize(clean_query, llm_fn, max_results=4, deep=deep)
                results = await _ws.search(clean_query, max_results=5, deep=deep)
                if not results:
                    return f"No encontré resultados en internet para: '{clean_query}'"
                lines = [f"**Resultados web para:** {clean_query}\n"]
                for i, r in enumerate(results[:5], 1):
                    lines.append(f"{i}. **{r.title}**")
                    lines.append(f"   {r.snippet[:200]}")
                    lines.append(f"   🔗 {r.url}")
                    lines.append("")
                return "\n".join(lines)

            self.register(APIRoute(
                name="web_search",
                keywords=[
                    # Explícito (singular y plural)
                    "busca en internet", "busca online", "buscar en la web",
                    "busca web", "busca en google", "busca en duckduckgo",
                    "busquen en internet", "busquen online", "busquen en la web",
                    "busquen internet", "busquen",
                    "encuentra en internet", "consulta en internet",
                    "busca y dime", "busca en internet y dime",
                    "investiga en internet", "investiga online",
                    "investiga sobre", "investigues sobre",
                    "investiga quién", "investiga quien",
                    "investiga qué", "investiga que",
                    "averigua sobre", "averigua quién", "averigua quien",
                    # Preguntas sobre eventos actuales (el LLM no tiene datos post-entrenamiento)
                    "quién es el presidente", "quien es el presidente",
                    "quién es la presidenta", "quien es la presidenta",
                    "quién es el primer ministro", "quien es el primer ministro",
                    "quién gobierna", "quien gobierna",
                    "presidente de", "presidenta de",
                    "quién ganó las elecciones", "quien gano las elecciones",
                    "resultado de las elecciones", "resultados electorales",
                    "qué pasó con", "que paso con",
                    "últimas noticias de", "ultimas noticias de",
                    "noticias de hoy", "noticias recientes",
                    "quién ganó", "quien gano",
                    "situación actual de", "situacion actual de",
                    "actualmente quién", "actualmente quien",
                    # Patrones de noticias/actualidad
                    "noticias sobre", "noticias de",
                    "busca noticias", "buscar noticias",
                    "situacion actual", "situación actual",
                    "estado actual de",
                ],

                handler=handle_websearch,
                description="Búsqueda en internet (DuckDuckGo) con resumen LLM",
                requires_param=True,
            ))
        except ImportError as e:
            print(f"[APIRegistry] WebSearch handler no disponible: {e}")

        # ----- Wikipedia -----
        try:
            from modules.search.web_search import WebSearch

            _ws2 = WebSearch()

            async def handle_wikipedia(query: str, llm_fn=None) -> Optional[str]:
                # Extraer término de búsqueda
                term = _clean_wiki_query(query)
                raw = await _ws2.wikipedia_summary(term)

                # Si Wikipedia falló o el término no produjo resultados, dejar que el LLM responda
                if not raw or raw.startswith("Error ") or raw.startswith("No pude"):
                    return None

                # Si tenemos LLM y el resultado es largo/crudo, resumir
                if llm_fn and raw and len(raw) > 300:
                    try:
                        summary_prompt = (
                            f"El usuario preguntó: \"{query}\"\n\n"
                            f"Información obtenida de Wikipedia:\n{raw[:2000]}\n\n"
                            "Instrucciones:\n"
                            "- Responde en español de forma clara y concisa (3-5 oraciones).\n"
                            "- Responde directamente la pregunta del usuario.\n"
                            "- Si es una página de desambiguación, elige la acepción más probable.\n"
                            "- NO uses emojis, asteriscos ni markdown.\n"
                            "- NO digas 'según Wikipedia' ni cites la fuente.\n"
                        )
                        summarized = llm_fn(summary_prompt)
                        if summarized and len(summarized.strip()) > 20:
                            return summarized.strip()
                    except Exception as e:
                        print(f"[Wikipedia] LLM summary failed: {e}")
                return raw

            self.register(APIRoute(
                name="wikipedia",
                # SÓLO intención explícita de Wikipedia. Antes incluía "qué es"/"definición de"
                # → interceptaba TODA pregunta de concepto ("qué es la mitocondria") y la mandaba
                # a un fetch de Wikipedia (lento / podía colgarse) en vez de dejar que el LLM
                # respondiera conversacionalmente. Para enciclopedia, pedila explícita.
                keywords=["wikipedia", "wiki", "enciclopedia"],
                handler=handle_wikipedia,
                description="Búsqueda en Wikipedia",
                requires_param=True,
            ))
        except ImportError as e:
            print(f"[APIRegistry] Wikipedia handler no disponible: {e}")

        # ----- RSS Feeds -----
        try:
            from modules.integrations.rss_monitor import RSSMonitor

            _rss = RSSMonitor()

            async def handle_rss(query: str) -> str:
                q_lower = query.lower()
                if any(w in q_lower for w in ["monitorea", "sigue", "agrega feed", "añade feed"]):
                    # Extraer URL del feed
                    url_match = re.search(r'https?://\S+', query)
                    if url_match:
                        url = url_match.group(0)
                        success = _rss.add_feed(url)
                        return f"✅ Feed agregado: {url}" if success else f"No se pudo agregar el feed."
                    return "Por favor proporciona la URL del feed RSS."

                elif any(w in q_lower for w in ["lista feeds", "lista de feeds", "mis feeds", "feeds configurados"]):
                    feeds = _rss.list_feeds()
                    if not feeds:
                        return "No tienes feeds configurados. Usa: 'agrega feed https://...'"
                    lines = ["**Feeds configurados:**"]
                    for f in feeds:
                        lines.append(f"• {f['name']} ({f['url']})")
                    return "\n".join(lines)

                elif any(w in q_lower for w in ["elimina feed", "borra feed", "quita feed"]):
                    url_match = re.search(r'https?://\S+', query)
                    if url_match:
                        url = url_match.group(0)
                        success = _rss.remove_feed(url)
                        return f"✅ Feed eliminado." if success else "Feed no encontrado."
                    return "Proporciona la URL del feed a eliminar."

                else:
                    # Obtener últimas entradas de todos los feeds o uno específico
                    url_match = re.search(r'https?://\S+', query)
                    if url_match:
                        return await _rss.get_latest(url_match.group(0))

                    feeds = _rss.list_feeds()
                    if feeds:
                        return await _rss.get_latest(feeds[0]["url"])
                    return "No tienes feeds configurados."

            self.register(APIRoute(
                name="rss",
                keywords=["rss", "feed", "feeds", "suscribete",
                         "monitorea blog", "últimas entradas de feed", "blog posts", "noticias del blog"],
                handler=handle_rss,
                description="Gestión y lectura de RSS feeds",
                requires_param=True,
            ))
        except ImportError as e:
            print(f"[APIRegistry] RSS handler no disponible: {e}")

        # ----- Hora / Timezone -----
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            # Mapa de lugares en español → IANA timezone id
            _TZ_MAP = {
                # Latinoamérica
                "peru": "America/Lima",
                "perú": "America/Lima",
                "lima": "America/Lima",
                "colombia": "America/Bogota",
                "bogotá": "America/Bogota",
                "bogota": "America/Bogota",
                "ecuador": "America/Guayaquil",
                "quito": "America/Guayaquil",
                "venezuela": "America/Caracas",
                "caracas": "America/Caracas",
                "argentina": "America/Argentina/Buenos_Aires",
                "buenos aires": "America/Argentina/Buenos_Aires",
                "chile": "America/Santiago",
                "santiago": "America/Santiago",
                "brasil": "America/Sao_Paulo",
                "brazil": "America/Sao_Paulo",
                "sao paulo": "America/Sao_Paulo",
                "são paulo": "America/Sao_Paulo",
                "uruguay": "America/Montevideo",
                "montevideo": "America/Montevideo",
                "paraguay": "America/Asuncion",
                "asunción": "America/Asuncion",
                "asuncion": "America/Asuncion",
                "bolivia": "America/La_Paz",
                "la paz": "America/La_Paz",
                "méxico": "America/Mexico_City",
                "mexico": "America/Mexico_City",
                "ciudad de méxico": "America/Mexico_City",
                "ciudad de mexico": "America/Mexico_City",
                "cuba": "America/Havana",
                "habana": "America/Havana",
                "puerto rico": "America/Puerto_Rico",
                "panamá": "America/Panama",
                "panama": "America/Panama",
                "costa rica": "America/Costa_Rica",
                "guatemala": "America/Guatemala",
                "honduras": "America/Tegucigalpa",
                "el salvador": "America/El_Salvador",
                "nicaragua": "America/Managua",
                "dominicana": "America/Santo_Domingo",
                # Estados Unidos
                "nueva york": "America/New_York",
                "new york": "America/New_York",
                "nueva orleans": "America/Chicago",
                "chicago": "America/Chicago",
                "denver": "America/Denver",
                "los angeles": "America/Los_Angeles",
                "los ángeles": "America/Los_Angeles",
                "san francisco": "America/Los_Angeles",
                "miami": "America/New_York",
                "seattle": "America/Los_Angeles",
                "alaska": "America/Anchorage",
                "hawái": "Pacific/Honolulu",
                "hawaii": "Pacific/Honolulu",
                # Europa
                "españa": "Europe/Madrid",
                "madrid": "Europe/Madrid",
                "barcelona": "Europe/Madrid",
                "reino unido": "Europe/London",
                "uk": "Europe/London",
                "londres": "Europe/London",
                "london": "Europe/London",
                "paris": "Europe/Paris",
                "parís": "Europe/Paris",
                "francia": "Europe/Paris",
                "alemania": "Europe/Berlin",
                "berlin": "Europe/Berlin",
                "berlín": "Europe/Berlin",
                "italia": "Europe/Rome",
                "roma": "Europe/Rome",
                "portugal": "Europe/Lisbon",
                "lisboa": "Europe/Lisbon",
                "países bajos": "Europe/Amsterdam",
                "amsterdam": "Europe/Amsterdam",
                "bélgica": "Europe/Brussels",
                "bruselas": "Europe/Brussels",
                "suiza": "Europe/Zurich",
                "zurich": "Europe/Zurich",
                "zurich": "Europe/Zurich",
                "austria": "Europe/Vienna",
                "viena": "Europe/Vienna",
                "polonia": "Europe/Warsaw",
                "varsovia": "Europe/Warsaw",
                "rusia": "Europe/Moscow",
                "moscú": "Europe/Moscow",
                "moscu": "Europe/Moscow",
                "grecia": "Europe/Athens",
                "atenas": "Europe/Athens",
                "turquía": "Europe/Istanbul",
                "turquia": "Europe/Istanbul",
                "estambul": "Europe/Istanbul",
                "ucrania": "Europe/Kiev",
                "kiev": "Europe/Kiev",
                "finlandia": "Europe/Helsinki",
                "suecia": "Europe/Stockholm",
                "noruega": "Europe/Oslo",
                "dinamarca": "Europe/Copenhagen",
                # Asia
                "japón": "Asia/Tokyo",
                "japon": "Asia/Tokyo",
                "tokio": "Asia/Tokyo",
                "tokyo": "Asia/Tokyo",
                "china": "Asia/Shanghai",
                "beijing": "Asia/Shanghai",
                "shangai": "Asia/Shanghai",
                "shanghai": "Asia/Shanghai",
                "corea": "Asia/Seoul",
                "seúl": "Asia/Seoul",
                "seoul": "Asia/Seoul",
                "india": "Asia/Kolkata",
                "mumbai": "Asia/Kolkata",
                "delhi": "Asia/Kolkata",
                "tailandia": "Asia/Bangkok",
                "bangkok": "Asia/Bangkok",
                "singapur": "Asia/Singapore",
                "singapore": "Asia/Singapore",
                "dubai": "Asia/Dubai",
                "emiratos": "Asia/Dubai",
                "israel": "Asia/Jerusalem",
                "tel aviv": "Asia/Jerusalem",
                "arabia": "Asia/Riyadh",
                "riad": "Asia/Riyadh",
                "iraq": "Asia/Baghdad",
                "bagdad": "Asia/Baghdad",
                "irán": "Asia/Tehran",
                "iran": "Asia/Tehran",
                "pakistán": "Asia/Karachi",
                "pakistan": "Asia/Karachi",
                "bangladesh": "Asia/Dhaka",
                "indonesia": "Asia/Jakarta",
                "jakarta": "Asia/Jakarta",
                "filipinas": "Asia/Manila",
                "manila": "Asia/Manila",
                "vietnam": "Asia/Ho_Chi_Minh",
                "hong kong": "Asia/Hong_Kong",
                "malasia": "Asia/Kuala_Lumpur",
                "kuala lumpur": "Asia/Kuala_Lumpur",
                "myanmar": "Asia/Rangoon",
                "nepal": "Asia/Kathmandu",
                # África
                "egipto": "Africa/Cairo",
                "cairo": "Africa/Cairo",
                "sudáfrica": "Africa/Johannesburg",
                "sudafrica": "Africa/Johannesburg",
                "nigeria": "Africa/Lagos",
                "marruecos": "Africa/Casablanca",
                "kenia": "Africa/Nairobi",
                "etiopía": "Africa/Addis_Ababa",
                # Oceanía
                "australia": "Australia/Sydney",
                "sídney": "Australia/Sydney",
                "sydney": "Australia/Sydney",
                "melbourne": "Australia/Melbourne",
                "nueva zelanda": "Pacific/Auckland",
                "new zealand": "Pacific/Auckland",
                "auckland": "Pacific/Auckland",
                # UTC
                "utc": "UTC",
                "gmt": "Etc/GMT",
            }

            def _find_timezone(query: str) -> tuple[str, str]:
                """Extrae lugar e IANA timezone de la query. Retorna (lugar, tz_id)."""
                q = query.lower()
                # Buscar match exacto de mayor longitud primero
                best_key = None
                best_tz = None
                for key, tz in _TZ_MAP.items():
                    if key in q:
                        if best_key is None or len(key) > len(best_key):
                            best_key = key
                            best_tz = tz
                return (best_key or "local", best_tz or "America/Lima")

            async def handle_time(query: str) -> str:
                from datetime import datetime as _dt
                q_lower = query.lower()

                has_location = any(k in q_lower for k in _TZ_MAP)

                # Si hay una ubicación explícita, úsala siempre (tiene prioridad)
                if has_location:
                    lugar, tz_id = _find_timezone(query)
                    try:
                        tz = ZoneInfo(tz_id)
                        now = _dt.now(tz)
                        lugar_display = lugar.title()
                        hora_fmt = now.strftime("%H:%M:%S")
                        fecha_fmt = now.strftime("%A, %d de %B de %Y")
                        offset = now.strftime("%z")
                        offset_fmt = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
                        return (
                            f"Hora en {lugar_display}: {hora_fmt} "
                            f"({fecha_fmt}) {offset_fmt}"
                        )
                    except ZoneInfoNotFoundError:
                        return f"No pude encontrar la zona horaria para '{lugar}'."

                # Sin ubicación → hora local del servidor
                now = _dt.now()
                return (
                    f"Hora local del servidor: {now.strftime('%H:%M:%S')} "
                    f"({now.strftime('%A, %d de %B de %Y')})"
                )

            self.register(APIRoute(
                name="time_timezone",
                keywords=["que hora es", "qué hora es", "hora en", "hora actual",
                         "hora de", "hora local", "dime la hora", "dime que hora"],
                handler=handle_time,
                description="Hora actual en cualquier zona horaria del mundo",
                requires_param=False,
            ))
        except ImportError as e:
            print(f"[APIRegistry] Time/Timezone handler no disponible: {e}")

    # ------------------------------------------------------------------
    # Router principal
    # ------------------------------------------------------------------

    async def handle(self, query: str, llm_fn=None) -> Optional[str]:
        """
        Detecta la API apropiada y ejecuta el handler.

        Args:
            query: Query del usuario
            llm_fn: Callable opcional para resúmenes (ej. llm.generate)

        Returns:
            Respuesta de la API o None si no hay match
        """
        self._setup_default_routes()

        if not query:
            return None

        # Encontrar el mejor match
        best_route = None
        best_confidence = 0.0

        for route in self._routes:
            matches, confidence = route.matches(query)
            if matches and confidence > best_confidence:
                best_confidence = confidence
                best_route = route

        if best_route and best_confidence > 0.0:
            print(f"[APIRegistry] Route match: {best_route.name} (confidence={best_confidence:.2f})")
            try:
                import inspect
                sig = inspect.signature(best_route.handler)
                # Pasar llm_fn solo si el handler lo acepta. Timeout duro: ningún handler de
                # API externa (Wikipedia, etc.) puede colgar el turno — si tarda, fallback al LLM.
                if "llm_fn" in sig.parameters and llm_fn is not None:
                    result = await asyncio.wait_for(best_route.handler(query, llm_fn=llm_fn), timeout=6.0)
                else:
                    result = await asyncio.wait_for(best_route.handler(query), timeout=6.0)
                return result
            except asyncio.TimeoutError:
                print(f"[APIRegistry] {best_route.name} timeout (>6s) — fallback al LLM")
                return None
            except Exception as e:
                print(f"[APIRegistry] Error en handler {best_route.name}: {e}")
                return f"Error al consultar {best_route.name}: {str(e)}"

        return None

    def get_routes(self) -> List[dict]:
        """Retorna lista de rutas registradas."""
        self._setup_default_routes()
        return [
            {
                "name": r.name,
                "description": r.description,
                "keywords": r.keywords[:5],  # Primeras 5 keywords
            }
            for r in self._routes
        ]

    def is_api_query(self, query: str) -> bool:
        """Detecta rápidamente si una query debería ir al API registry."""
        self._setup_default_routes()
        for route in self._routes:
            matches, _ = route.matches(query)
            if matches:
                return True
        return False


# ------------------------------------------------------------------
# Helpers de extracción de parámetros
# ------------------------------------------------------------------

def _extract_coin_name(query: str) -> str:
    """Extrae el nombre de la criptomoneda de una query."""
    q = query.lower()

    # Lista de criptos conocidas
    coins = [
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "cardano", "ada",
        "dogecoin", "doge", "ripple", "xrp", "polkadot", "dot", "litecoin", "ltc",
        "binance coin", "bnb", "avalanche", "avax", "chainlink", "link", "monero", "xmr",
        "tether", "usdt", "uniswap", "uni", "shiba inu", "shib"
    ]

    for coin in coins:
        if coin in q:
            return coin

    # Fallback: buscar después de palabras clave
    for kw in ["precio de", "cotización de", "valor de", "cuánto vale", "cuanto vale"]:
        if kw in q:
            after = q.split(kw)[-1].strip().split()[0]
            if after and len(after) > 1:
                return after

    return "bitcoin"  # Default


def _clean_search_query(query: str) -> str:
    """
    Convierte la query del usuario en una búsqueda web efectiva.
    - Strip de prefijos de asistente ("Zephyro", "dime", etc.)
    - Strip de prefijos explícitos de comando ("busca en internet", etc.)
    - Reformatea preguntas de gobierno en términos de búsqueda directos con año
    - Añade el año actual en queries con marcadores temporales
    """
    year = datetime.datetime.now().year
    q = query.strip()
    q_lower = q.lower()

    # ── 0. Strip nombre del asistente y muletillas ────────────────────
    # "Zephyro dime quien es..." → "quien es..."
    q = re.sub(r'(?i)^\s*(?:zephyro|oye|hey|hola)\s*[,:]?\s*', '', q).strip()
    q = re.sub(r'(?i)^\s*(?:dime|cuéntame|cuentame|dinos|responde)\s+', '', q).strip()
    q_lower = q.lower()

    # ── 1. Strip solo prefijos de comando explícito ───────────────────
    command_prefixes = [
        "busca en internet y dime", "busquen en internet y dime",
        "busca en internet:", "busca en internet ",
        "busquen en internet:", "busquen en internet ",
        "busquen online:", "busquen online ",
        "busquen en la web:", "busquen en la web ",
        "busquen internet y dime", "busquen internet:",
        "busquen internet ", "busquen ",
        "busca online:", "busca online ",
        "busca en la web:", "busca en la web ",
        "busca en google:", "busca en google ",
        "busca web:", "busca web ",
        "busca y dime ",
        "encuentra en internet:",
        "consulta en internet:",
        "investiga en internet:", "investiga en internet ",
        "investiga online:", "investiga online ",
        "busca ", "search ",
    ]
    for prefix in command_prefixes:
        if q_lower.startswith(prefix.lower()):
            q = q[len(prefix):].strip()
            q_lower = q.lower()
            for connector in ["y dime ", "dime ", "y explica ", "explica "]:
                if q_lower.startswith(connector):
                    q = q[len(connector):].strip()
                    q_lower = q.lower()
            break

    # ── 2. Reformatear preguntas de gobierno/cargo → búsqueda directa ─
    # "quien es el presidente del Peru" → "presidente de Peru 2026"
    gov_match = re.search(
        r'\b(?:quien es|quién es|cual es|cuál es)\b.{0,30}'
        r'\b(presidente|presidenta|primer ministro|primera ministra|'
        r'canciller|gobernador|gobernadora|ministro)\b'
        r'(?:\s+del|\s+de|\s+en)?\s*([\w\s]{2,30}?)'
        r'(?:\s+actualmente|\s+hoy|\s+ahora|\s*\??\s*$)',
        q_lower,
        re.IGNORECASE,
    )
    if gov_match:
        role = gov_match.group(1).rstrip()
        place = gov_match.group(2).strip().rstrip('?.,').strip()
        if place:
            # Evitar duplicar "de" si el rol ya lo incluye
            connector = " " if role.endswith(" de") else " de "
            return f"{role}{connector}{place} {year}"

    # ── 3. Añadir año si hay indicadores temporales ───────────────────
    temporal_markers = ["actualmente", "ahora mismo", "ahorita mismo", "este año", "en este momento", "hoy en día", "este domingo", "este sábado", "en poco tiempo"]
    if any(m in q_lower for m in temporal_markers) and str(year) not in q:
        q = q.rstrip('?. ').strip() + f" {year}"

    # ── 4. Enriquecer queries de noticias con mes+año para sesgar hacia resultados recientes
    if _NEWS_ENRICH_RE.search(q_lower) and str(year) not in q:
        current_month = _MONTH_NAMES_ES[datetime.datetime.now().month - 1]
        q = q.rstrip('?., ').strip() + f" {current_month} {year}"

    return q or query.strip()


def _clean_wiki_query(query: str) -> str:
    """Extrae el término de búsqueda de una query de Wikipedia."""
    import re
    prefixes = [
        "en wikipedia: ", "en wikipedia ",
        "wikipedia:", "wikipedia ",
        "wiki:", "wiki ",
        "definición de ", "definicion de ",
        "significado de ",
        "qué es ", "que es ",
        "qué son ", "que son ",
        "significa ",
    ]
    q = query.strip()
    q_lower = q.lower()

    # Buscar el prefijo en cualquier posición del query (no solo al inicio)
    # Ejemplo: "Zephyro dime que es una mariposa" → "una mariposa"
    best_pos = -1
    best_prefix_len = 0
    for prefix in prefixes:
        pos = q_lower.find(prefix.lower())
        if pos != -1:
            end_pos = pos + len(prefix)
            # Preferir el match más largo si hay varios
            if len(prefix) > best_prefix_len:
                best_pos = end_pos
                best_prefix_len = len(prefix)
    if best_pos > 0:
        q = q[best_pos:].strip()
    elif q_lower.startswith(prefixes[0].lower()):
        q = q[len(prefixes[0]):].strip()

    # Eliminar artículos españoles al inicio
    q = re.sub(r'^(un|una|uno|el|la|los|las|unos|unas)\s+', '', q, flags=re.IGNORECASE).strip()

    # Eliminar signos de puntuación finales
    q = q.rstrip('?.,!¿¡ ')

    return q or query.strip()


# Singleton global
_api_registry = APIRegistry()


async def query_external_api(query: str, llm_fn=None) -> Optional[str]:
    """Función de alto nivel para consultar APIs externas."""
    return await _api_registry.handle(query, llm_fn=llm_fn)


def is_external_api_query(query: str) -> bool:
    """Detecta si una query debería usar APIs externas."""
    return _api_registry.is_api_query(query)


# ── Web classifier + direct search (bypass keyword matching) ─────────

_MONTH_NAMES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

_NEWS_ENRICH_RE = re.compile(
    r'\b(noticias?|últimas?\s+noticias?|ultimas?\s+noticias?|'
    r'actualidad|novedades?|trending|'
    r'qu[eé]\s+pas[oó]|qu[eé]\s+(est[aá]|esta)\s+pasando|'
    r'qu[eé]\s+ocurri[oó]|'
    r'situaci[oó]n actual|segunda vuelta|segunda ronda|balotaje|'
    r'ahorita mismo|ahora mismo|este domingo|este s[aá]bado|'
    r'resultado[s]?\s+de\s+(votos?|elecciones?))\b',
    re.IGNORECASE,
)

_NEVER_SEARCH_PATTERNS = [
    r'^\s*(hola|buenos|buenas|hey|adiós|adios|gracias|chao|bye)\b',
    r'^\s*[\d\s\+\-\*\/\(\)\.,]+\s*$',           # pure math
    r'^\s*(qué es|que es|qué son|que son)\s+\w+\s*$',  # "qué es X" (timeless)
    r'^\s*(quién eres|quien eres|cómo te llamas|como te llamas)',
]

# Patterns that indicate stable knowledge queries — no real-time data needed.
# Used by _tool_loop_needs_search() as Tier B shortcircuit (only when no temporal marker present).
_KNOWLEDGE_ONLY_RE = re.compile(
    r'\b('
    r'expl[íi]ca(?:me)?(?:\s+brevemente|\s+de\s+forma\s+\w+)?\s+c[oó]mo\b|'
    r'expl[íi]came\b|'
    r'c[oó]mo\s+funciona\b|'
    r'c[oó]mo\s+se\s+(?:produce|forma|hace|llama|define|clasifica|divide|genera|crea)\b|'
    r'qu[eé]\s+es\s+(?:la|el|un|una)\s|'
    r'qu[eé]\s+son\s+(?:los|las)\s|'
    r'definici[oó]n\s+de\s|'
    r'concepto\s+de\s|'
    r'proceso\s+de\s+(?:la|el|los|las|un|una)\s|'
    r'mecanismo\s+de\s|'
    r'para\s+qu[eé]\s+sirve\s|'
    r'diferencia\s+entre\s|'
    r'historia\s+de\s+(?:la|el|los|las)\s|'
    r'c[oó]mo\s+(?:surgi[oó]|naci[oó]|se\s+origin[oó]|fue\s+descubiert[oa])\b'
    r')',
    re.IGNORECASE,
)

# Temporal markers that indicate a query needs current data (overrides KNOWLEDGE_ONLY_RE).
_TOOL_LOOP_TEMPORAL_RE = re.compile(
    r'\b('
    r'hoy|ahora|actualmente|ayer|'
    r'esta\s+semana|este\s+mes|este\s+a[ñn]o|'
    r'[úu]ltimo[s]?\s+d[íi]as?|[úu]ltima[s]?\s+(?:horas?|semanas?|noticias?)|'
    r'reciente(?:mente)?|trending|viral|'
    r'hace\s+\d+\s+(?:d[íi]as?|horas?|meses?|semanas?)|'
    r'en\s+(?:vivo|directo)|en\s+este\s+momento'
    r')\b',
    re.IGNORECASE,
)

# Patrones que siempre requieren internet (bypass del filtro de longitud y LLM)
_ALWAYS_SEARCH_PATTERNS = [
    r'\b(actualmente|ahora mismo|hoy en día|este año|en este momento)\b',
    r'\b(últimas noticias|noticias de hoy|noticias recientes|noticias actuales)\b',
    r'\b(quien es el presidente|quién es el presidente|quien es la presidenta|quién es la presidenta)\b',
    r'\b(quien gobierna|quién gobierna|quien manda|quién manda)\b',
    r'\b(resultado de las elecciones|quién ganó las elecciones|quien gano las elecciones)\b',
    r'\b(precio actual|cotización actual|cotizacion actual|tipo de cambio actual)\b',
    r'\b(tipo de cambio|precio del (dólar|dollar|euro|yuan|yen|peso)|(cómo|como) (está|esta|va|viene) el (dólar|euro|peso|yen|yuan|real))\b',
    r'\b(qué pasó|que paso|qué ocurrió|que ocurrio)\b',
    r'\b(busca|buscar)\s+noticias\b',
    r'\bnoticias\s+(?:sobre|de|del|acerca)\b',
    r'\b(guerra|conflicto|crisis)\b.*\b(actual|actualmente|situación|situacion|hoy)\b',
    # Follow-up geopolítico / posición de otros países
    r'\b(que opina[n]?|qué opina[n]?|posici[oó]n de|reacci[oó]n de|respuesta de|c[oó]mo reaccion[oó])\b',
    r'\by\s+(rusia|china|eeuu|estados unidos|europa|iran|israel|onu|nato|otan|occidente|oriente)\b',
    r'\b(rusia|china)\s+(opina|dice|piensa|reacciona|condena|apoya|critica)\b',
    r'\b(declaraci[oó]n|comunicado|postura)\s+(oficial|de|del)\b',
    # Elecciones / encuestas — siempre requieren datos actualizados
    r'\b(encuesta[s]?|sondeo[s]?|encuestadora[s]?)\b',
    r'\b(candidato[s]?|candidata[s]?)\s+.{0,40}(presidenc|elecciones?|congreso|senad)',
    r'\b(elecciones?)\s+(de|del|en|para)\b',
    r'\b(favorito[s]?|puntero[s]?|lider[a]?)\s+(en las|segun|según|de las)\b',
    # Investigar/buscar implícito — el usuario quiere datos actualizados
    r'\b(investig[aáeéuú]\w*|averig[uü][aáeé]\w*)\s+(sobre|acerca|quién|quien|cuál|cual|qué|que|cómo|como|los|las|el|la)\b',
    r'\b(busca|buscar|búscame|buscame|dime|necesito saber)\s+(sobre|acerca|quién|quien|cuál|cual|qué|que|cómo|como|los|las|el|la)\b',
    # "necesito que investigues/busques" — delegated research requests
    r'\b(necesito que|quiero que|podrías|podrias|puedes)\s+(investig\w+|averig\w+|busc\w+)\b',
    # Candidatos / presidencia (sin trailing \b para prefijos parciales)
    r'\b(candidato[s]?|candidata[s]?)\s+(a la |al |para la |presidencial)',
    r'\bpresidencial(es)?\b',
    # Lanzamientos/versiones recientes — datos probablemente post-cutoff del LLM
    # (falsos positivos tipo "salió de casa hace pocos días" son aceptables)
    r'\b(hace\s+(?:pocos|unos|algunos)\s+d[íi]as|hace\s+poco|reci[eé]n(?:temente)?)\b',
    r'\b(sali[oó]|lanz[oó]|anunci[oó]|present[oó]|public[oó])\b.{0,40}\b(modelo|versi[oó]n|update|actualizaci[oó]n|parche|release)\b',
    r'\bnuevo\s+modelo\s+de\b',
    r'\b[uú]ltim[oa]\s+(versi[oó]n|modelo)\s+de\b',
    r'\bacaba\s+de\s+(salir|lanzar|anunciar|presentar)\b',
    r'\b(modelo|versi[oó]n)\s+(?:m[aá]s\s+)?reciente\b',
]

def needs_web_search(query: str, llm_fast, context: str = "") -> bool:
    """
    Layer 0: fast bypass para queries claramente temporales/actuales (0ms).
    Layer A: regex pre-filter para queries que nunca necesitan internet (0ms).
    Layer B: llm_fast YES/NO classifier (~400ms).
    Returns True only when internet data is likely needed.
    Safe: always returns False on any exception.
    """
    import re
    q = query.strip()
    q_lower = q.lower()

    # Layer 0: bypass inmediato para queries de información actual
    for pattern in _ALWAYS_SEARCH_PATTERNS:
        if re.search(pattern, q_lower):
            return True

    # Layer A: obvious non-web queries
    if len(q.split()) <= 3:
        return False
    for pattern in _NEVER_SEARCH_PATTERNS:
        if re.search(pattern, q_lower):
            return False

    # Layer B: ask the fast model
    context_block = ""
    if context:
        context_block = f"\nContexto previo (tema anterior): \"{context}\"\n"
    prompt = (
        "Eres un clasificador. ¿Esta pregunta necesita buscar en internet "
        "para obtener información ACTUALIZADA (noticias, precios actuales, personas "
        "en cargo actualmente, eventos recientes, resultados deportivos)?\n"
        "Si la respuesta viene del conocimiento general o matemáticas: NO.\n"
        "Si necesita datos que cambian con el tiempo o son muy recientes: SI.\n"
        "Si es un follow-up de una búsqueda previa (hay contexto previo): SI.\n\n"
        "Ejemplos:\n"
        "\"¿Qué es Python?\" → NO\n"
        "\"¿Quién es el canciller de Alemania?\" → SI\n"
        "\"¿Cuál es el precio de Tesla ahora?\" → SI\n"
        "\"¿Quién ganó la copa ayer?\" → SI\n"
        "\"Explica la teoría de la relatividad\" → NO\n"
        "\"¿Qué pasó con la guerra en Ucrania?\" → SI\n"
        "\"¿Cuántos años tiene la Tierra?\" → NO\n"
        "\"y que opinan sobre esto\" (con contexto previo) → SI\n\n"
        f"{context_block}"
        f"Pregunta: \"{query}\"\n"
        "Respuesta (solo SI o NO):"
    )
    try:
        result = llm_fast.generate(prompt, force_local=True, preset="router")
        return result.strip().upper().startswith("SI")
    except Exception as e:
        print(f"[WebClassifier] Error: {e}")
        return False


def _tool_loop_search_heuristic(query: str) -> Optional[bool]:
    """Tiers A/B of `_tool_loop_needs_search` (pure regex, no I/O).

    True/False when the patterns decide; None when the query is ambiguous and
    needs the Tier C LLM classifier. Lets the orchestrator skip launching a
    speculative classifier task for queries the regex already resolves.
    """
    q = query.strip()
    for pattern in _ALWAYS_SEARCH_PATTERNS:
        if re.search(pattern, q, re.IGNORECASE):
            return True
    if not _TOOL_LOOP_TEMPORAL_RE.search(q):
        if _KNOWLEDGE_ONLY_RE.search(q):
            return False
        for pattern in _NEVER_SEARCH_PATTERNS:
            if re.search(pattern, q, re.IGNORECASE):
                return False
    return None


async def _tool_loop_needs_search(query: str) -> bool:
    """
    Pre-filter for the orchestrator tool loop: decides whether to include SEARCH_TOOL
    in _tools_for_loop before calling generate_with_tools().

    3-tier logic (all tiers are fast except Tier C ~0.3s):
      Tier A: _ALWAYS_SEARCH_PATTERNS match → True immediately
      Tier B: no temporal marker AND (_KNOWLEDGE_ONLY_RE OR _NEVER_SEARCH_PATTERNS match) → False
      Tier C: ambiguous → DeepSeek v4 Flash one-shot classifier
      Default (error/timeout): True (conservative — better to search than miss)

    This function is async because Tier C uses an async DeepSeek API call.
    """
    import asyncio as _aio

    # Tier A (always-search patterns) and Tier B (knowledge-only without a temporal marker)
    heuristic = _tool_loop_search_heuristic(query)
    if heuristic is not None:
        return heuristic

    # Tier C: ambiguous query → SÍ/NO classifier. Local-first (modelo chico, ~0.3s) y
    # fallback a DeepSeek v4 Flash (cloud) cuando Ollama no está disponible.
    try:
        from core.llm_helpers import call_deepseek_flash, call_local_fast
        prompt = (
            "Eres un clasificador. ¿Esta pregunta necesita buscar en internet "
            "para obtener información ACTUALIZADA (noticias, precios, personas en cargo, "
            "eventos recientes, resultados deportivos)?\n"
            "Si la respuesta viene del conocimiento general del LLM: NO.\n"
            "Si necesita datos que cambian con el tiempo o son muy recientes: SI.\n\n"
            "Ejemplos:\n"
            "\"¿Qué es Python?\" → NO\n"
            "\"¿Quién es el presidente de Argentina?\" → SI\n"
            "\"Explicame cómo funciona la fotosíntesis\" → NO\n"
            "\"¿Cuál es el precio del dólar hoy?\" → SI\n"
            "\"¿Qué pasó con la guerra en Ucrania?\" → SI\n"
            "\"¿Cuántos años tiene la Tierra?\" → NO\n"
            "\"¿Cómo funciona un motor de combustión interna?\" → NO\n"
            "\"Últimas noticias de Argentina\" → SI\n"
            f"\nPregunta: \"{query}\"\n"
            "Respuesta (solo SI o NO):"
        )
        local = await call_local_fast(prompt, max_tokens=4)
        if local:
            return local.strip().upper().startswith("SI")
        result = await _aio.wait_for(call_deepseek_flash(prompt), timeout=3.0)
        if result:
            return result.strip().upper().startswith("SI")
    except Exception:
        pass

    return True  # conservative default: allow search on any error/timeout/ambiguity


async def search_web_direct(query: str, llm_fn=None) -> Optional[str]:
    """
    Direct web search — bypasses keyword routing.
    Called when needs_web_search() returns True.
    """
    _deep_kw = ["a fondo", "en profundidad", "profundo", "profunda", "investigación profunda"]
    deep = any(kw in query.lower() for kw in _deep_kw)
    try:
        from modules.search.web_search import WebSearch
        _ws = WebSearch()
        if llm_fn:
            return await _ws.search_and_summarize(query, llm_fn, max_results=4, deep=deep)
        results = await _ws.search(query, max_results=5, deep=deep)
        if not results:
            return f"No encontré resultados en internet para: '{query}'"
        lines = [f"**Resultados web para:** {query}\n"]
        for i, r in enumerate(results[:5], 1):
            lines.append(f"{i}. **{r.title}**")
            lines.append(f"   {r.snippet[:200]}")
            lines.append(f"   {r.url}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[WebDirect] Error: {e}")
        return None
