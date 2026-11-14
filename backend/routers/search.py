"""Search router — web search, crypto, RSS endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth_middleware import require_admin, require_auth

router = APIRouter()


@router.post("/search/web")
async def web_search(request: Request, session: dict = Depends(require_auth)):
    """Búsqueda web via DuckDuckGo + extracción con trafilatura"""
    data = await request.json()
    query = data.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    try:
        from modules.search.web_search import WebSearch
        ws = WebSearch()
        results = await ws.search(query, max_results=5)
        return {
            "query": query,
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in results
            ]
        }
    except Exception as e:
        return {"error": str(e), "results": []}


@router.post("/search/article")
async def fetch_article(request: Request, session: dict = Depends(require_auth)):
    """Extrae texto de un artículo web"""
    data = await request.json()
    url = data.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    try:
        from modules.search.web_search import WebSearch
        ws = WebSearch()
        text = await ws.fetch_article(url)
        return {"url": url, "text": text, "length": len(text)}
    except Exception as e:
        return {"error": str(e), "text": ""}


@router.post("/search/products")
async def product_search(request: Request, session: dict = Depends(require_auth)):
    """Búsqueda de productos en MercadoLibre, eBay y AliExpress"""
    data = await request.json()
    query = data.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    sources = data.get("sources") or None
    max_results = min(int(data.get("max_results") or 5), 10)
    ml_site = data.get("ml_site") or None

    try:
        from modules.search.ecommerce import EcommerceSearch
        searcher = EcommerceSearch()
        results = await searcher.search(
            query=query,
            sources=sources,
            max_per_source=max_results,
            ml_site=ml_site,
        )
        return {
            "query": query,
            "count": len(results),
            "results": [r.to_dict() for r in results],
            "formatted": searcher.format_results(results, query),
        }
    except Exception as e:
        return {"error": str(e), "results": []}


@router.get("/crypto/price/{coin}")
async def get_crypto_price(coin: str, session: dict = Depends(require_auth), currency: str = "usd"):
    """Precio actual de una criptomoneda (CoinGecko)"""
    try:
        from modules.integrations.finance import get_crypto_price
        result = await get_crypto_price(coin, currency)
        return {"coin": coin, "formatted": result}
    except Exception as e:
        return {"error": str(e)}


@router.get("/crypto/top")
async def get_top_cryptos_endpoint(session: dict = Depends(require_auth), limit: int = 10):
    """Top N criptomonedas por market cap"""
    try:
        from modules.integrations.finance import get_top_cryptos
        result = await get_top_cryptos(limit)
        return {"formatted": result}
    except Exception as e:
        return {"error": str(e)}


@router.get("/rss/feeds")
async def list_rss_feeds(session: dict = Depends(require_auth)):
    """Lista los RSS feeds configurados"""
    try:
        from modules.integrations.rss_monitor import RSSMonitor
        monitor = RSSMonitor()
        return {"feeds": monitor.list_feeds()}
    except Exception as e:
        return {"error": str(e)}


@router.post("/rss/add")
async def add_rss_feed(request: Request, session: dict = Depends(require_admin)):
    """Agrega un RSS feed para monitorear"""
    data = await request.json()
    url = data.get("url", "")
    name = data.get("name", "")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    try:
        from modules.integrations.rss_monitor import RSSMonitor
        monitor = RSSMonitor()
        success = monitor.add_feed(url, name)
        return {"success": success}
    except Exception as e:
        return {"error": str(e)}


@router.get("/rss/latest/{feed_url:path}")
async def get_rss_latest(feed_url: str, session: dict = Depends(require_auth), max_entries: int = 5):
    """Obtiene las últimas entradas de un feed"""
    try:
        from modules.integrations.rss_monitor import RSSMonitor
        monitor = RSSMonitor()
        entries = await monitor.fetch_feed(feed_url, max_entries)
        return {"entries": [e.to_dict() for e in entries]}
    except Exception as e:
        return {"error": str(e)}
