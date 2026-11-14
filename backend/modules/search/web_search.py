# modules/search/web_search.py
"""
Motor de búsqueda web OVERKILL para Zephyro.
5 capas de fallback para que NUNCA falle:

  ┌─────────────────────────────────────────────┐
  │  L1  Tavily  (API, rápido, fiable)          │
  │  L2  Tavily advanced  (si L1 vacío)         │
  │  L3  DuckDuckGo  (gratis, sin key)          │
  │  L4  DuckDuckGo retry  (otra región)        │
  │  L5  Scraping directo Google (nuclear)       │
  └─────────────────────────────────────────────┘

Cada capa tiene su propio timeout, retry y error handling.
Si TODAS fallan, retorna lista vacía (nunca lanza excepción).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
from typing import Optional, List

from core.llm_cache import LLMCache

# ── Logging ──────────────────────────────────────────────────────────
_TAG = "[WebSearch]"

def _log(msg: str, level: str = "INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"{ts} [{level}] {_TAG} {msg}")

def _log_err(msg: str):
    _log(msg, "ERROR")

def _log_warn(msg: str):
    _log(msg, "WARN")


# ── Cache ────────────────────────────────────────────────────────────
_search_cache = LLMCache(max_size=200)


# ── News query detection ──────────────────────────────────────────────
_NEWS_QUERY_RE = re.compile(
    r'\b('
    r'noticias?|últimas?\s+noticias?|ultimas?\s+noticias?|'
    r'noticias?\s+de\s+hoy|noticias?\s+recientes?|noticias?\s+actuales?|'
    r'news|breaking|trending|'
    r'actualidad|novedades?|acontecimiento[s]?|'
    r'qu[eé]\s+pas[oó]|qu[eé]\s+(est[aá]|esta)\s+pasando|'
    r'qu[eé]\s+ocurri[oó]|qu[eé]\s+ha\s+pasado|'
    r'esta\s+semana\s+(en|de)|'
    r'(lanzamiento|anunci[oó]|revelaci[oó]n|revel[oó]|anunci[oó])\s+.{0,30}(hoy|ayer|reciente)|'
    # Eventos de actualidad política/electoral — siempre requieren datos frescos
    r'situaci[oó]n actual|estado actual|'
    r'segunda vuelta|segunda ronda|balotaje|'
    r'ahorita mismo|ahora mismo|'
    r'este domingo|este s[aá]bado|este lunes|hoy mismo|'
    r'en poco tiempo|pr[oó]ximo[s]?\s+(d[ií]a[s]?|fin de semana|domingo)|'
    r'encuesta[s]?\s+(electoral|de intenci[oó]n)|'
    r'c[oó]mo\s+van?\s+(la[s]?\s+)?(elecciones?|votaciones?|el\s+conteo|los\s+resultados)|'
    r'qui[eé]n\s+(va\s+ganando|gan[oó]|gana(?:r[aá])?|lleva\s+la\s+delantera|encabeza)|'
    r'(resultado[s]?|conteo)\s+(?:de\s+(?:la[s]?\s+)?)?(votos?|elecciones?|electoral(?:es)?)'
    r')\b',
    re.IGNORECASE,
)


def _is_news_query(query: str) -> bool:
    """Detecta si la query busca noticias o eventos recientes."""
    return bool(_NEWS_QUERY_RE.search(query))


# ── Result ───────────────────────────────────────────────────────────
class WebSearchResult:
    """Resultado individual de búsqueda web."""
    __slots__ = ("title", "url", "snippet", "full_text", "engine", "published_date", "score")

    def __init__(self, title: str, url: str, snippet: str,
                 full_text: str = "", engine: str = "", published_date: str = "",
                 score: float = 0.0):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.full_text = full_text
        self.engine = engine
        self.published_date = published_date
        self.score = score  # relevancia 0..1 (Tavily la provee; otros engines = 0.0)

    def __repr__(self) -> str:
        return f"WebSearchResult(title={self.title[:50]!r}, engine={self.engine!r})"


# ── "Fresh info" enhancement: elegir la mejor fuente + sintetizar novedad ──
# Dominios oficiales/autoritativos (gobierno/edu) y medios reconocidos.
_AUTHORITATIVE_TLDS = (".gob.", ".gov.", ".gob", ".gov", ".edu", ".int", ".mil")
_AUTHORITATIVE_DOMAINS = frozenset({
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk",
    "elcomercio.pe", "infobae.com", "rpp.pe", "gestion.pe", "larepublica.pe",
    "lanacion.com.ar", "clarin.com", "infobae.com.ar",
    "elpais.com", "elmundo.es", "nytimes.com", "washingtonpost.com",
    "theguardian.com", "bloomberg.com", "ft.com", "wsj.com",
})


def _domain_authority(url: str) -> float:
    """0..1: 1.0 dominio oficial (gob/edu), 0.7 medio reconocido, 0.0 resto."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return 0.0
    if not host:
        return 0.0
    if any(t in host for t in _AUTHORITATIVE_TLDS):
        return 1.0
    if any(host == d or host.endswith("." + d) for d in _AUTHORITATIVE_DOMAINS):
        return 0.7
    return 0.0


def pick_best_source(results, exclude_url: Optional[str] = None):
    """Elige la fuente más autoritativa/reciente de una lista de WebSearchResult.

    score = autoridad_dominio + 0.5*score_Tavily + 0.2*(tiene published_date)
    Devuelve el mejor por encima del umbral, o None si nada califica.
    `exclude_url` permite saltar la URL que el fast-answer ya deep-browseó.
    """
    best = None
    best_total = 0.0
    _excl = (exclude_url or "").rstrip("/")
    for r in results or []:
        url = getattr(r, "url", "") or ""
        if not url or (_excl and url.rstrip("/") == _excl):
            continue
        auth = _domain_authority(url)
        tav = min(max(float(getattr(r, "score", 0.0) or 0.0), 0.0), 1.0)
        recency = 0.2 if getattr(r, "published_date", "") else 0.0
        total = auth + 0.5 * tav + recency
        if total > best_total:
            best_total = total
            best = r
    # Umbral: requiere algo de autoridad o un score Tavily decente.
    if best is None or best_total < 0.5:
        return None
    return best


def _parse_enhancement(raw: str) -> dict:
    """Parsea la salida estructurada de synthesize_enhancement."""
    if not raw or not raw.strip():
        return {"novel": False, "answer": "", "whats_new": ""}
    m_nov = re.search(r'NOVEDAD:\s*(s[ií]|no)', raw, re.IGNORECASE)
    novel = bool(m_nov and m_nov.group(1).lower().startswith("s"))
    whats_new = ""
    m_chg = re.search(r'CAMBIO:\s*(.+)', raw, re.IGNORECASE)
    if m_chg:
        whats_new = m_chg.group(1).strip().splitlines()[0].strip()
    answer = ""
    m_ans = re.search(r'RESPUESTA:\s*(.+)', raw, re.IGNORECASE | re.DOTALL)
    if m_ans:
        answer = m_ans.group(1).strip()
    # Sin respuesta enriquecida, no hay novedad útil que ofrecer.
    if novel and not answer:
        novel = False
    return {"novel": novel, "answer": answer, "whats_new": whats_new}


async def synthesize_enhancement(query: str, fast_answer: str, deep_content: str,
                                 source_label: str, llm_fn) -> dict:
    """Compara la respuesta ya dada vs el contenido de la mejor fuente.

    Devuelve {"novel": bool, "answer": str, "whats_new": str}. Si la fuente no
    aporta datos nuevos/más recientes → novel=False (no se ofrece nada).
    `llm_fn` es síncrono (mismo patrón _synth_fn del orchestrator) → se corre en thread.
    """
    if not deep_content or not deep_content.strip():
        return {"novel": False, "answer": "", "whats_new": ""}
    verified_date = time.strftime("%Y-%m-%d %H:%M")
    prompt = (
        f"Fecha actual verificada del servidor: {verified_date}.\n"
        f"El usuario preguntó: '{query}'.\n\n"
        f"Ya le respondí esto (RESPUESTA PREVIA):\n{fast_answer}\n\n"
        f"Luego consulté una fuente más autoritativa ({source_label}) y extraje:\n"
        f"{deep_content[:3500]}\n\n"
        f"Tarea: ¿la fuente aporta datos NUEVOS o MÁS RECIENTES que la respuesta previa "
        f"(números, fechas, porcentajes, hechos concretos)? Si hay contradicción, preferí "
        f"lo más reciente/autoritativo y mencioná ambos con su fecha.\n"
        f"Respondé EXACTAMENTE en este formato (sin texto extra):\n"
        f"NOVEDAD: SI|NO\n"
        f"CAMBIO: <una frase de qué cambia; vacío si NO>\n"
        f"RESPUESTA: <respuesta enriquecida en español con la info más actual y su fecha; "
        f"vacío si NO>"
    )
    try:
        raw = await asyncio.to_thread(lambda: llm_fn(prompt))
    except Exception as _e:
        _log_warn(f"synthesize_enhancement falló: {_e}")
        return {"novel": False, "answer": "", "whats_new": ""}
    return _parse_enhancement(raw if isinstance(raw, str) else str(raw or ""))


# ── Engine layer timeouts ────────────────────────────────────────────
_TAVILY_TIMEOUT = 12       # seconds
_SERPER_TIMEOUT = 10       # seconds
_DDG_TIMEOUT = 10
_SCRAPE_TIMEOUT = 12


class WebSearch:
    """
    Motor de búsqueda web con 5 capas de fallback.

    Uso:
        ws = WebSearch()
        results = await ws.search("presidente de peru 2026")
        summary  = await ws.search_and_summarize("precio bitcoin", llm_fn)
    """

    def __init__(self, max_results: int = 5):
        self.max_results = max_results
        # Lazy availability flags (None = not checked yet)
        self._ddg_available: Optional[bool] = None
        self._trafilatura_available: Optional[bool] = None
        self._tavily_available: Optional[bool] = None
        self._tavily_key: Optional[str] = None

    # ==================================================================
    # Dependency checks (lazy, cached)
    # ==================================================================

    def _check_ddg(self) -> bool:
        if self._ddg_available is None:
            try:
                from ddgs import DDGS  # noqa
                self._ddg_available = True
            except ImportError:
                try:
                    from duckduckgo_search import DDGS  # noqa
                    self._ddg_available = True
                except ImportError:
                    self._ddg_available = False
                    _log_warn("ddgs no instalado. pip install ddgs")
        return self._ddg_available

    def _check_trafilatura(self) -> bool:
        if self._trafilatura_available is None:
            try:
                import trafilatura  # noqa
                self._trafilatura_available = True
            except ImportError:
                self._trafilatura_available = False
        return self._trafilatura_available

    def _check_tavily(self) -> bool:
        """Verifica si Tavily está disponible. Re-checks env cada vez."""
        key = os.getenv("TAVILY_API_KEY", "").strip()
        if not key:
            self._tavily_available = False
            self._tavily_key = None
            return False
        self._tavily_key = key
        if self._tavily_available is None:
            try:
                from tavily import TavilyClient  # noqa
                self._tavily_available = True
            except ImportError:
                self._tavily_available = False
                _log_warn("tavily-python no instalado. pip install tavily-python")
        return self._tavily_available

    def _check_serper(self) -> bool:
        """Verifica si Serper API está configurada."""
        return bool(os.getenv("SERPER_API_KEY", "").strip())

    # ==================================================================
    # SEARCH — orquestador principal con 6 capas
    # ==================================================================

    async def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        deep: bool = False,
        is_news: Optional[bool] = None,
    ) -> List[WebSearchResult]:
        """
        Busca en la web con 5 capas de fallback. NUNCA lanza excepción.

        Layers:
            L1: Tavily basic (o news si is_news=True)
            L2: Tavily advanced (auto si L1 vacío)
            L3: DuckDuckGo / DuckDuckGo News
            L4: DuckDuckGo retry (región alternativa)
            L5: Scraping directo Google
        """
        n = max_results or self.max_results
        t0 = time.monotonic()

        # Auto-detect news queries
        if is_news is None:
            is_news = _is_news_query(query)

        # ── Cache check ──────────────────────────────────────────────
        depth_tag = "deep" if deep else "basic"
        if is_news:
            # 30-minute time buckets for news to avoid serving stale results
            time_bucket = int(time.time() / 1800)
            cache_key = f"news:{query}:{n}:{time_bucket}"
        else:
            cache_key = f"search:{query}:{n}:{depth_tag}"
        cached = _search_cache.get(cache_key)
        if cached:
            try:
                data = json.loads(cached)
                results = [WebSearchResult(**r) for r in data]
                _log(f"Cache hit ({len(results)} results) for: {query!r}")
                return results
            except Exception:
                pass

        results: List[WebSearchResult] = []
        engine_used = "none"

        # ── L1: Tavily basic ─────────────────────────────────────────
        if self._check_tavily() and not deep:
            tag = "tavily-news" if is_news else "tavily-basic"
            _log(f"L1 {tag} → {query!r}")
            try:
                results = await asyncio.wait_for(
                    self._search_tavily(query, n, deep=False, is_news=is_news),
                    timeout=_TAVILY_TIMEOUT,
                )
                if results:
                    engine_used = tag
                    _log(f"L1 {tag} ✓ {len(results)} results [{time.monotonic()-t0:.1f}s]")
            except asyncio.TimeoutError:
                _log_warn(f"L1 {tag} TIMEOUT ({_TAVILY_TIMEOUT}s)")
            except Exception as e:
                _log_err(f"L1 {tag} FAILED: {e}")

        # ── L2: Tavily advanced ──────────────────────────────────────
        if not results and self._check_tavily():
            _log(f"L2 Tavily advanced → {query!r}")
            try:
                results = await asyncio.wait_for(
                    self._search_tavily(query, n, deep=True, is_news=is_news),
                    timeout=_TAVILY_TIMEOUT + 5,
                )
                if results:
                    engine_used = "tavily-advanced"
                    _log(f"L2 Tavily advanced ✓ {len(results)} results [{time.monotonic()-t0:.1f}s]")
            except asyncio.TimeoutError:
                _log_warn(f"L2 Tavily advanced TIMEOUT ({_TAVILY_TIMEOUT+5}s)")
            except Exception as e:
                _log_err(f"L2 Tavily advanced FAILED: {e}")

        # ── L3: Serper API (Google results via API oficial) ──────────
        if not results and self._check_serper():
            _log(f"L3 Serper → {query!r}")
            try:
                results = await asyncio.wait_for(
                    self._search_serper(query, n, is_news=is_news),
                    timeout=_SERPER_TIMEOUT,
                )
                if results:
                    engine_used = "serper-news" if is_news else "serper"
                    _log(f"L3 Serper ✓ {len(results)} results [{time.monotonic()-t0:.1f}s]")
            except asyncio.TimeoutError:
                _log_warn(f"L3 Serper TIMEOUT ({_SERPER_TIMEOUT}s)")
            except Exception as e:
                _log_err(f"L3 Serper FAILED: {e}")

        # ── L4: DuckDuckGo ───────────────────────────────────────────
        if not results:
            ddg_tag = "ddg-news" if is_news else "ddg"
            _log(f"L4 {ddg_tag} → {query!r}")
            try:
                results = await asyncio.wait_for(
                    self._search_ddg(query, n, region="wt-wt", is_news=is_news),
                    timeout=_DDG_TIMEOUT,
                )
                if results:
                    engine_used = ddg_tag
                    _log(f"L4 {ddg_tag} ✓ {len(results)} results [{time.monotonic()-t0:.1f}s]")
            except asyncio.TimeoutError:
                _log_warn(f"L4 {ddg_tag} TIMEOUT ({_DDG_TIMEOUT}s)")
            except Exception as e:
                _log_err(f"L4 {ddg_tag} FAILED: {e}")

        # ── L5: DuckDuckGo retry (región alternativa) ────────────────
        if not results:
            _log(f"L5 DuckDuckGo retry (region=xl-es) → {query!r}")
            try:
                results = await asyncio.wait_for(
                    self._search_ddg(query, n, region="xl-es", is_news=is_news),
                    timeout=_DDG_TIMEOUT,
                )
                if results:
                    engine_used = "ddg-news-retry" if is_news else "ddg-retry"
                    _log(f"L5 DuckDuckGo retry ✓ {len(results)} results [{time.monotonic()-t0:.1f}s]")
            except asyncio.TimeoutError:
                _log_warn(f"L5 DuckDuckGo retry TIMEOUT ({_DDG_TIMEOUT}s)")
            except Exception as e:
                _log_err(f"L5 DuckDuckGo retry FAILED: {e}")

        # ── L6: Scraping directo Google (nuclear) ────────────────────
        if not results:
            _log(f"L6 Google scrape (NUCLEAR) → {query!r}")
            try:
                results = await asyncio.wait_for(
                    self._search_google_scrape(query, n),
                    timeout=_SCRAPE_TIMEOUT,
                )
                if results:
                    engine_used = "google-scrape"
                    _log(f"L5 Google scrape ✓ {len(results)} results [{time.monotonic()-t0:.1f}s]")
            except asyncio.TimeoutError:
                _log_warn(f"L5 Google scrape TIMEOUT ({_SCRAPE_TIMEOUT}s)")
            except Exception as e:
                _log_err(f"L5 Google scrape FAILED: {e}")

        # ── Final ────────────────────────────────────────────────────
        elapsed = time.monotonic() - t0
        if results:
            _log(f"✓ SEARCH OK via {engine_used}: {len(results)} results for {query!r} [{elapsed:.1f}s]")
            cache_data = json.dumps([
                {"title": r.title, "url": r.url, "snippet": r.snippet,
                 "full_text": "", "engine": r.engine, "published_date": r.published_date}
                for r in results
            ])
            _search_cache.set(cache_key, cache_data)
        else:
            _log_err(f"✗ ALL 5 LAYERS FAILED for {query!r} [{elapsed:.1f}s]")

        return results

    # ==================================================================
    # L3: Serper API
    # ==================================================================

    async def _search_serper(
        self, query: str, max_results: int, is_news: bool = False,
    ) -> List[WebSearchResult]:
        """Búsqueda via Serper API. Modo news: endpoint /news."""
        import json as _json
        key = os.getenv("SERPER_API_KEY", "").strip()
        if not key:
            return []

        endpoint = "https://google.serper.dev/news" if is_news else "https://google.serper.dev/search"
        headers = {"X-API-KEY": key, "Content-Type": "application/json"}
        payload = {"q": query, "num": max_results, "gl": "pe", "hl": "es"}
        if is_news:
            # Filtro de frescura estilo Google: restringe a la última semana
            payload["tbs"] = "qdr:w"

        import requests as _requests
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: _requests.post(
                    endpoint,
                    headers=headers,
                    data=_json.dumps(payload),
                    timeout=_SERPER_TIMEOUT,
                )
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            _log_warn(f"Serper request error: {e}")
            return []

        result_key = "news" if is_news else "organic"
        tag = "serper-news" if is_news else "serper"
        results = []
        for item in data.get(result_key, [])[:max_results]:
            results.append(WebSearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", "") or item.get("body", ""),
                full_text="",
                engine=tag,
                published_date=item.get("date", "") if is_news else "",
            ))
        return results

    # ==================================================================
    # L1/L2: Tavily
    # ==================================================================

    async def _search_tavily(
        self, query: str, max_results: int, deep: bool = False, is_news: bool = False,
    ) -> List[WebSearchResult]:
        """Búsqueda via Tavily API. Modo news: topic='news' + days=7."""
        from tavily import TavilyClient

        key = self._tavily_key
        if not key:
            raise RuntimeError("TAVILY_API_KEY no configurada")

        client = TavilyClient(api_key=key)
        depth = "advanced" if deep else "basic"

        def _sync():
            kwargs = {"search_depth": depth, "max_results": max_results}
            if is_news:
                kwargs["topic"] = "news"
                kwargs["days"] = 7
            return client.search(query, **kwargs)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _sync)

        tag = "tavily-news" if is_news else f"tavily-{depth}"
        results = []
        for r in response.get("results", []):
            results.append(WebSearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
                engine=tag,
                published_date=r.get("published_date", "") or "",
                score=float(r.get("score", 0.0) or 0.0),
            ))
        return results

    # ==================================================================
    # L3/L4: DuckDuckGo
    # ==================================================================

    async def _search_ddg(
        self, query: str, max_results: int, region: str = "wt-wt", is_news: bool = False,
    ) -> List[WebSearchResult]:
        """Búsqueda via DuckDuckGo. Modo news: ddgs.news() con timelimit semanal."""
        if not self._check_ddg():
            return []

        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        loop = asyncio.get_event_loop()

        if is_news:
            def _sync_news():
                with DDGS() as ddgs:
                    try:
                        return list(ddgs.news(query, region=region, max_results=max_results, timelimit='w'))
                    except Exception:
                        # Fallback a text con timelimit si ddgs.news() no está disponible
                        return list(ddgs.text(query, region=region, max_results=max_results, timelimit='w'))

            raw = await loop.run_in_executor(None, _sync_news)
            results = []
            for r in raw:
                results.append(WebSearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", "") or r.get("href", ""),
                    snippet=r.get("body", ""),
                    engine=f"ddg-news({region})",
                    published_date=r.get("date", ""),
                ))
        else:
            def _sync():
                with DDGS() as ddgs:
                    return list(ddgs.text(query, region=region, max_results=max_results))

            raw = await loop.run_in_executor(None, _sync)
            results = []
            for r in raw:
                results.append(WebSearchResult(
                    title=r.get("title", ""),
                    url=r.get("href", ""),
                    snippet=r.get("body", ""),
                    engine=f"ddg({region})",
                ))

        return results

    # ==================================================================
    # L5: Google scraping directo (nuclear fallback)
    # ==================================================================

    async def _search_google_scrape(
        self, query: str, max_results: int,
    ) -> List[WebSearchResult]:
        """
        Scraping directo de Google. NO requiere API key.
        Fallback nuclear cuando todo lo demás falla.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                "Gecko/20100101 Firefox/128.0"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        encoded_q = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded_q}&hl=es&num={max_results+5}"

        def _sync():
            import requests
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.text

        loop = asyncio.get_event_loop()
        html = await loop.run_in_executor(None, _sync)

        return self._parse_google_html(html, max_results)

    @staticmethod
    def _parse_google_html(html: str, max_results: int) -> List[WebSearchResult]:
        """
        Parsea HTML de Google con múltiples estrategias de extracción.
        """
        results: List[WebSearchResult] = []

        # Strategy 1: <a href="/url?q=URL"><h3>TITLE</h3></a>
        link_pattern = re.compile(
            r'<a[^>]+href="/url\?q=([^"&]+)[^"]*"[^>]*>.*?<h3[^>]*>(.*?)</h3>',
            re.DOTALL,
        )
        for m in link_pattern.finditer(html):
            if len(results) >= max_results:
                break
            raw_url = urllib.parse.unquote(m.group(1))
            title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if not title or not raw_url.startswith("http"):
                continue

            # Buscar snippet cercano
            snippet = ""
            snippet_html = html[m.end():m.end() + 1500]
            sn_match = re.search(
                r'<(?:span|div)[^>]*class="[^"]*(?:VwiC3b|lEBKkf|IsZvec)[^"]*"[^>]*>(.*?)</(?:span|div)>',
                snippet_html, re.DOTALL,
            )
            if sn_match:
                snippet = re.sub(r'<[^>]+>', '', sn_match.group(1)).strip()
            if not snippet:
                text_blocks = re.findall(r'>([^<]{40,300})<', snippet_html)
                if text_blocks:
                    snippet = text_blocks[0].strip()

            results.append(WebSearchResult(
                title=title, url=raw_url, snippet=snippet[:500],
                engine="google-scrape",
            ))

        # Strategy 2: href directo a HTTPS (sin /url?q=)
        if not results:
            alt_pattern = re.compile(
                r'<a[^>]+href="(https?://(?!www\.google\.com)[^"]+)"[^>]*>.*?<h3[^>]*>(.*?)</h3>',
                re.DOTALL,
            )
            for m in alt_pattern.finditer(html):
                if len(results) >= max_results:
                    break
                raw_url = m.group(1)
                title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                if not title:
                    continue
                results.append(WebSearchResult(
                    title=title, url=raw_url, snippet="",
                    engine="google-scrape-alt",
                ))

        return results

    # ==================================================================
    # Extracción de artículos completos
    # ==================================================================

    async def fetch_article(self, url: str) -> str:
        """Extrae texto completo de un artículo web."""
        cache_key = f"article:{url}"
        cached = _search_cache.get(cache_key)
        if cached:
            return cached

        if not self._check_trafilatura():
            return await self._fetch_article_fallback(url)

        try:
            import trafilatura

            def _sync_fetch():
                downloaded = trafilatura.fetch_url(url)
                if downloaded:
                    return trafilatura.extract(
                        downloaded,
                        include_comments=False,
                        include_tables=True,
                        no_fallback=False,
                    )
                return None

            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, _sync_fetch)

            if text:
                text = text[:3000]
                _search_cache.set(cache_key, text)
                return text
            return ""
        except Exception as e:
            _log_err(f"Error extrayendo artículo {url}: {e}")
            return ""

    async def fetch_article_rich(self, url: str) -> dict:
        """
        Extrae texto, imágenes, tablas y precios de un artículo web.
        Retorna: {text, images, tables, prices, json_ld}
        """
        cache_key = f"rich:{url}"
        cached = _search_cache.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass

        result = {"text": "", "images": [], "tables": [], "prices": [], "json_ld": []}

        if not self._check_trafilatura():
            result["text"] = await self._fetch_article_fallback(url)
            return result

        try:
            import trafilatura
            from modules.search.content_parser import (
                extract_images, extract_tables, tables_to_markdown,
                extract_prices, extract_json_ld,
            )

            def _sync_fetch_rich():
                downloaded = trafilatura.fetch_url(url)
                if not downloaded:
                    return None, None
                text = trafilatura.extract(
                    downloaded,
                    include_comments=False,
                    include_tables=True,
                    no_fallback=False,
                )
                return text, downloaded

            loop = asyncio.get_event_loop()
            text, html = await loop.run_in_executor(None, _sync_fetch_rich)

            if text:
                result["text"] = text[:3000]
            if html:
                result["images"] = extract_images(html, url)
                result["tables"] = extract_tables(html)
                result["prices"] = extract_prices(text or "")
                result["json_ld"] = extract_json_ld(html)

            _search_cache.set(cache_key, json.dumps(result))
        except Exception as e:
            _log_err(f"fetch_article_rich error for {url}: {e}")

        return result

    async def _fetch_article_fallback(self, url: str) -> str:
        """Fallback: requests + HTML parser básico."""
        try:
            import requests
            from html.parser import HTMLParser

            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.texts: list[str] = []
                    self._skip = False

                def handle_starttag(self, tag, attrs):
                    if tag in ("script", "style", "nav", "footer", "header"):
                        self._skip = True

                def handle_endtag(self, tag):
                    if tag in ("script", "style", "nav", "footer", "header"):
                        self._skip = False

                def handle_data(self, data):
                    if not self._skip:
                        stripped = data.strip()
                        if len(stripped) > 20:
                            self.texts.append(stripped)

            resp = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/128.0"
            })
            resp.raise_for_status()
            parser = TextExtractor()
            parser.feed(resp.text)
            return " ".join(parser.texts)[:3000]
        except Exception as e:
            _log_err(f"Fallback fetch failed for {url}: {e}")
            return ""

    # ==================================================================
    # Deep Browse — Extracción con Playwright (JavaScript rendering)
    # ==================================================================

    # Singleton headless browser para deep browse (no interfiere con Edge visible)
    _deep_browser = None
    _deep_browser_lock = asyncio.Lock()

    @classmethod
    async def _get_deep_browser(cls):
        """Lazy-init de instancia headless Chromium para deep browse."""
        async with cls._deep_browser_lock:
            if cls._deep_browser is not None:
                try:
                    # Verificar que sigue funcional
                    if cls._deep_browser["page"] and not cls._deep_browser["page"].is_closed():
                        return cls._deep_browser
                except Exception:
                    pass
                # Limpiar instancia rota
                await cls._cleanup_deep_browser()

            try:
                from playwright.async_api import async_playwright
                pw = await async_playwright().start()
                browser = await pw.chromium.launch(
                    headless=True,
                    args=[
                        "--no-first-run",
                        "--disable-default-apps",
                        "--disable-extensions",
                        "--disable-gpu",
                    ],
                    timeout=30000,
                )
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
                    ),
                )
                page = await context.new_page()
                cls._deep_browser = {
                    "pw": pw,
                    "browser": browser,
                    "context": context,
                    "page": page,
                }
                _log("Deep browse: Chromium headless inicializado")
                return cls._deep_browser
            except Exception as e:
                _log_err(f"Deep browse: No se pudo iniciar Chromium headless: {e}")
                return None

    @classmethod
    async def _cleanup_deep_browser(cls):
        """Cierra la instancia headless de deep browse."""
        if cls._deep_browser:
            try:
                await cls._deep_browser["context"].close()
            except Exception:
                pass
            try:
                await cls._deep_browser["browser"].close()
            except Exception:
                pass
            try:
                await cls._deep_browser["pw"].stop()
            except Exception:
                pass
            cls._deep_browser = None

    async def deep_browse(self, url: str, query: str = "", timeout: int = 20) -> dict:
        """
        Navega a una URL con Playwright headless y extrae contenido renderizado.
        
        A diferencia de fetch_article() (HTTP simple + trafilatura), esto:
        - Ejecuta JavaScript (para SPAs, dashboards, páginas dinámicas)
        - Espera a que el contenido cargue completamente
        - Detecta problemas de acceso (CAPTCHA, login wall, 403)
        
        Args:
            url: URL a navegar
            query: Contexto de lo que busca el usuario (para error messages)
            timeout: Timeout en segundos para la navegación
            
        Returns:
            dict con keys:
                - "success": bool
                - "content": str (texto extraído, max 5000 chars)
                - "title": str (título de la página)
                - "error": str (mensaje de error si success=False)
                - "error_type": str (timeout|login_wall|captcha|blocked|empty|unknown)
        """
        cache_key = f"deep:{url}"
        cached = _search_cache.get(cache_key)
        if cached:
            return cached

        db = await self._get_deep_browser()
        if db is None:
            # Fallback a fetch_article (HTTP simple)
            text = await self.fetch_article(url)
            if text:
                result = {"success": True, "content": text, "title": "", "error": "", "error_type": ""}
            else:
                result = {"success": False, "content": "", "title": "", "error": "No se pudo iniciar el navegador headless ni extraer contenido por HTTP", "error_type": "unknown"}
            return result

        page = db["page"]
        try:
            # Navegar con timeout
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

            # Verificar código de respuesta
            status = response.status if response else 0
            if status in (401, 403):
                result = {
                    "success": False, "content": "", "title": "",
                    "error": f"Acceso denegado a {url} (HTTP {status})",
                    "error_type": "blocked",
                }
                return result

            # Esperar un poco más para que JavaScript cargue contenido dinámico
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass  # networkidle timeout es OK — ya tenemos domcontentloaded

            title = await page.title()

            # Detectar CAPTCHA
            captcha_indicators = await page.evaluate("""() => {
                const html = document.body?.innerHTML?.toLowerCase() || '';
                return {
                    hasCaptcha: html.includes('captcha') || html.includes('recaptcha') || 
                                html.includes('hcaptcha') || html.includes('challenge-platform'),
                    hasLoginForm: !!(document.querySelector('form[action*="login"]') || 
                                    document.querySelector('input[type="password"]')),
                    bodyLength: (document.body?.innerText || '').length,
                }
            }""")

            if captcha_indicators.get("hasCaptcha"):
                result = {
                    "success": False, "content": "", "title": title,
                    "error": f"La página {url} tiene protección anti-bot (CAPTCHA). No puedo extraer el contenido automáticamente.",
                    "error_type": "captcha",
                }
                return result

            if captcha_indicators.get("hasLoginForm") and captcha_indicators.get("bodyLength", 0) < 500:
                result = {
                    "success": False, "content": "", "title": title,
                    "error": f"La página {url} requiere login para acceder al contenido.",
                    "error_type": "login_wall",
                }
                return result

            # Extraer texto visible
            text = await page.evaluate("""() => {
                // Remover scripts, styles, nav, footer, ads
                const removeSelectors = ['script', 'style', 'nav', 'footer', 'header', 
                    'iframe', '.ad', '.advertisement', '[role="banner"]', '[role="navigation"]'];
                for (const sel of removeSelectors) {
                    document.querySelectorAll(sel).forEach(el => el.remove());
                }
                return document.body?.innerText || '';
            }""")

            if not text or len(text.strip()) < 50:
                result = {
                    "success": False, "content": "", "title": title,
                    "error": f"No se pudo extraer contenido útil de {url}. La página puede estar vacía o requerir interacción.",
                    "error_type": "empty",
                }
                return result

            # Limpiar y truncar
            text = re.sub(r'\n{3,}', '\n\n', text.strip())
            text = text[:5000]

            result = {
                "success": True,
                "content": text,
                "title": title,
                "error": "",
                "error_type": "",
            }
            _search_cache.set(cache_key, result)
            return result

        except Exception as e:
            error_str = str(e).lower()
            if "timeout" in error_str:
                result = {
                    "success": False, "content": "", "title": "",
                    "error": f"La página {url} tardó demasiado en cargar (>{timeout}s).",
                    "error_type": "timeout",
                }
            else:
                result = {
                    "success": False, "content": "", "title": "",
                    "error": f"Error accediendo a {url}: {e}",
                    "error_type": "unknown",
                }
            return result

    # ==================================================================
    # Búsqueda + Resumen con LLM
    # ==================================================================

    async def search_and_summarize(
        self,
        query: str,
        llm_fn,
        max_results: int = 3,
        language: str = "es",
        deep: bool = False,
        is_news: Optional[bool] = None,
    ) -> str:
        """Busca en la web y genera un resumen usando LLM. Auto deep-browse si snippets insuficientes."""
        if is_news is None:
            is_news = _is_news_query(query)

        results = await self.search(query, max_results=max_results, deep=deep, is_news=is_news)

        if not results:
            return f"No encontré resultados en internet para: '{query}'"

        mode_tag = "[news]" if is_news else ""
        _log(f"Summarizing {len(results)} results{mode_tag} from {results[0].engine} for: {query!r}")

        context_parts = []
        for i, r in enumerate(results, 1):
            date_line = f"\n   Publicado: {r.published_date}" if r.published_date else ""
            context_parts.append(
                f"[{i}] **{r.title}**\n"
                f"URL: {r.url}{date_line}\n"
                f"Resumen: {r.snippet[:500]}"
            )
        context_str = "\n\n".join(context_parts)

        # Auto deep-browse: si los snippets son cortos o contienen indicadores
        # de datos incompletos, navegar a la fuente para extraer más información.
        # Para queries de noticias/volátiles se intenta SIEMPRE: los snippets del
        # índice del buscador suelen estar desactualizados o incompletos.
        total_snippet_len = sum(len(r.snippet) for r in results)
        deep_content = ""
        if is_news or total_snippet_len < 400 or len(results) <= 1:
            _log(f"Auto deep-browse: is_news={is_news}, snippets={total_snippet_len} chars")
            for r in results[:2]:
                browse_result = await self.deep_browse(r.url, query)
                if browse_result.get("success") and browse_result.get("content"):
                    deep_content = browse_result["content"][:3000]
                    _log(f"Deep browse exitoso: {r.url} ({len(deep_content)} chars)")
                    break
                elif browse_result.get("error"):
                    _log_warn(f"Deep browse: {browse_result['error']}")

        lang_instruction = (
            "Responde en español." if language == "es"
            else "Answer in English."
        )

        deep_section = ""
        if deep_content:
            deep_section = (
                f"\n\nCONTENIDO EXTRAÍDO DE LA FUENTE:\n{deep_content}\n"
            )

        verified_date = time.strftime("%Y-%m-%d %H:%M")

        if is_news:
            summary_prompt = (
                f"Fecha actual verificada del servidor: {verified_date}.\n\n"
                f"Los siguientes son resultados de búsqueda web obtenidos HOY para: '{query}'\n\n"
                f"RESULTADOS:\n{context_str}"
                f"{deep_section}\n\n"
                f"Instrucciones CRÍTICAS:\n"
                f"1. Usa SOLAMENTE la información de las fuentes. NO inventes datos ni eventos.\n"
                f"2. Si un resultado tiene fecha de publicación, menciónala (ej: 'según el artículo del 28/05...').\n"
                f"3. Si toda la información parece tener más de 7 días de antigüedad, advierte: "
                f"'No encontré noticias muy recientes; la información más actual disponible es de [fecha]'.\n"
                f"4. Si hay contradicciones entre fuentes, menciónalas.\n"
                f"5. NO deduzcas ni extrapoles eventos que no estén explícitamente en las fuentes.\n"
                f"6. Sintetiza en un párrafo continuo de 4-6 oraciones que conecte los datos "
                f"de las distintas fuentes entre sí (no las resumas fuente por fuente); "
                f"relaciona las cifras y hechos. Termina siempre con una oración completa. "
                f"{lang_instruction}\n\n"
                f"Respuesta:"
            )
        else:
            summary_prompt = (
                f"Basándote en los siguientes resultados de búsqueda web recientes, "
                f"responde de forma concisa a: '{query}'\n\n"
                f"RESULTADOS:\n{context_str}"
                f"{deep_section}\n\n"
                f"Instrucciones: Sintetiza la información más relevante en un párrafo "
                f"continuo de 4-6 oraciones que conecte los datos de las distintas fuentes "
                f"entre sí (no las resumas fuente por fuente). "
                f"Si los resultados tienen fechas o datos concretos, inclúyelos. "
                f"Termina siempre con una oración completa. "
                f"{lang_instruction}\n\n"
                f"Respuesta:"
            )

        try:
            summary = await asyncio.get_event_loop().run_in_executor(
                None, lambda: llm_fn(summary_prompt)
            )
            sources = "\n".join(
                f"  • [{r.title[:60]}]({r.url})" for r in results[:3]
            )
            return f"{summary}\n\n**Fuentes:**\n{sources}"
        except Exception as e:
            _log_err(f"Error generando resumen con LLM: {e}")
            lines = [f"Resultados para '{query}':\n"]
            for r in results:
                lines.append(f"• **{r.title}**: {r.snippet[:200]}")
                lines.append(f"  {r.url}")
            return "\n".join(lines)

    # ==================================================================
    # Wikipedia
    # ==================================================================

    async def wikipedia_summary(self, query: str, sentences: int = 5) -> str:
        """Obtiene un resumen de Wikipedia."""
        cache_key = f"wiki:{query}"
        cached = _search_cache.get(cache_key)
        if cached:
            return cached

        try:
            import wikipedia

            def _sync_wiki():
                wikipedia.set_lang("es")
                try:
                    page = wikipedia.page(query, auto_suggest=True)
                    summary = wikipedia.summary(query, sentences=sentences, auto_suggest=True)
                    return f"**{page.title}** (Wikipedia)\n\n{summary}\n\n🔗 {page.url}"
                except wikipedia.exceptions.DisambiguationError as e:
                    if e.options:
                        page = wikipedia.page(e.options[0])
                        summary = page.summary[:1000]
                        return f"**{page.title}** (Wikipedia)\n\n{summary}\n\n🔗 {page.url}"
                    return f"Múltiples resultados para '{query}': {', '.join(e.options[:5])}"
                except wikipedia.exceptions.PageError:
                    wikipedia.set_lang("en")
                    summary = wikipedia.summary(query, sentences=sentences)
                    return f"*(Wikipedia en inglés)*\n\n{summary}"

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _sync_wiki)
            _search_cache.set(cache_key, result)
            return result
        except ImportError:
            results = await self.search(f"wikipedia {query}", max_results=1)
            if results and results[0].url:
                text = await self.fetch_article(results[0].url)
                if text:
                    return text[:1500]
            return f"No pude obtener información de Wikipedia sobre '{query}'"
        except Exception as e:
            _log_err(f"Error Wikipedia: {e}")
            # Intentar DuckDuckGo como fallback antes de rendirse
            try:
                results = await self.search(f"{query} wikipedia", max_results=1)
                if results and results[0].url:
                    text = await self.fetch_article(results[0].url)
                    if text and len(text) > 100:
                        return text[:1500]
            except Exception:
                pass
            return ""

    # ==================================================================
    # Diagnóstico
    # ==================================================================

    def get_status(self) -> dict:
        """Retorna estado de todos los motores."""
        return {
            "tavily": self._check_tavily(),
            "tavily_key_set": bool(os.getenv("TAVILY_API_KEY", "").strip()),
            "tavily_key_preview": (self._tavily_key or "")[:14] + "..." if self._tavily_key else "NOT SET",
            "duckduckgo": self._check_ddg(),
            "trafilatura": self._check_trafilatura(),
            "google_scrape": True,  # siempre disponible como fallback nuclear
            "cache_stats": _search_cache.stats,
        }
