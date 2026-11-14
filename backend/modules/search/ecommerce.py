# modules/search/ecommerce.py
"""
Búsqueda de productos en tiendas online: MercadoLibre, eBay, AliExpress.

- MercadoLibreProvider  → API pública gratuita, multi-región
- EbayProvider          → eBay Finding API (requiere EBAY_APP_ID)
- AliExpressProvider    → Playwright stealth (sin API key)
- EcommerceSearch       → Orquestador paralelo con normalización de precios
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

from core.llm_cache import LLMCache

_TAG = "[Ecommerce]"


def _log(msg: str, level: str = "INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"{ts} [{level}] {_TAG} {msg}")


def _log_warn(msg: str):
    _log(msg, "WARN")


def _log_err(msg: str):
    _log(msg, "ERROR")


_ecommerce_cache = LLMCache(max_size=200)

# ── ML site IDs ───────────────────────────────────────────────────────────────

ML_SITES: dict[str, str] = {
    "argentina": "MLA", "mla": "MLA",
    "mexico": "MLM", "méxico": "MLM", "mlm": "MLM",
    "peru": "MPE", "perú": "MPE", "mpe": "MPE",
    "chile": "MLC", "mlc": "MLC",
    "colombia": "MCO", "mco": "MCO",
    "uruguay": "MLU", "mlu": "MLU",
    "venezuela": "MLV", "mlv": "MLV",
    "panama": "MPA", "panamá": "MPA", "mpa": "MPA",
    "costa rica": "MCR", "mcr": "MCR",
    "bolivia": "MBO", "mbo": "MBO",
    "dominicana": "MRD", "mrd": "MRD",
    "brasil": "MLB", "brazil": "MLB", "mlb": "MLB",
    "ecuador": "MEC", "mec": "MEC",
}

_ML_SITE_KEYWORDS_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in ML_SITES if len(k) > 3) + r')\b',
    re.IGNORECASE,
)

# Tasas aproximadas a USD para ordenamiento (no para mostrar al usuario)
_USD_RATES: dict[str, float] = {
    "USD": 1.0, "EUR": 1.08, "ARS": 0.00085, "MXN": 0.058,
    "PEN": 0.27, "CLP": 0.001, "COP": 0.00024, "UYU": 0.025,
    "BOB": 0.14, "BRL": 0.18, "DOP": 0.017, "CRC": 0.0019,
    "VES": 0.000027, "PAB": 1.0, "GTQ": 0.13,
}

_PRICE_SYMBOLS: dict[str, str] = {
    "ARS": "$", "MXN": "$", "PEN": "S/", "USD": "$",
    "CLP": "$", "COP": "$", "EUR": "€", "BRL": "R$",
    "UYU": "$", "BOB": "Bs", "DOP": "RD$",
}


def _to_usd(amount: float, currency: str) -> float:
    rate = _USD_RATES.get(currency.upper(), 1.0)
    return round(amount * rate, 4)


# ── ProductResult ─────────────────────────────────────────────────────────────

@dataclass
class ProductResult:
    title: str
    price: float
    currency: str
    url: str
    image_url: str
    source: str             # "mercadolibre" | "ebay" | "aliexpress"
    price_usd: float = 0.0
    rating: Optional[float] = None
    reviews_count: Optional[int] = None
    seller: Optional[str] = None
    condition: Optional[str] = None    # "new" | "used"
    site: Optional[str] = None         # ML site ID (e.g. "MLA")

    def __post_init__(self):
        if self.price_usd == 0.0 and self.price > 0:
            self.price_usd = _to_usd(self.price, self.currency)

    def format_price(self) -> str:
        symbol = _PRICE_SYMBOLS.get(self.currency, "")
        if self.currency == "PEN":
            return f"S/ {self.price:,.2f}"
        if symbol:
            return f"{symbol}{self.price:,.2f} {self.currency}"
        return f"{self.price:,.2f} {self.currency}"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "price": self.price,
            "currency": self.currency,
            "price_usd": self.price_usd,
            "url": self.url,
            "image_url": self.image_url,
            "source": self.source,
            "rating": self.rating,
            "reviews_count": self.reviews_count,
            "seller": self.seller,
            "condition": self.condition,
            "site": self.site,
        }


# ── MercadoLibreProvider ──────────────────────────────────────────────────────

class MercadoLibreProvider:
    """
    Búsqueda en MercadoLibre.

    Flujo de autenticación:
    1. Si ML_CLIENT_ID + ML_CLIENT_SECRET configurados → OAuth app token (6h TTL).
    2. Si ML_ACCESS_TOKEN configurado → usa directamente.
    3. Sin credenciales → log warning, skip.

    Registrar app gratis en: https://developers.mercadolibre.com.ar/
    """

    _BASE = "https://api.mercadolibre.com/sites/{site}/search"
    _TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

    # Class-level token cache (shared between instances)
    _cached_token: Optional[str] = None
    _token_expires_at: float = 0.0

    def _detect_site(self, query: str) -> str:
        m = _ML_SITE_KEYWORDS_RE.search(query)
        if m:
            kw = m.group(1).lower()
            return ML_SITES.get(kw, "MLA")
        return os.getenv("ML_DEFAULT_SITE", "MLA")

    def _clean_query(self, query: str) -> str:
        q = _ML_SITE_KEYWORDS_RE.sub("", query)
        q = re.sub(
            r'\b(busca|buscar|buscar en|encuentra|precio de|cuánto cuesta|cuanto cuesta'
            r'|cuánto vale|cuanto vale|mercadolibre|aliexpress|ebay|comparar|comparar precios)\b',
            '', q, flags=re.IGNORECASE,
        )
        return re.sub(r'\s+', ' ', q).strip()

    async def _get_token(self) -> Optional[str]:
        """Obtiene un token de acceso usando client credentials o env var."""
        # Direct token
        direct = os.getenv("ML_ACCESS_TOKEN", "").strip()
        if direct:
            return direct

        # Client credentials flow
        client_id = os.getenv("ML_CLIENT_ID", "").strip()
        client_secret = os.getenv("ML_CLIENT_SECRET", "").strip()
        if not (client_id and client_secret):
            _log_warn(
                "MercadoLibre: configura ML_CLIENT_ID + ML_CLIENT_SECRET (gratis en "
                "developers.mercadolibre.com.ar) o ML_ACCESS_TOKEN para usar esta fuente"
            )
            return None

        # Use cached token if still valid
        if MercadoLibreProvider._cached_token and time.time() < MercadoLibreProvider._token_expires_at:
            return MercadoLibreProvider._cached_token

        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status != 200:
                        _log_warn(f"ML token request failed: {resp.status}")
                        return None
                    data = await resp.json()
                    token = data.get("access_token")
                    expires_in = int(data.get("expires_in") or 21600)
                    MercadoLibreProvider._cached_token = token
                    MercadoLibreProvider._token_expires_at = time.time() + expires_in - 300
                    return token
        except Exception as exc:
            _log_err(f"ML token error: {exc}")
            return None

    async def search(
        self,
        query: str,
        max_results: int = 5,
        site: Optional[str] = None,
    ) -> list[ProductResult]:
        import aiohttp

        token = await self._get_token()
        if not token:
            return []

        resolved_site = site or self._detect_site(query)
        clean = self._clean_query(query)
        if not clean:
            clean = query

        url = self._BASE.format(site=resolved_site)
        params = {"q": clean, "limit": min(max_results, 20)}
        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        _log_warn(f"ML API {resp.status} para site={resolved_site}")
                        return []
                    data = await resp.json()
        except Exception as exc:
            _log_err(f"ML search error: {exc}")
            return []

        results: list[ProductResult] = []
        for item in data.get("results", []):
            try:
                price = float(item.get("price") or 0)
                if price <= 0:
                    continue
                currency = item.get("currency_id") or "USD"
                seller_obj = item.get("seller") or {}
                seller = seller_obj.get("nickname") if isinstance(seller_obj, dict) else None

                results.append(ProductResult(
                    title=item.get("title") or "",
                    price=price,
                    currency=currency,
                    url=item.get("permalink") or "",
                    image_url=item.get("thumbnail") or "",
                    source="mercadolibre",
                    seller=seller,
                    condition=item.get("condition"),
                    site=resolved_site,
                ))
            except Exception as exc:
                _log_warn(f"ML item parse: {exc}")

        _log(f"ML {resolved_site}: {len(results)} resultados para '{clean}'")
        return results


# ── EbayProvider ──────────────────────────────────────────────────────────────

class EbayProvider:
    """Búsqueda en eBay via Finding API (requiere EBAY_APP_ID en .env)."""

    _URL = "https://svcs.ebay.com/services/search/FindingService/v1"

    async def search(self, query: str, max_results: int = 5) -> list[ProductResult]:
        app_id = os.getenv("EBAY_APP_ID", "").strip()
        if not app_id:
            _log_warn("EBAY_APP_ID no configurado — skip eBay")
            return []

        import aiohttp

        params = {
            "OPERATION-NAME": "findItemsByKeywords",
            "SERVICE-VERSION": "1.0.0",
            "SECURITY-APPNAME": app_id,
            "RESPONSE-DATA-FORMAT": "JSON",
            "keywords": query,
            "paginationInput.entriesPerPage": str(min(max_results, 20)),
            "outputSelector": "PictureURLLarge",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self._URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        _log_warn(f"eBay API {resp.status}")
                        return []
                    data = await resp.json()
        except Exception as exc:
            _log_err(f"eBay search error: {exc}")
            return []

        try:
            items = (
                data.get("findItemsByKeywordsResponse", [{}])[0]
                .get("searchResult", [{}])[0]
                .get("item", [])
            )
        except (IndexError, KeyError, TypeError):
            return []

        results: list[ProductResult] = []
        for item in items:
            try:
                price_info = item.get("sellingStatus", [{}])[0].get("currentPrice", [{}])[0]
                price = float(price_info.get("__value__") or 0)
                if price <= 0:
                    continue
                currency = price_info.get("@currencyId") or "USD"

                gallery = (item.get("galleryURL") or [""])[0]
                big_img = (item.get("pictureURLLarge") or [""])[0] or gallery

                cond_info = (item.get("condition") or [{}])[0]
                cond_name = (cond_info.get("conditionDisplayName") or [""])[0]
                condition = "used" if "used" in cond_name.lower() else "new"

                results.append(ProductResult(
                    title=(item.get("title") or [""])[0],
                    price=price,
                    currency=currency,
                    url=(item.get("viewItemURL") or [""])[0],
                    image_url=big_img,
                    source="ebay",
                    condition=condition,
                ))
            except Exception as exc:
                _log_warn(f"eBay item parse: {exc}")

        _log(f"eBay: {len(results)} resultados para '{query}'")
        return results


# ── AliExpressProvider ────────────────────────────────────────────────────────

class AliExpressProvider:
    """
    Búsqueda en AliExpress via Playwright stealth.
    Extrae datos del JSON embebido en scripts de la página.
    """

    _SEARCH_URL = "https://www.aliexpress.com/wholesale?SearchText={query}&sortType=default"

    async def search(self, query: str, max_results: int = 5) -> list[ProductResult]:
        try:
            from playwright.async_api import async_playwright  # noqa
        except ImportError:
            _log_warn("Playwright no instalado — skip AliExpress")
            return []

        try:
            return await asyncio.wait_for(self._scrape(query, max_results), timeout=30.0)
        except asyncio.TimeoutError:
            _log_warn("AliExpress timeout (30s)")
            return []
        except Exception as exc:
            _log_err(f"AliExpress error: {exc}")
            return []

    async def _scrape(self, query: str, max_results: int) -> list[ProductResult]:
        from playwright.async_api import async_playwright

        url = self._SEARCH_URL.format(query=urllib.parse.quote_plus(query))

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="es-MX",
            )
            page = await context.new_page()

            # Stealth: ocultar webdriver
            await page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "window.chrome={runtime:{}};"
            )

            # Aplicar playwright-stealth si está disponible
            try:
                from playwright_stealth import stealth_async
                await stealth_async(page)
            except ImportError:
                pass

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)

                results = await self._extract_json_data(page, max_results)
                if not results:
                    results = await self._extract_dom_data(page, max_results)
            finally:
                await browser.close()

        _log(f"AliExpress: {len(results)} resultados para '{query}'")
        return results

    async def _extract_json_data(self, page, max_results: int) -> list[ProductResult]:
        try:
            scripts: list[str] = await page.evaluate(
                """() => {
                    const out = [];
                    for (const s of document.querySelectorAll('script')) {
                        const t = s.textContent || '';
                        if (t.includes('"originalPrice"') || t.includes('"salePrice"')
                            || t.includes('__INIT_DATA__') || t.includes('window._dida_config_')) {
                            out.push(t.substring(0, 150000));
                        }
                    }
                    return out;
                }"""
            )
        except Exception:
            return []

        results: list[ProductResult] = []
        for text in scripts:
            items = self._parse_ali_items(text, max_results - len(results))
            results.extend(items)
            if len(results) >= max_results:
                break
        return results[:max_results]

    def _parse_ali_items(self, script_text: str, limit: int) -> list[ProductResult]:
        import json as _json

        results: list[ProductResult] = []
        try:
            m = re.search(r'"items"\s*:\s*(\[.{20,200000}?\])', script_text, re.DOTALL)
            if m:
                items: list = _json.loads(m.group(1))
                for item in items[:limit]:
                    if isinstance(item, dict):
                        r = self._parse_ali_item(item)
                        if r:
                            results.append(r)
        except Exception:
            pass
        return results

    def _parse_ali_item(self, item: dict) -> Optional[ProductResult]:
        try:
            title = (
                item.get("title") or item.get("subject") or item.get("name") or ""
            )
            if not title:
                return None

            price_obj = (
                item.get("prices") or item.get("salePrice") or
                item.get("price") or item.get("originalPrice") or {}
            )
            if isinstance(price_obj, dict):
                price = float(
                    price_obj.get("min") or price_obj.get("value") or
                    price_obj.get("salePrice") or 0
                )
                currency = price_obj.get("currencyCode") or "USD"
            else:
                price = float(price_obj or 0)
                currency = "USD"

            if price <= 0:
                return None

            raw_url = item.get("productDetailUrl") or item.get("detailUrl") or ""
            url = raw_url if raw_url.startswith("http") else ("https://www.aliexpress.com" + raw_url)

            image = (
                item.get("imageUrl") or item.get("image") or
                item.get("mainImageUrl") or ""
            )

            rating_obj = item.get("starRating") or item.get("ratings") or {}
            rating: Optional[float] = None
            reviews: Optional[int] = None
            if isinstance(rating_obj, dict):
                r = rating_obj.get("averageStar") or rating_obj.get("value")
                rating = float(r) if r else None
                rc = rating_obj.get("totalCount") or rating_obj.get("count")
                reviews = int(rc) if rc else None

            seller_obj = item.get("store") or item.get("seller") or {}
            seller: Optional[str] = None
            if isinstance(seller_obj, dict):
                seller = seller_obj.get("storeName") or seller_obj.get("name")

            return ProductResult(
                title=title,
                price=price,
                currency=currency,
                url=url,
                image_url=image,
                source="aliexpress",
                rating=rating,
                reviews_count=reviews,
                seller=seller,
                condition="new",
            )
        except Exception:
            return None

    async def _extract_dom_data(self, page, max_results: int) -> list[ProductResult]:
        try:
            items: list[dict] = await page.evaluate(
                """(limit) => {
                    const sel = '[class*="product-card"],[class*="item-card"],[data-product-id]';
                    const out = [];
                    for (const card of [...document.querySelectorAll(sel)].slice(0, limit)) {
                        const titleEl = card.querySelector('[class*="title"],h2,h3');
                        const priceEl = card.querySelector('[class*="price"]');
                        const imgEl   = card.querySelector('img');
                        const linkEl  = card.querySelector('a[href*="/item/"]');
                        const title   = titleEl ? titleEl.textContent.trim() : '';
                        const priceT  = priceEl ? priceEl.textContent.trim()  : '';
                        const image   = imgEl ? (imgEl.src || imgEl.dataset.src || '') : '';
                        const url     = linkEl ? linkEl.href : '';
                        if (title && priceT) out.push({title, priceT, image, url});
                    }
                    return out;
                }""",
                max_results,
            )
        except Exception:
            return []

        results: list[ProductResult] = []
        for item in items or []:
            price_m = re.search(r'[\d]+(?:[.,]\d+)*', (item.get("priceT") or "").replace(",", "."))
            if not price_m:
                continue
            try:
                price = float(price_m.group(0))
            except ValueError:
                continue
            raw_url = item.get("url") or ""
            url = raw_url if raw_url.startswith("http") else ("https://www.aliexpress.com" + raw_url)
            results.append(ProductResult(
                title=item.get("title") or "",
                price=price,
                currency="USD",
                url=url,
                image_url=item.get("image") or "",
                source="aliexpress",
                condition="new",
            ))
        return results


# ── EcommerceSearch ───────────────────────────────────────────────────────────

class EcommerceSearch:
    """Orquestador de búsqueda e-commerce multi-provider."""

    def __init__(self):
        self._ml = MercadoLibreProvider()
        self._ebay = EbayProvider()
        self._ali = AliExpressProvider()

    async def search(
        self,
        query: str,
        sources: list[str] | None = None,
        max_per_source: int = 5,
        ml_site: Optional[str] = None,
    ) -> list[ProductResult]:
        if sources is None:
            sources = ["mercadolibre", "ebay", "aliexpress"]

        cache_key = f"ecom:{query}:{','.join(sorted(sources))}:{max_per_source}:{ml_site or ''}"
        cached = _ecommerce_cache.get(cache_key)
        if cached and isinstance(cached, list):
            return [ProductResult(**r) for r in cached]

        tasks = []
        if "mercadolibre" in sources:
            tasks.append(("ml", self._ml.search(query, max_per_source, ml_site)))
        if "ebay" in sources:
            tasks.append(("ebay", self._ebay.search(query, max_per_source)))
        if "aliexpress" in sources:
            tasks.append(("ali", self._ali.search(query, max_per_source)))

        if not tasks:
            return []

        labels, coros = zip(*tasks)
        raw = await asyncio.gather(*coros, return_exceptions=True)

        all_results: list[ProductResult] = []
        for label, result in zip(labels, raw):
            if isinstance(result, Exception):
                _log_warn(f"{label} provider error: {result}")
            elif isinstance(result, list):
                all_results.extend(result)

        all_results.sort(key=lambda x: x.price_usd)

        _ecommerce_cache.set(cache_key, [r.to_dict() for r in all_results], ttl=1800)
        return all_results

    @staticmethod
    def format_results(results: list[ProductResult], query: str) -> str:
        """Formatea resultados de productos para consumo del LLM."""
        if not results:
            return f"No se encontraron productos para: '{query}'"

        source_labels = {
            "mercadolibre": "MercadoLibre",
            "ebay": "eBay",
            "aliexpress": "AliExpress",
        }

        parts = [f'=== Resultados de productos para "{query}" ===\n']
        by_source: dict[str, list[ProductResult]] = {}
        for r in results:
            by_source.setdefault(r.source, []).append(r)

        n = 1
        for source, items in by_source.items():
            site = items[0].site or ""
            label = source_labels.get(source, source)
            if site:
                label = f"{label} ({site})"
            parts.append(f"[{label}]")

            for r in items:
                parts.append(f"{n}. {r.title}")
                price_str = r.format_price()
                usd_note = f" (~${r.price_usd:.2f} USD)" if r.currency not in ("USD",) else ""
                parts.append(f"   Precio: {price_str}{usd_note}")

                if r.seller:
                    parts.append(f"   Vendedor: {r.seller}")

                meta: list[str] = []
                if r.condition:
                    meta.append("Nuevo" if r.condition == "new" else "Usado")
                if r.rating:
                    star_str = f"★ {r.rating:.1f}"
                    if r.reviews_count:
                        star_str += f" ({r.reviews_count:,} reseñas)"
                    meta.append(star_str)
                if meta:
                    parts.append(f"   {' | '.join(meta)}")

                if r.url:
                    parts.append(f"   {r.url}")
                if r.image_url:
                    parts.append(f"   [imagen] {r.image_url}")

                parts.append("")
                n += 1
            parts.append("")

        return "\n".join(parts)
