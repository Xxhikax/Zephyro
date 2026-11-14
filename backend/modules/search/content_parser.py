# modules/search/content_parser.py
"""
Extracción de contenido estructurado de HTML.

Funciones exportadas:
- extract_json_ld        → schema.org JSON-LD blocks
- extract_product_ld     → primer Product/Offer encontrado
- extract_tables         → tablas HTML como list[dict]
- tables_to_markdown     → formatea tablas a markdown
- extract_prices         → precios de texto plano
- extract_images         → URLs de imágenes (filtra iconos)
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Optional

# ── JSON-LD ──────────────────────────────────────────────────────────────────

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def extract_json_ld(html: str) -> list[dict]:
    """Extrae y parsea todos los bloques JSON-LD de schema.org."""
    results: list[dict] = []
    for match in _JSONLD_RE.finditer(html):
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict):
                results.append(data)
        except (json.JSONDecodeError, ValueError):
            pass
    return results


def extract_product_ld(html: str) -> Optional[dict]:
    """Retorna el primer bloque JSON-LD de tipo Product u Offer."""
    for obj in extract_json_ld(html):
        t = obj.get("@type", "")
        if t in ("Product", "ItemList", "Offer"):
            return obj
        if "offers" in obj or "price" in obj:
            return obj
    return None


# ── TABLES ───────────────────────────────────────────────────────────────────

def extract_tables(html: str) -> list[dict]:
    """
    Extrae tablas HTML como lista de dicts {"headers": [...], "rows": [[...]]}.
    Requiere beautifulsoup4; retorna [] si no está instalado.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html, "html.parser")
    tables: list[dict] = []

    for table in soup.find_all("table"):
        headers: list[str] = []
        rows: list[list[str]] = []

        thead = table.find("thead")
        if thead:
            headers = [th.get_text(strip=True) for th in thead.find_all(["th", "td"])]

        tbody = table.find("tbody") or table
        for tr in tbody.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells or all(c == "" for c in cells):
                continue
            if not headers:
                headers = cells
            else:
                rows.append(cells)

        if headers or rows:
            tables.append({"headers": headers, "rows": rows})

    return tables


def tables_to_markdown(tables: list[dict]) -> str:
    """Convierte lista de tablas a markdown estándar."""
    parts: list[str] = []
    for t in tables:
        headers = t.get("headers", [])
        rows = t.get("rows", [])
        if not headers and not rows:
            continue
        if headers:
            parts.append("| " + " | ".join(headers) + " |")
            parts.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in rows[:25]:
            parts.append("| " + " | ".join(str(c) for c in row) + " |")
        parts.append("")
    return "\n".join(parts)


# ── PRICES ───────────────────────────────────────────────────────────────────

_PRICE_RE = re.compile(
    r"""
    (?:
      (?P<usd>USD?\s*[\$]?\s*)      (?P<usd_v>\d[\d,]*(?:\.\d{1,2})?)   # USD 45.99 / US$ 45
    | (?P<pen>S/\s*)                 (?P<pen_v>\d[\d,]*(?:\.\d{1,2})?)   # S/ 250
    | (?P<mxn>MXN\s*\$?\s*)         (?P<mxn_v>\d[\d,]*(?:\.\d{1,2})?)   # MXN $3,500
    | (?P<ars>ARS\s*\$?\s*)         (?P<ars_v>\d[\d,]*(?:\.\d{1,2})?)   # ARS $28,999
    | (?P<cop>COP\s*\$?\s*)         (?P<cop_v>\d[\d,]*(?:\.\d{1,2})?)   # COP $45,000
    | (?P<clp>CLP\s*\$?\s*)         (?P<clp_v>\d[\d,]*(?:\.\d{1,2})?)   # CLP $25,000
    | (?P<brl>R\$\s*)               (?P<brl_v>\d[\d,]*(?:\.\d{1,2})?)   # R$150
    | (?P<eur>€\s*)                  (?P<eur_v>\d[\d,]*(?:\.\d{1,2})?)   # €45.99
    | (?P<gen>\$\s*)                 (?P<gen_v>\d[\d,]*(?:\.\d{1,2})?)   # $1,200
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

_CURRENCY_BY_GROUP = {
    "usd": "USD", "pen": "PEN", "mxn": "MXN", "ars": "ARS",
    "cop": "COP", "clp": "CLP", "brl": "BRL", "eur": "EUR", "gen": "USD",
}
_VALUE_GROUP = {k: f"{k}_v" for k in _CURRENCY_BY_GROUP}


def extract_prices(text: str) -> list[dict]:
    """
    Extrae precios del texto con su moneda inferida.
    Retorna lista de {amount, currency, raw, context}.
    """
    results: list[dict] = []
    for m in _PRICE_RE.finditer(text):
        gd = m.groupdict()
        for prefix, currency in _CURRENCY_BY_GROUP.items():
            if gd.get(prefix) is not None:
                raw_val = gd.get(_VALUE_GROUP[prefix]) or ""
                try:
                    # Normalize: remove thousand separators, keep decimal
                    normalized = raw_val.replace(",", "")
                    amount = float(normalized)
                    context = text[max(0, m.start() - 40): m.end() + 40].strip()
                    results.append({
                        "amount": amount,
                        "currency": currency,
                        "raw": m.group(0).strip(),
                        "context": context,
                    })
                except ValueError:
                    pass
                break
    return results


# ── IMAGES ───────────────────────────────────────────────────────────────────

def extract_images(html: str, base_url: str = "") -> list[dict]:
    """
    Extrae URLs de imágenes del HTML, filtrando data-URIs e iconos pequeños.
    Usa BeautifulSoup si está disponible; fallback a regex.
    Retorna lista de {src, alt}.
    """
    try:
        from bs4 import BeautifulSoup
        return _extract_images_bs4(html, base_url)
    except ImportError:
        return _extract_images_regex(html, base_url)


def _normalize_url(src: str, base_url: str) -> str:
    if base_url and src.startswith("/"):
        p = urllib.parse.urlparse(base_url)
        return f"{p.scheme}://{p.netloc}{src}"
    return src


def _extract_images_bs4(html: str, base_url: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    images: list[dict] = []
    seen: set[str] = set()

    for img in soup.find_all("img"):
        src = (
            img.get("src") or
            img.get("data-src") or
            img.get("data-lazy-src") or
            img.get("data-original") or
            ""
        )
        if not src or src.startswith("data:"):
            continue

        src = _normalize_url(src, base_url)
        if src in seen:
            continue
        seen.add(src)

        # Skip tiny icons
        try:
            w = int(img.get("width") or 0)
            h = int(img.get("height") or 0)
            if 0 < w < 60 or 0 < h < 60:
                continue
        except (ValueError, TypeError):
            pass

        images.append({"src": src, "alt": img.get("alt") or ""})

    return images[:20]


def _extract_images_regex(html: str, base_url: str) -> list[dict]:
    pattern = re.compile(
        r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    results: list[dict] = []
    seen: set[str] = set()
    for m in pattern.finditer(html):
        src = m.group(1)
        if src.startswith("data:") or src in seen:
            continue
        seen.add(src)
        results.append({"src": _normalize_url(src, base_url), "alt": ""})
    return results[:20]
