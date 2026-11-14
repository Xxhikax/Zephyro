# modules/integrations/finance.py
"""
Integración con APIs financieras gratuitas.
- CoinGecko: Precios de criptomonedas (sin API key)
- Historial de 7 días
- Alertas básicas de precio
"""
import asyncio
import time
from typing import Optional, Dict, List, Tuple
from core.llm_cache import LLMCache

_finance_cache = LLMCache(max_size=100)

# TTL corto para datos financieros (2 minutos)
_PRICE_TTL = 120


class CryptoAPI:
    """Cliente para CoinGecko API (completamente gratuita, sin autenticación)."""

    BASE_URL = "https://api.coingecko.com/api/v3"

    # Mapa de nombres comunes a IDs de CoinGecko
    COIN_ALIASES = {
        "bitcoin": "bitcoin", "btc": "bitcoin",
        "ethereum": "ethereum", "eth": "ethereum",
        "binance coin": "binancecoin", "bnb": "binancecoin",
        "solana": "solana", "sol": "solana",
        "cardano": "cardano", "ada": "cardano",
        "ripple": "ripple", "xrp": "ripple",
        "dogecoin": "dogecoin", "doge": "dogecoin",
        "polkadot": "polkadot", "dot": "polkadot",
        "litecoin": "litecoin", "ltc": "litecoin",
        "avalanche": "avalanche-2", "avax": "avalanche-2",
        "chainlink": "chainlink", "link": "chainlink",
        "uniswap": "uniswap", "uni": "uniswap",
        "monero": "monero", "xmr": "monero",
        "tether": "tether", "usdt": "tether",
        "usdc": "usd-coin",
        "shiba inu": "shiba-inu", "shib": "shiba-inu",
    }

    def __init__(self):
        self._session = None

    def _resolve_coin_id(self, query: str) -> str:
        """Resuelve nombre/alias a ID de CoinGecko."""
        q = query.lower().strip()
        return self.COIN_ALIASES.get(q, q.replace(" ", "-"))

    async def _get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        """Realiza GET request a CoinGecko."""
        import requests

        url = f"{self.BASE_URL}{endpoint}"
        try:
            def _sync():
                headers = {"Accept": "application/json", "User-Agent": "Zephyro/1.0"}
                resp = requests.get(url, params=params or {}, headers=headers, timeout=10)
                resp.raise_for_status()
                return resp.json()

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _sync)
        except Exception as e:
            print(f"[CryptoAPI] Error en request {url}: {e}")
            return None

    async def get_price(self, coin: str, vs_currency: str = "usd") -> Optional[Dict]:
        """
        Obtiene el precio actual de una criptomoneda.

        Returns:
            dict con keys: price, change_24h, market_cap, volume_24h, last_updated
        """
        coin_id = self._resolve_coin_id(coin)
        cache_key = f"price:{coin_id}:{vs_currency}"

        # Cache muy corto (2 minutos)
        cached = _finance_cache.get(cache_key)
        if cached:
            import json
            try:
                return json.loads(cached)
            except Exception:
                pass

        data = await self._get(
            "/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": vs_currency,
                "include_24hr_change": "true",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_last_updated_at": "true",
            }
        )

        if not data or coin_id not in data:
            return None

        coin_data = data[coin_id]
        result = {
            "coin_id": coin_id,
            "price": coin_data.get(vs_currency),
            "change_24h": coin_data.get(f"{vs_currency}_24h_change"),
            "market_cap": coin_data.get(f"{vs_currency}_market_cap"),
            "volume_24h": coin_data.get(f"{vs_currency}_24h_vol"),
            "last_updated": coin_data.get("last_updated_at"),
            "currency": vs_currency.upper(),
        }

        # Guardar en cache
        import json
        _finance_cache.set(cache_key, json.dumps(result))
        return result

    async def get_history(self, coin: str, days: int = 7, vs_currency: str = "usd") -> Optional[List]:
        """
        Obtiene histórico de precios.

        Returns:
            Lista de [timestamp, price] o None si falla
        """
        coin_id = self._resolve_coin_id(coin)
        cache_key = f"history:{coin_id}:{days}:{vs_currency}"

        cached = _finance_cache.get(cache_key)
        if cached:
            import json
            try:
                return json.loads(cached)
            except Exception:
                pass

        data = await self._get(
            f"/coins/{coin_id}/market_chart",
            params={"vs_currency": vs_currency, "days": days}
        )

        if not data or "prices" not in data:
            return None

        prices = data["prices"]  # [[timestamp_ms, price], ...]

        import json
        _finance_cache.set(cache_key, json.dumps(prices))
        return prices

    def format_price(self, price_data: dict) -> str:
        """Formatea datos de precio para mostrar al usuario."""
        if not price_data:
            return "No se pudo obtener el precio."

        coin_name = price_data["coin_id"].replace("-", " ").title()
        price = price_data.get("price", 0)
        change = price_data.get("change_24h", 0) or 0
        currency = price_data.get("currency", "USD")

        # Formatear precio
        if price >= 1000:
            price_str = f"{price:,.2f}"
        elif price >= 1:
            price_str = f"{price:.4f}"
        else:
            price_str = f"{price:.8f}"

        # Indicador de cambio
        change_icon = "🟢" if change >= 0 else "🔴"
        change_str = f"+{change:.2f}%" if change >= 0 else f"{change:.2f}%"

        market_cap = price_data.get("market_cap", 0) or 0
        vol = price_data.get("volume_24h", 0) or 0

        lines = [
            f"**{coin_name}** ({currency})",
            f"💰 Precio: {price_str} {currency}",
            f"{change_icon} Cambio 24h: {change_str}",
        ]

        if market_cap > 0:
            lines.append(f"📊 Market Cap: ${market_cap/1e9:.2f}B")
        if vol > 0:
            lines.append(f"📈 Volumen 24h: ${vol/1e9:.2f}B")

        return "\n".join(lines)

    def format_history_ascii(self, prices: List, coin_name: str) -> str:
        """Genera un mini gráfico ASCII del histórico."""
        if not prices or len(prices) < 2:
            return "Sin datos históricos disponibles."

        values = [p[1] for p in prices]
        min_val = min(values)
        max_val = max(values)
        price_range = max_val - min_val

        if price_range == 0:
            return f"Precio estable: {values[-1]:.2f}"

        # Gráfico ASCII de 8 puntos
        bars = "▁▂▃▄▅▆▇█"
        n_bars = min(20, len(values))
        step = len(values) // n_bars
        sample = values[::step][-n_bars:]

        chart = ""
        for v in sample:
            idx = int((v - min_val) / price_range * (len(bars) - 1))
            chart += bars[idx]

        change_total = ((values[-1] - values[0]) / values[0] * 100) if values[0] > 0 else 0
        icon = "📈" if change_total >= 0 else "📉"

        return (
            f"**{coin_name.title()}** últimos {len(prices) // 24}d:\n"
            f"{chart}\n"
            f"Min: {min_val:.2f} | Max: {max_val:.2f}\n"
            f"{icon} Cambio periodo: {change_total:+.1f}%"
        )


# Singleton global
_crypto_api = CryptoAPI()


async def get_crypto_price(coin: str, vs_currency: str = "usd") -> str:
    """
    Función de alto nivel para obtener precio de una cripto.

    Args:
        coin: Nombre o símbolo (ej: "bitcoin", "BTC", "ethereum")
        vs_currency: Moneda de referencia ("usd", "eur", "btc")

    Returns:
        String formateado con precio
    """
    data = await _crypto_api.get_price(coin, vs_currency)
    if data:
        return _crypto_api.format_price(data)
    return f"No encontré información de precio para '{coin}'. ¿Es el nombre correcto?"


async def get_crypto_history(coin: str, days: int = 7) -> str:
    """
    Función de alto nivel para obtener historial de precio.

    Args:
        coin: Nombre o símbolo
        days: Número de días

    Returns:
        Gráfico ASCII + datos
    """
    prices = await _crypto_api.get_history(coin, days)
    if prices:
        return _crypto_api.format_history_ascii(prices, coin)
    return f"No pude obtener el historial de '{coin}'."


async def get_top_cryptos(limit: int = 10, vs_currency: str = "usd") -> str:
    """Obtiene las top N criptomonedas por market cap."""
    data = await _crypto_api._get(
        "/coins/markets",
        params={
            "vs_currency": vs_currency,
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
    )

    if not data:
        return "No pude obtener el ranking de criptomonedas."

    lines = [f"**Top {limit} Criptomonedas por Market Cap**\n"]
    for i, coin in enumerate(data, 1):
        change = coin.get("price_change_percentage_24h", 0) or 0
        icon = "🟢" if change >= 0 else "🔴"
        price = coin.get("current_price", 0)
        lines.append(
            f"{i}. **{coin['name']}** ({coin['symbol'].upper()}) "
            f"- ${price:,.2f} {icon}{change:+.1f}%"
        )

    return "\n".join(lines)
