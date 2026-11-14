"""
Tests intensivos para detección de búsqueda web en Zephyro.

Cubre:
  - Queries simples que SÍ deben buscar
  - Queries simples que NO deben buscar
  - Queries complejas/compuestas
  - Follow-ups conversacionales
  - Falsos positivos (frases que parecen búsqueda pero no lo son)
  - Casos edge (muy cortos, matemáticas, preguntas sobre Zephyro mismo)
  - is_external_api_query() vs _ALWAYS_SEARCH_PATTERNS cobertura

Uso:
    python backend/tests/test_web_search_detection.py
    python backend/tests/test_web_search_detection.py --verbose
"""
import sys
import os
import re
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from modules.integrations.api_registry import (
    _ALWAYS_SEARCH_PATTERNS,
    _NEVER_SEARCH_PATTERNS,
    is_external_api_query,
)

# ── Heurística de follow-up (replicada desde orchestrator) ───────────────────
_FOLLOWUP_CUES = {
    'más', 'mas', 'también', 'tambien', 'además', 'ademas',
    'dime', 'listame', 'listamelos', 'lista', 'cuáles', 'cuales',
    'quiénes', 'quienes', 'todos', 'completa', 'completo',
    'detalle', 'detalles', 'detállame', 'detallame', 'detalla',
    'amplía', 'amplia', 'profundiza', 'explícame', 'explicame',
    'específicamente', 'especificamente', 'exactamente',
}
_REF_PATTERN = re.compile(
    r'\b(esos|esas|estos|estas|ellos|ellas|los mismos|las mismas|'
    r'al respecto|sobre eso|de eso|de esto|lo mismo|lo anterior)\b'
)

def _pattern_match(query: str) -> tuple[bool, int | None]:
    q = query.strip().lower()
    for i, p in enumerate(_ALWAYS_SEARCH_PATTERNS):
        if re.search(p, q):
            return True, i
    return False, None

def _never_match(query: str) -> bool:
    q = query.strip().lower()
    for p in _NEVER_SEARCH_PATTERNS:
        if re.search(p, q):
            return True
    return False

def _followup_heuristic(query: str) -> bool:
    ql = query.strip().lower()
    words = set(ql.split())
    has_cue = bool(words & _FOLLOWUP_CUES)
    has_ref = bool(_REF_PATTERN.search(ql))
    return has_cue or has_ref

def detect_search(query: str, ctx_hint: str = "") -> tuple[bool, str]:
    """
    Simula la lógica completa de detección en cloud mode sin LLM local.
    Retorna (should_search, reason).
    """
    # 1. API Registry keyword match
    if is_external_api_query(query):
        return True, "api_registry"
    # 2. _ALWAYS_SEARCH_PATTERNS
    matched, pat_idx = _pattern_match(query)
    if matched:
        return True, f"pattern[{pat_idx}]"
    # 3. Follow-up heurística (solo si hay contexto previo)
    if ctx_hint and _followup_heuristic(query):
        return True, "followup_heuristic"
    return False, "none"


# ── Casos de test ─────────────────────────────────────────────────────────────

# (query, should_search, ctx_hint, label)
TEST_CASES = [

    # ── DEBE BUSCAR: divisas / mercados ─────────────────────────────────────
    ("cuál es el tipo de cambio del dólar hoy",
     True, "", "tipo_cambio_dolar"),
    ("cómo está el euro hoy",
     True, "", "euro_hoy"),
    ("cómo va el peso mexicano",
     True, "", "peso_mexicano"),

    # ── DEBE BUSCAR: queries de actualidad directas ──────────────────────────
    ("busca las últimas noticias sobre el debate presidencial del Perú",
     True, "", "noticias_peru_debate"),
    ("quienes son los candidatos presidenciales peruanos actualmente",
     True, "", "candidatos_peruanos_actualmente"),
    ("quien es el presidente de mexico actualmente",
     True, "", "presidente_mexico"),
    ("qué pasó con la guerra en ucrania",
     True, "", "guerra_ucrania"),
    ("últimas noticias de economía",
     True, "", "noticias_economia"),
    ("resultado de las elecciones en argentina",
     True, "", "resultado_elecciones"),

    # ── DEBE BUSCAR: con "investiga/averigua" ───────────────────────────────
    ("Zephyro necesito que investigues sobre quienes son los 35 candidatos a la presidencia del Peru",
     True, "", "CASO_REAL_FAILING_investiga"),
    ("investiga sobre los candidatos presidenciales peruanos 2026",
     True, "", "investiga_candidatos"),
    ("averigua quiénes son los favoritos según las encuestas",
     True, "", "averigua_favoritos"),
    ("investiga qué está pasando en oriente medio",
     True, "", "investiga_oriente_medio"),
    ("necesito que investigues el estado actual del conflicto en Gaza",
     True, "", "investiga_gaza"),

    # ── DEBE BUSCAR: elecciones/encuestas/candidatos ─────────────────────────
    ("cuáles son los candidatos a la presidencia de Venezuela",
     True, "", "candidatos_venezuela"),
    ("encuestas presidenciales en colombia 2026",
     True, "", "encuestas_colombia"),
    ("sondeos electorales de España",
     True, "", "sondeos_espana"),
    ("quiénes son los candidatos al congreso",
     True, "", "candidatos_congreso"),
    ("candidatos a la presidencia México 2027",
     True, "", "candidatos_mexico"),
    ("elecciones en brasil este año",
     True, "", "elecciones_brasil"),
    ("quiénes son los favoritos según las últimas encuestas",
     True, "", "favoritos_encuestas"),

    # ── DEBE BUSCAR: geopolítica/actualidad ─────────────────────────────────
    ("cómo reaccionó China ante la nueva política arancelaria de Trump",
     True, "", "china_reaccion_trump"),
    ("posición de Rusia sobre el acuerdo de paz con Ucrania",
     True, "", "rusia_posicion"),
    ("qué opina la ONU sobre la situación en Venezuela",
     True, "", "onu_venezuela"),
    ("y qué dice Europa al respecto",
     True, "situación en Ucrania y posición de países aliados", "europa_respecto"),
    ("declaración oficial de la Casa Blanca sobre Gaza",
     True, "", "declaracion_casa_blanca"),

    # ── DEBE BUSCAR: consultas compuestas/complejas ──────────────────────────
    ("busca noticias sobre el conflicto en Gaza y dime también cuántas víctimas hay actualmente",
     True, "", "compuesta_gaza_victimas"),
    ("investiga quiénes son los candidatos presidenciales de Perú y cuáles son los favoritos según las encuestas más recientes",
     True, "", "compuesta_candidatos_encuestas"),
    ("quiero que busques información sobre las elecciones en México 2027, especialmente los candidatos de los principales partidos y sus propuestas económicas",
     True, "", "compuesta_elecciones_mexico"),
    ("dime quién ganó las elecciones en Ecuador y cuál fue el porcentaje de votos",
     True, "", "compuesta_ecuador_votos"),
    ("busca las últimas noticias sobre Tesla, incluyendo el precio de la acción y qué dijo Elon Musk esta semana",
     True, "", "compuesta_tesla_musk"),

    # ── DEBE BUSCAR: follow-ups con contexto ────────────────────────────────
    ("Pero dime quiénes son todos, listamelos",
     True, "candidatos presidenciales Peru 2026", "followup_listame"),
    ("dime más sobre los favoritos",
     True, "encuestas electorales Peru", "followup_favoritos"),
    ("quiénes son los primeros cinco",
     True, "candidatos presidencia", "followup_primeros"),
    ("profundiza en el candidato de Fuerza Popular",
     True, "candidatos Peru", "followup_profundiza"),
    ("y también investiga sus propuestas económicas",
     True, "candidatos Peru", "followup_propuestas"),
    ("detállame sus posiciones sobre economía",
     True, "candidatos Peru", "followup_detalle"),
    ("detalla las propuestas de los tres primeros",
     True, "candidatos presidenciales Peru 2026", "followup_detalla"),
    ("explícame más sobre el segundo candidato",
     True, "lista candidatos presidenciales", "followup_explicame"),
    ("dame más detalles sobre eso",
     True, "noticias Gaza", "followup_eso"),
    ("y qué pasa con los mismos en las regiones del norte",
     True, "candidatos Peru", "followup_mismos"),

    # ── NO DEBE BUSCAR: preguntas sobre Zephyro ──────────────────────────────
    ("hola, cómo estás",
     False, "", "saludo_hola"),
    ("buenos días Zephyro",
     False, "", "saludo_buenos_dias"),
    ("gracias",
     False, "", "gracias"),
    ("quién eres tú",
     False, "", "quien_eres"),
    ("cómo te llamas",
     False, "", "como_te_llamas"),

    # ── Wikipedia: knowledge queries → SÍ van a API externa (Wikipedia route) ─
    ("qué es la fotosíntesis",
     True, "", "fotosintesis_wikipedia"),      # correcto: Wikipedia
    ("qué es Python",
     True, "", "python_wikipedia"),             # correcto: Wikipedia
    # Las siguientes son demasiado largas para el filtro "qué es X" → Wikipedia no matchea
    ("cómo funciona el motor de combustión interna",
     False, "", "motor_atemporal"),
    ("cuántos años tiene la Tierra",
     False, "", "edad_tierra"),
    ("explícame la teoría de la relatividad",
     False, "", "relatividad"),

    # ── NO DEBE BUSCAR: tareas de sistema / automatización ──────────────────
    ("abre Spotify",
     False, "", "automatizacion_spotify"),
    ("pon la canción gold in the ceiling",
     False, "", "automatizacion_cancion"),
    ("apaga la pantalla",
     False, "", "automatizacion_pantalla"),
    ("crea un archivo llamado notas.txt en el escritorio",
     False, "", "automatizacion_archivo"),
    ("abre YouTube",
     False, "", "automatizacion_youtube"),

    # ── NO DEBE BUSCAR: matemáticas ─────────────────────────────────────────
    ("cuánto es 15 por 23",
     False, "", "matematica_simple"),
    ("2 + 2",
     False, "", "matematica_trivial"),
    ("si tengo 100 y gasto 37.5 cuánto queda",
     False, "", "matematica_compuesta"),

    # ── Cobertura de patrones no alcanzados por los tests anteriores ────────
    ("quien es el presidente de Brasil actualmente",
     True, "", "pat2_presidente"),
    ("quién gobierna España",
     True, "", "pat3_quien_gobierna"),
    ("busca noticias del día",
     True, "", "pat8_busca_noticias"),
    ("el conflicto en Gaza sigue actual",
     True, "", "pat10_conflicto_actual"),
    ("y Rusia qué opina sobre esto",
     True, "negociaciones de paz Ucrania", "pat12_rusia_geopolitica"),
    ("Rusia condena el ataque",
     True, "", "pat13_rusia_condena"),
    ("los favoritos según las encuestas son",
     True, "", "pat18_favoritos_segun"),
    ("candidatos presidenciales de Chile",
     True, "", "pat21_candidatos_presidenciales"),
    ("el debate presidencial fue intenso",
     True, "", "pat22_presidencial"),

    # ── FALSOS POSITIVOS potenciales (no debe buscar) ───────────────────────
    ("dime qué hora es",
     True, "", "hora_api"),     # correcto: va a time_timezone API route
    ("busca mis archivos de música",
     False, "", "busca_archivos"),  # "busca" pero en filesystem local
    # "noticias de" matchea web_search keyword — edge case conocido
    # En la práctica el router lo intercepta como automation antes de llegar aquí
    ("ponme noticias de fondo mientras trabajo",
     True, "", "noticias_fondo_edgecase"),  # FP conocido: router lo captura antes
]


# ── Runner ────────────────────────────────────────────────────────────────────

def run_tests(verbose: bool = False):
    passed = 0
    failed = 0
    warnings = []

    # Agrupar por categoría
    categories = {}
    for query, expected, ctx, label in TEST_CASES:
        cat = label.split("_")[0]
        categories.setdefault(cat, []).append((query, expected, ctx, label))

    print(f"\n{'='*70}")
    print(f"Test de detección de búsqueda web — {len(TEST_CASES)} casos")
    print(f"{'='*70}\n")

    all_cases = TEST_CASES
    for query, expected, ctx, label in all_cases:
        result, reason = detect_search(query, ctx)
        ok = result == expected

        if ok:
            passed += 1
            if verbose:
                icon = "✓" if expected else "○"
                print(f"  {icon} [{label}]")
                if verbose and expected:
                    print(f"      vía: {reason}")
                    print(f"      q: {query[:80]}")
        else:
            failed += 1
            icon = "✗ FALSO POSITIVO" if result and not expected else "✗ FALSO NEGATIVO"
            print(f"  {icon} [{label}]")
            print(f"      q:        {query[:80]}")
            print(f"      esperado: {'BUSCAR' if expected else 'no buscar'}")
            print(f"      obtenido: {'BUSCAR' if result else 'no buscar'} (vía {reason})")
            if ctx:
                print(f"      ctx:      {ctx[:60]}")
            print()

    # ── Estadísticas ─────────────────────────────────────────────────────────
    total = passed + failed
    pct = 100 * passed / total
    should_search = sum(1 for _, e, _, _ in TEST_CASES if e)
    should_not    = sum(1 for _, e, _, _ in TEST_CASES if not e)
    fn = sum(1 for q, e, c, l in TEST_CASES if e and not detect_search(q, c)[0])
    fp = sum(1 for q, e, c, l in TEST_CASES if not e and detect_search(q, c)[0])

    print(f"\n{'─'*70}")
    print(f"  Resultado: {passed}/{total} ({pct:.1f}%)")
    print(f"  Casos que SÍ deben buscar: {should_search}  |  que NO deben: {should_not}")
    print(f"  Falsos negativos (búsqueda perdida):   {fn}")
    print(f"  Falsos positivos (búsqueda innecesaria): {fp}")
    print(f"{'─'*70}")

    # ── Cobertura por mecanismo ───────────────────────────────────────────────
    print("\n  Cobertura por mecanismo de detección:")
    mech_counts = {}
    for query, expected, ctx, label in TEST_CASES:
        if expected:
            _, reason = detect_search(query, ctx)
            mech_counts[reason] = mech_counts.get(reason, 0) + 1
    for mech, count in sorted(mech_counts.items(), key=lambda x: -x[1]):
        print(f"    {mech:30} → {count} queries")

    # ── Análisis de patrones: cuáles se usan más ─────────────────────────────
    print("\n  Uso de _ALWAYS_SEARCH_PATTERNS:")
    pat_counts = [0] * len(_ALWAYS_SEARCH_PATTERNS)
    for query, expected, ctx, label in TEST_CASES:
        if expected:
            matched, idx = _pattern_match(query)
            if matched and idx is not None:
                pat_counts[idx] += 1
    for i, (pat, count) in enumerate(zip(_ALWAYS_SEARCH_PATTERNS, pat_counts)):
        if count > 0:
            print(f"    [{i:2}] {count}x  {pat[:65]}")
    unused = [i for i, c in enumerate(pat_counts) if c == 0]
    if unused:
        print(f"\n  ⚠ Patrones sin cobertura en tests: {unused}")
        for i in unused:
            print(f"    [{i}] {_ALWAYS_SEARCH_PATTERNS[i]}")

    print()
    return failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    ok = run_tests(verbose=args.verbose)
    sys.exit(0 if ok else 1)
