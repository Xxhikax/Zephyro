"""
Benchmark de rendimiento para componentes críticos de Zephyro.

Mide tiempos de:
- prepare_for_tts() sobre respuestas reales de distintos tamaños
- sanitize() vs prepare_for_tts() pipeline
- search_relevant_context() síncrono vs asyncio.to_thread() wrapper

Uso:
    python backend/tests/test_performance_benchmark.py

No requiere backend corriendo; usa mocks donde sea necesario.
"""
import asyncio
import sys
import os
import time
import statistics
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.response_sanitizer import prepare_for_tts, sanitize

# ── Fixtures de texto ─────────────────────────────────────────────────────────

SHORT_RESPONSE = "El clima hoy será soleado con 24 grados en la Ciudad de México."

MEDIUM_RESPONSE = """
# Noticias del día

Según Reuters, los mercados financieros cerraron al alza hoy jueves.
El índice S&P 500 subió un 1.2% mientras que el Nasdaq ganó un 0.8%.

De acuerdo con Bloomberg, los inversores reaccionaron positivamente a los datos de inflación.
Las cifras mostraron una desaceleración del 3.2% al 2.9% anual.

## Puntos clave:
• Los mercados europeos también subieron
• El euro se fortaleció frente al dólar
• El precio del petróleo bajó un 0.5%

Fuentes:
• Reuters
• Bloomberg
• El Financiero
""".strip()

LONG_RESPONSE = (MEDIUM_RESPONSE + "\n\n") * 5 + "Fin del resumen."

ENGLISH_HEAVY = """
Te voy a poner la canción.
gold in the ceiling by the black keys
Espero que la disfrutes.
another one bites the dust by queen
También tengo esta en la lista.
""".strip()

RESPONSES = {
    "short": SHORT_RESPONSE,
    "medium": MEDIUM_RESPONSE,
    "long": LONG_RESPONSE,
    "english_heavy": ENGLISH_HEAVY,
}

ITERATIONS = 500


# ── Benchmark helpers ─────────────────────────────────────────────────────────

def bench_sync(fn, text, n=ITERATIONS):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn(text)
        times.append((time.perf_counter() - t0) * 1000)
    return times


async def bench_async(async_fn, n=ITERATIONS):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        await async_fn()
        times.append((time.perf_counter() - t0) * 1000)
    return times


def print_stats(label, times_ms):
    mean = statistics.mean(times_ms)
    median = statistics.median(times_ms)
    p95 = sorted(times_ms)[int(len(times_ms) * 0.95)]
    print(f"  {label:<45} mean={mean:.3f}ms  median={median:.3f}ms  p95={p95:.3f}ms")


# ── Benchmarks de TTS filters ────────────────────────────────────────────────

def run_tts_filter_benchmarks():
    print("\n=== TTS Filter Benchmarks ===")
    for name, text in RESPONSES.items():
        chars = len(text)
        times = bench_sync(prepare_for_tts, text)
        print_stats(f"prepare_for_tts [{name}, {chars} chars]", times)

    print()
    for name, text in RESPONSES.items():
        times_san = bench_sync(sanitize, text)
        times_tts = bench_sync(prepare_for_tts, text)
        overhead_ms = statistics.mean(times_tts) - statistics.mean(times_san)
        print(f"  prepare_for_tts overhead vs sanitize [{name}]: +{overhead_ms:.3f}ms")


# ── Benchmark de vector search (mock) ────────────────────────────────────────

def _mock_embed_query(text):
    """Simula la latencia de embed_query (~150ms)."""
    time.sleep(0.15)
    return [0.1] * 768


def run_vector_search_benchmarks():
    print("\n=== Vector Search Benchmarks (mock embed ~150ms) ===")

    # Mock del VectorStore con embed_query simulado
    mock_store = MagicMock()
    mock_store.vectors = [[0.1] * 768]
    mock_store.documents = ["Texto de prueba de memoria vectorial."]
    mock_store.metadatas = [{"role": "user"}]

    # Simular el tiempo de search_relevant_context síncrono
    def fake_search_sync(query, k=1):
        time.sleep(0.15)  # simula embed_query blocking
        return "- [user]: Texto de prueba de memoria vectorial."

    async def fake_search_async(query, k=1):
        return await asyncio.to_thread(fake_search_sync, query, k)

    # Benchmark síncrono (3 llamadas para simular 3 requests)
    n_calls = 10
    sync_times = []
    for _ in range(n_calls):
        t0 = time.perf_counter()
        fake_search_sync("query de prueba")
        sync_times.append((time.perf_counter() - t0) * 1000)

    # Benchmark async (3 llamadas concurrentes simuladas)
    async def run_async_bench():
        async_times = []
        for _ in range(n_calls):
            t0 = time.perf_counter()
            await fake_search_async("query de prueba")
            async_times.append((time.perf_counter() - t0) * 1000)
        return async_times

    async_times = asyncio.run(run_async_bench())

    print_stats("search_relevant_context [sync, blocking]", sync_times)
    print_stats("async_search_relevant_context [asyncio.to_thread]", async_times)

    # Mostrar ganancia de concurrencia
    print(f"\n  Nota: el beneficio real es que asyncio.to_thread() libera el event loop")
    print(f"  durante los ~150ms de embed_query, permitiendo procesar otros requests.")


# ── Pipeline completo ────────────────────────────────────────────────────────

def run_pipeline_benchmark():
    print("\n=== Pipeline TTS completo (sanitize + prepare_for_tts) ===")
    text = MEDIUM_RESPONSE
    n = ITERATIONS

    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        cleaned = sanitize(text)
        prepared = prepare_for_tts(cleaned)
        times.append((time.perf_counter() - t0) * 1000)

    print_stats("sanitize() + prepare_for_tts() [medium]", times)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Benchmark Zephyro — {ITERATIONS} iteraciones por test (excepto vector search)")
    print("=" * 70)

    run_tts_filter_benchmarks()
    run_pipeline_benchmark()
    run_vector_search_benchmarks()

    print("\nBenchmark completo.")
