# modules/search/tools.py
"""
Definición de tools de Anthropic para Zephyro.
Tools disponibles: search (web general), product_search (e-commerce).
"""
from __future__ import annotations

import re
import time
from typing import Optional

SEARCH_TOOL = {
    "name": "search",
    "description": (
        "Busca información actualizada en internet usando Tavily. "
        "Úsala ÚNICAMENTE cuando el usuario pregunte por datos que cambian con el tiempo: "
        "noticias, precios actuales, resultados de elecciones, candidatos en cargo, "
        "eventos recientes, tipo de cambio, clima, o cualquier dato que cambia con el tiempo. "
        "NO la uses para: ciencia, biología, historia, matemáticas, conceptos, definiciones, "
        "procesos naturales (fotosíntesis, respiración, etc.), explicaciones generales, "
        "ni conocimiento estable que no depende de la fecha actual."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "La consulta de búsqueda en español o inglés"
            },
            "max_results": {
                "type": "integer",
                "description": "Número máximo de resultados (1-10). Por defecto 5.",
                "default": 5
            }
        },
        "required": ["query"]
    }
}


async def execute_search(
    query: str,
    max_results: int = 5,
    searcher=None
) -> str:
    """
    Ejecuta la búsqueda web y formatea los resultados para que Claude los procese.

    Para queries que piden listas/enumeraciones (candidatos, rankings, etc.) activa
    automáticamente: max_results=10, deep=True, y extracción de artículo completo
    para los top-2 resultados.

    Args:
        query: Consulta de búsqueda (se reescribe si es imperativa/vaga)
        max_results: Máximo de resultados (puede subir a 10 si se detecta list-intent)
        searcher: Instancia de WebSearch (se crea una si no se pasa)

    Returns:
        String con los resultados formateados para que Claude los procese
    """
    if searcher is None:
        from modules.search.web_search import WebSearch
        searcher = WebSearch()

    # ── Reescritura de query ──────────────────────────────────────────
    effective_query = _rewrite_query(query)

    # ── Detección de list-intent ──────────────────────────────────────
    is_list_query = _is_list_query(effective_query)
    if is_list_query:
        max_results = max(max_results, 10)
        deep = True
    else:
        deep = False

    results = await searcher.search(effective_query, max_results=max_results, deep=deep)

    if not results:
        # Retry con query original si la reescritura no dio resultados
        if effective_query != query:
            results = await searcher.search(query, max_results=max_results, deep=deep)
        if not results:
            return f"No se encontraron resultados para: '{effective_query}'"

    parts = [f"Resultados de búsqueda para: \"{effective_query}\"\n"]

    # Para list-queries: extraer artículo completo de los top-2 resultados
    full_text_budget = 2
    for i, r in enumerate(results, 1):
        parts.append(f"{i}. {r.title}")
        parts.append(f"   URL: {r.url}")
        if r.snippet:
            parts.append(f"   {r.snippet}")

        if is_list_query and full_text_budget > 0 and r.url:
            try:
                full_text = await searcher.fetch_article(r.url)
                if full_text and len(full_text) > 200:
                    excerpt = full_text[:2500].strip()
                    if len(full_text) > 2500:
                        excerpt += "..."
                    parts.append(f"   Contenido completo: {excerpt}")
                    full_text_budget -= 1
            except Exception:
                # fetch_article falló — continuar sin texto completo
                if r.full_text:
                    parts.append(f"   Contenido: {r.full_text[:600].strip()}")
        elif r.full_text:
            excerpt = r.full_text[:600].strip()
            if len(r.full_text) > 600:
                excerpt += "..."
            parts.append(f"   Contenido: {excerpt}")

        parts.append("")

    return "\n".join(parts)


# ── Helpers ───────────────────────────────────────────────────────────

_LIST_INTENT_PATTERNS = [
    r'\b(lista[r]?|todos|todas|listame|lista\s*me|enumera[r]?|dame\s+los|dame\s+las|'
    r'cu[aá]les\s+son|cu[aá]ntos?\s+(son|hay|tiene)|'
    r'todas?\s+(las?|los?)\s+\w+|todos?\s+(los?|las?)\s+\w+)\b',
    r'\b(candidatos?|candidatas?)\b.{0,60}\b(presidenc|elecci|inscrit|registrad|lista)',
    r'\b(inscrit[oa]s?|registrad[oa]s?|postulant[eo]s?)\b',
    r'\b(ranking|clasificaci[oó]n|top\s+\d+|\d+\s+(mejores?|primeros?|cand|part))\b',
]
_LIST_INTENT_RE = re.compile("|".join(_LIST_INTENT_PATTERNS), re.IGNORECASE)

def _is_list_query(query: str) -> bool:
    """Detecta si la query pide una lista o enumeración."""
    return bool(_LIST_INTENT_RE.search(query))


_IMPERATIVE_PREFIXES = re.compile(
    r'^(?:dame\s+(?:la\s+lista\s+de\s+(?:los\s+)?|los\s+|las\s+|el\s+|la\s+)?|'
    r'listame\s+(?:los\s+|las\s+)?|'
    r'enumerame\s+(?:los\s+|las\s+)?|'
    r'cu[aá]les\s+son\s+(?:los\s+|las\s+)?|'
    r'dime\s+(?:cu[aá]les\s+son\s+(?:los\s+|las\s+)?|los\s+|las\s+)?)',
    re.IGNORECASE,
)

_YEAR = str(time.gmtime().tm_year)

_ELECTION_CANDIDATES_RE = re.compile(
    r'\bcandidatos?\s+presidenciales?\b.{0,40}\b(per[uú]|m[eé]xico|colombia|chile|argentina|venezuela|bolivia|ecuador|brasil|uruguay|paraguay)\b'
    r'|\b(per[uú]|m[eé]xico|colombia|chile|argentina|venezuela|bolivia|ecuador|brasil|uruguay|paraguay)\b.{0,40}\bcandidatos?\s+presidenciales?\b',
    re.IGNORECASE,
)

def _rewrite_query(query: str) -> str:
    """
    Reescribe queries imperativas/vagas para mejorar los resultados de búsqueda.

    Reglas:
    1. Queries de candidatos presidenciales → añadir organismo electoral + año.
    2. Queries con prefijo imperativo → eliminar prefijo + añadir año si hay indicador electoral.
    3. El resto → sin cambios.
    """
    q = query.strip()
    q_stripped = _IMPERATIVE_PREFIXES.sub("", q).strip()

    # Regla 1: candidatos presidenciales de un país → query canónica
    if _ELECTION_CANDIDATES_RE.search(q):
        # Extraer el país mencionado
        country_match = re.search(
            r'\b(per[uú]|m[eé]xico|colombia|chile|argentina|venezuela|'
            r'bolivia|ecuador|brasil|uruguay|paraguay)\b',
            q, re.IGNORECASE,
        )
        country = country_match.group(0).capitalize() if country_match else ""
        if country:
            return f"candidatos presidenciales {country} {_YEAR} lista completa JNE elecciones"
        return q_stripped or q

    # Regla 2: prefijo imperativo eliminado + query tiene contenido útil
    if q_stripped and q_stripped != q and len(q_stripped) >= 10:
        # Añadir año si la query toca elecciones o candidatos pero no tiene año
        if re.search(r'\b(elecci[oó]n|candidato|presidenc)\b', q_stripped, re.IGNORECASE):
            if _YEAR not in q_stripped:
                q_stripped = q_stripped.rstrip("?., ") + f" {_YEAR}"
        return q_stripped

    return q


# ── Product Search Tool ───────────────────────────────────────────────────────

PRODUCT_SEARCH_TOOL = {
    "name": "product_search",
    "description": (
        "Busca y compara precios de productos en tiendas online: "
        "MercadoLibre (multi-región), eBay y AliExpress. "
        "Úsala cuando el usuario quiera comparar precios, buscar un producto específico, "
        "saber cuánto cuesta algo en tiendas online, o comprar algo."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Nombre o descripción del producto a buscar",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string", "enum": ["mercadolibre", "ebay", "aliexpress"]},
                "description": "Tiendas donde buscar. Por defecto todas.",
            },
            "max_results": {
                "type": "integer",
                "description": "Resultados por tienda (1-10). Por defecto 5.",
                "default": 5,
            },
            "ml_site": {
                "type": "string",
                "description": (
                    "Site de MercadoLibre: MLA (Argentina), MLM (México), MPE (Perú), "
                    "MLC (Chile), MCO (Colombia). Por defecto MLA o el detectado del contexto."
                ),
            },
        },
        "required": ["query"],
    },
}


async def execute_product_search(
    query: str,
    sources: list[str] | None = None,
    max_results: int = 5,
    ml_site: str | None = None,
    searcher=None,
) -> str:
    """
    Ejecuta la búsqueda de productos en e-commerce y formatea los resultados.

    Args:
        query:       Producto a buscar
        sources:     Lista de fuentes; None = todas
        max_results: Resultados por fuente
        ml_site:     Site de MercadoLibre (MLA/MLM/MPE/MLC/MCO)
        searcher:    Instancia de EcommerceSearch (se crea una si no se pasa)
    """
    if searcher is None:
        from modules.search.ecommerce import EcommerceSearch
        searcher = EcommerceSearch()

    results = await searcher.search(
        query=query,
        sources=sources,
        max_per_source=min(max_results, 10),
        ml_site=ml_site,
    )
    return searcher.format_results(results, query)
