"""
Test rápido del pipeline de búsqueda web: Tavily + DDG fallback.
Ejecutar desde el directorio backend/:
    python tests/test_tavily_search.py
"""
import sys, asyncio, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'), override=True)

from modules.search.web_search import WebSearch
from modules.integrations.api_registry import _clean_search_query, _ALWAYS_SEARCH_PATTERNS
import re

SEP = "─" * 60

def check(label, ok, detail=""):
    icon = "✓" if ok else "✗"
    print(f"  [{icon}] {label}" + (f": {detail}" if detail else ""))

async def main():
    ws = WebSearch()

    print(f"\n{SEP}")
    print("  STATUS DE MOTORES")
    print(SEP)
    key = os.getenv("TAVILY_API_KEY", "")
    check("TAVILY_API_KEY cargada", bool(key), key[:14] + "..." if key else "NO configurada")
    check("Tavily disponible", ws._check_tavily())
    check("DuckDuckGo disponible", ws._check_ddg())

    print(f"\n{SEP}")
    print("  TEST 1 — QUERY CLEANING")
    print(SEP)
    cases = [
        ("quien es el presidente de Peru actualmente",
         "presidente de peru 2026"),
        ("quien es el presidente del Peru actualmente",
         "presidente de peru 2026"),
        ("Zephyro dime quien es el presidente del Peru actualmente",
         "presidente de peru 2026"),
        ("oye Zephyro, quien es el presidente de Mexico actualmente",
         "presidente de mexico 2026"),
        ("dime quien es la presidenta de Peru ahora",
         "presidenta de peru 2026"),
        ("Buscalo por internet y dime quien es el presidente de Peru",
         "presidente de peru 2026"),
        ("busca en google quien es el presidente de Chile",
         "presidente de chile 2026"),
        ("que es Python",
         "que es Python"),  # NO debería modificarse
    ]
    for query, expected in cases:
        result = _clean_search_query(query)
        ok = expected.lower() in result.lower()
        check(f'"{query[:45]}..."', ok, f"→ {result}")

    print(f"\n{SEP}")
    print("  TEST 2 — LAYER 0 BYPASS TEMPORAL")
    print(SEP)
    temporal_cases = [
        ("quien es el presidente de Peru actualmente", True),
        ("noticias de hoy sobre IA", True),
        ("quien gobierna Argentina", True),
        ("que es Python", False),
        ("hola buenos dias", False),
    ]
    for query, expected in temporal_cases:
        hit = any(re.search(p, query.lower()) for p in _ALWAYS_SEARCH_PATTERNS)
        check(f'"{query}"', hit == expected, f"bypass={hit} (esperado={expected})")

    print(f"\n{SEP}")
    print("  TEST 3 — BÚSQUEDA REAL (presidente Perú)")
    print(SEP)
    query = "presidente de peru 2026"
    results = await ws.search(query, max_results=3)
    motor = "Tavily" if ws._tavily_available else "DuckDuckGo"
    check(f"Resultados via {motor}", len(results) > 0, f"{len(results)} encontrados")
    for r in results:
        print(f"       • {r.title[:70]}")

    print(f"\n{SEP}")
    print("  TEST 4 — BÚSQUEDA REAL (noticias IA)")
    print(SEP)
    query2 = "ultimas noticias inteligencia artificial 2026"
    results2 = await ws.search(query2, max_results=3)
    check(f"Resultados via {motor}", len(results2) > 0, f"{len(results2)} encontrados")
    for r in results2:
        print(f"       • {r.title[:70]}")

    print(f"\n{SEP}\n")

asyncio.run(main())
