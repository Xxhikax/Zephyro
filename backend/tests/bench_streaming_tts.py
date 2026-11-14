#!/usr/bin/env python3
"""
bench_streaming_tts.py — Fase 0 de P4 (streaming TTS).

Mide la ESTRUCTURA DE LATENCIA por tipo de pedido para cuantificar la ganancia
del streaming TTS ANTES de implementarlo, y para calibrar umbrales.

Para cada escenario LLM (streameable) mide, iterando `generate_stream`:
  - TTFT      : tiempo al primer token
  - 1ª oración: tiempo hasta cerrar la primera oración (cuándo podría arrancar el TTS)
  - gen total : tiempo de generación completa
Y con `synthesize_tts` mide la síntesis de la 1ª oración vs el texto completo.

Derivadas clave:
  - baseline_TTFA   = gen_total + tts_full         (lo que el usuario espera HOY)
  - streaming_TTFA  = 1ª_oracion + tts_1ª_oracion  (cuándo arrancaría el audio con streaming)
  - ahorro          = baseline_TTFA - streaming_TTFA

Los paths DETERMINISTAS (agenda/automatización) NO se ejecutan (efectos
secundarios): se mide sólo `synthesize_tts` de un string representativo para
confirmar gen≈0 y que el streaming no aplica.

Script MANUAL — NO lo colecta pytest. Hits LLM + TTS reales (cloud cuesta tokens).
Uso:
    ZEPHYRO_BENCH=1 backend/venv/Scripts/python.exe backend/tests/bench_streaming_tts.py
Flags:
    --cloud            medir también el tier cloud (cuesta tokens; default off)
    --no-llm           saltar mediciones LLM
    --no-tts           saltar mediciones TTS
    --tts-engine E     'local' (Piper, default) | 'fishaudio'
    --min-words N      umbral de palabras para cerrar chunk sin puntuación (default 5)
    --repeats N        repeticiones por escenario (default 1; promedia)
"""
__test__ = False  # no lo colecta pytest

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# La consola de Windows (cp1252) no imprime '→','≈'. Forzar UTF-8 best-effort.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

if os.environ.get("ZEPHYRO_BENCH") != "1":
    print("Guard del benchmark: exportá ZEPHYRO_BENCH=1 para correrlo "
          "(usa LLM y TTS reales; cloud cuesta tokens).")
    sys.exit(0)

from modules.voice.tts_chunking import (  # noqa: E402
    SENTENCE_ENDS, split_tts_sentences, count_sentences,
)

_OUT_DIR = Path(__file__).resolve().parent
_REPORT_TXT = _OUT_DIR / "_streaming_baseline_report.txt"
_HISTORY_JSONL = _OUT_DIR / "_streaming_baseline_history.jsonl"

SYSTEM_PREAMBLE = (
    "Eres Zephyro, el asistente personal de IA de Bruno. Respondé en español, "
    "de forma concisa y directa, sin emojis ni signos de exclamación entusiastas. "
    "Tenés capacidades de automatización, búsqueda web, agenda, voz y memoria. "
    "No inventes información; si no sabés algo, decilo.\n\n"
)

# Escenarios LLM (streameables): (label, user_input)
LLM_SCENARIOS = [
    ("chat_corto",   "¿Cómo estás hoy?"),
    ("chat_largo",   "Explicame en detalle cómo funciona la fotosíntesis, incluyendo "
                     "la fase luminosa y el ciclo de Calvin, con ejemplos."),
    ("investigacion","Resumí los puntos clave sobre el estado actual de la energía de "
                     "fusión nuclear y sus principales desafíos técnicos."),
    ("instruccion",  "Dame paso a paso cómo configurar un entorno virtual de Python "
                     "en Windows y activar dependencias."),
]

# Paths deterministas (NO se ejecutan): string representativo → sólo TTS.
DETERMINISTIC_SAMPLES = [
    ("agenda",         "Evento 12 agendado: Dentista, el lunes de 9 a 10."),
    ("automatizacion", "He abierto Spotify."),
    ("playback",       "Música pausada."),
    ("saludo_cache",   "Hola Bruno, en qué puedo ayudarte."),
]

# Muestras de TTS puro por longitud (para curva de síntesis vs largo del texto).
TTS_TEXT_SAMPLES = [
    ("tts_corto",  "Listo, Bruno."),
    ("tts_medio",  "Ya te lo dejé agendado. Tenés el turno el lunes a las nueve. "
                   "Si querés te lo recuerdo media hora antes."),
    ("tts_largo",  "La fotosíntesis convierte luz en energía química. Ocurre en dos "
                   "fases. La fase luminosa captura fotones en los tilacoides y produce "
                   "ATP y NADPH. Luego el ciclo de Calvin fija dióxido de carbono y "
                   "sintetiza glucosa. Sin este proceso no existiría la vida tal como "
                   "la conocemos."),
]


def _first_sentence_metrics(llm, prompt, force_local, force_cloud, min_words):
    """Itera generate_stream midiendo TTFT, tiempo a 1ª oración y gen total."""
    t0 = time.perf_counter()
    ttft_ms = None
    first_sentence_ms = None
    first_done = False
    buf = ""
    full = ""
    err = None
    try:
        for tok in llm.generate_stream(
            prompt, force_local=force_local, force_cloud=force_cloud,
        ):
            now = time.perf_counter()
            if not tok:
                continue
            if ttft_ms is None:
                ttft_ms = (now - t0) * 1000.0
            full += tok
            if not first_done:
                buf += tok
                if any(c in tok for c in SENTENCE_ENDS) or len(buf.split()) >= min_words:
                    first_sentence_ms = (now - t0) * 1000.0
                    first_done = True
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    gen_total_ms = (time.perf_counter() - t0) * 1000.0
    if first_sentence_ms is None:
        first_sentence_ms = gen_total_ms
    sentences = split_tts_sentences(full)
    return {
        "ttft_ms": ttft_ms,
        "first_sentence_ms": first_sentence_ms,
        "gen_total_ms": gen_total_ms,
        "chars": len(full),
        "sentences": len(sentences),
        "first_sentence_text": sentences[0] if sentences else "",
        "provider": getattr(llm, "last_provider_used", None),
        "model": getattr(llm, "last_model_used", None),
        "error": err,
    }


async def _measure_tts(text, engine):
    from routers.voice import synthesize_tts
    t0 = time.perf_counter()
    try:
        res = await synthesize_tts(text, engine=engine)
        ms = (time.perf_counter() - t0) * 1000.0
        return ms, bool(res.get("success")), res.get("error")
    except Exception as e:
        return (time.perf_counter() - t0) * 1000.0, False, f"{type(e).__name__}: {e}"


def _avg(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cloud", action="store_true", help="medir tier cloud (cuesta tokens)")
    ap.add_argument("--provider", default="auto",
                    choices=["auto", "anthropic", "gemini", "deepseek", "openai"],
                    help="proveedor cloud (default auto)")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-tts", action="store_true")
    ap.add_argument("--tts-engine", default="local", choices=["local", "fishaudio"])
    ap.add_argument("--min-words", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    results = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "llm": [],
        "deterministic": [],
        "tts_curve": [],
        "env": {},
    }

    llm = None
    if not args.no_llm:
        print(f"Inicializando LLMManager (llama3.2:3b, cloud={args.provider})...")
        try:
            from core.llm_manager import LLMManager
            llm = LLMManager(local_model="llama3.2:3b", cloud_provider=args.provider, preset="balanceado")
            results["env"]["ollama_available"] = getattr(llm, "ollama_available", None)
            results["env"]["cloud_provider"] = getattr(llm, "cloud_provider", None)
            results["env"]["cloud_model_present"] = getattr(llm, "cloud_model", None) is not None
        except Exception as e:
            print(f"  No se pudo iniciar LLMManager: {type(e).__name__}: {e}")
            llm = None

    tiers = [("local", True, False)]
    if args.cloud:
        tiers.append(("cloud", False, True))

    # ── LLM scenarios ─────────────────────────────────────────────────────────
    if llm is not None:
        for label, user_input in LLM_SCENARIOS:
            prompt = SYSTEM_PREAMBLE + f"Usuario: {user_input}\nZephyro:"
            for tier_name, fl, fc in tiers:
                runs = []
                for _ in range(args.repeats):
                    runs.append(_first_sentence_metrics(llm, prompt, fl, fc, args.min_words))
                agg = {
                    "scenario": label, "tier": tier_name, "user_input": user_input,
                    "ttft_ms": _avg([r["ttft_ms"] for r in runs]),
                    "first_sentence_ms": _avg([r["first_sentence_ms"] for r in runs]),
                    "gen_total_ms": _avg([r["gen_total_ms"] for r in runs]),
                    "chars": _avg([r["chars"] for r in runs]),
                    "sentences": _avg([r["sentences"] for r in runs]),
                    "first_sentence_text": runs[-1]["first_sentence_text"],
                    "provider": runs[-1]["provider"], "model": runs[-1]["model"],
                    "error": runs[-1]["error"],
                }
                # TTS de la 1ª oración (proxy del arranque de audio con streaming)
                if not args.no_tts and not agg["error"]:
                    first_s = runs[-1]["first_sentence_text"] or "Hola."
                    tts_first_ms, ok1, _e1 = await _measure_tts(first_s, args.tts_engine)
                    agg["tts_first_sentence_ms"] = tts_first_ms
                    agg["tts_first_ok"] = ok1
                else:
                    agg["tts_first_sentence_ms"] = None
                    agg["tts_first_ok"] = None
                results["llm"].append(agg)
                _print_llm_row(agg)

    # ── Deterministic samples (sólo TTS) ──────────────────────────────────────
    if not args.no_tts:
        for label, sample in DETERMINISTIC_SAMPLES:
            ms, ok, err = await _measure_tts(sample, args.tts_engine)
            row = {"scenario": label, "text": sample, "tts_full_ms": ms,
                   "ok": ok, "error": err, "gen_total_ms": 0.0,
                   "baseline_TTFA_ms": ms, "streaming": "N/A (determinista)"}
            results["deterministic"].append(row)
            print(f"  [determinista] {label:16s} TTS={ms:7.0f}ms  TTFA={ms:7.0f}ms  "
                  f"(gen=0, streaming N/A)  ok={ok}{' ERR:'+err if err else ''}")

        # ── TTS curve por longitud ───────────────────────────────────────────
        for label, text in TTS_TEXT_SAMPLES:
            ms, ok, err = await _measure_tts(text, args.tts_engine)
            first = split_tts_sentences(text)
            first_ms = None
            if first:
                first_ms, _, _ = await _measure_tts(first[0], args.tts_engine)
            row = {"scenario": label, "chars": len(text), "sentences": count_sentences(text),
                   "tts_full_ms": ms, "tts_first_sentence_ms": first_ms, "ok": ok, "error": err}
            results["tts_curve"].append(row)
            print(f"  [tts-curve]    {label:16s} chars={len(text):4d} "
                  f"full={ms:7.0f}ms  1ªoración={first_ms or 0:7.0f}ms  ok={ok}")

    _write_report(results, args)
    _append_history(results)
    _compare_previous(results)
    print(f"\nReporte: {_REPORT_TXT}\nHistorial: {_HISTORY_JSONL}")


def _print_llm_row(a):
    if a["error"]:
        print(f"  [LLM] {a['scenario']:14s} {a['tier']:5s}  ERROR: {a['error']}")
        return
    gen = a["gen_total_ms"] or 0
    fs = a["first_sentence_ms"] or 0
    ttsf = a.get("tts_first_sentence_ms")
    # baseline TTFA aproximado: gen + (síntesis de todo ≈ proporcional). Para la
    # comparación usamos la síntesis de 1ª oración como proxy del arranque.
    streaming_ttfa = (fs + ttsf) if ttsf is not None else None
    print(f"  [LLM] {a['scenario']:14s} {a['tier']:5s} "
          f"prov={str(a['provider']):8s} TTFT={a['ttft_ms'] or 0:6.0f}ms "
          f"first_sent={fs:6.0f}ms gen={gen:6.0f}ms chars={a['chars'] or 0:4.0f} "
          f"sent={a['sentences'] or 0:.0f} "
          f"TTS_1st={ttsf if ttsf is not None else 0:6.0f}ms "
          f"-> stream_TTFA~{streaming_ttfa if streaming_ttfa else 0:6.0f}ms")


def _write_report(results, args):
    lines = []
    lines.append("=" * 78)
    lines.append(f"STREAMING TTS — BASELINE (Fase 0)   {results['timestamp']}")
    lines.append(f"args: {results['args']}")
    lines.append(f"env:  {results['env']}")
    lines.append("=" * 78)
    lines.append("")
    lines.append("LLM (streameable) — tiempos de generación + arranque de audio proyectado")
    lines.append("-" * 78)
    header = (f"{'escenario':15s}{'tier':6s}{'prov':9s}{'TTFT':>8s}{'1ªorac':>9s}"
              f"{'gen':>8s}{'chars':>7s}{'sent':>6s}{'TTS_1ª':>9s}{'streamTTFA':>12s}")
    lines.append(header)
    for a in results["llm"]:
        if a["error"]:
            lines.append(f"{a['scenario']:15s}{a['tier']:6s}  ERROR: {a['error']}")
            continue
        fs = a["first_sentence_ms"] or 0
        ttsf = a.get("tts_first_sentence_ms") or 0
        lines.append(
            f"{a['scenario']:15s}{a['tier']:6s}{str(a['provider'] or '-'):9s}"
            f"{a['ttft_ms'] or 0:8.0f}{fs:9.0f}{a['gen_total_ms'] or 0:8.0f}"
            f"{a['chars'] or 0:7.0f}{a['sentences'] or 0:6.0f}{ttsf:9.0f}{fs+ttsf:12.0f}"
        )
    lines.append("")
    lines.append("Deterministas (NO streamean — gen≈0, sólo síntesis del string fijo)")
    lines.append("-" * 78)
    for r in results["deterministic"]:
        lines.append(f"{r['scenario']:18s} TTS_full={r['tts_full_ms']:8.0f}ms  "
                     f"TTFA={r['baseline_TTFA_ms']:8.0f}ms  {r['streaming']}  "
                     f"{'ERR:'+r['error'] if r['error'] else ''}")
    lines.append("")
    lines.append("Curva TTS (síntesis vs largo del texto)")
    lines.append("-" * 78)
    for r in results["tts_curve"]:
        lines.append(f"{r['scenario']:18s} chars={r['chars']:4d} sent={r['sentences']:2d} "
                     f"full={r['tts_full_ms']:8.0f}ms  1ªoración={r['tts_first_sentence_ms'] or 0:8.0f}ms")
    lines.append("")
    lines.append("Lectura: si en chat_largo/investigacion (1ªoración+TTS_1ª) << gen+TTS_full,")
    lines.append("el streaming TTS adelanta el primer audio significativamente → justifica Fase 1.")
    lines.append("En los deterministas gen=0 → streaming no aporta (gating OFF correcto).")
    _REPORT_TXT.write_text("\n".join(lines), encoding="utf-8")


def _append_history(results):
    with open(_HISTORY_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(results, ensure_ascii=False) + "\n")


def _compare_previous(results):
    """Imprime delta de gen_total y first_sentence vs la corrida previa (si hay)."""
    try:
        if not _HISTORY_JSONL.exists():
            return
        runs = [json.loads(l) for l in _HISTORY_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
        if len(runs) < 2:
            return
        prev = runs[-2]
        prev_map = {(r["scenario"], r["tier"]): r for r in prev.get("llm", [])}
        print("\nDelta vs corrida previa (gen_total_ms):")
        for a in results["llm"]:
            key = (a["scenario"], a["tier"])
            if key in prev_map and not a["error"] and prev_map[key].get("gen_total_ms"):
                d = (a["gen_total_ms"] or 0) - (prev_map[key]["gen_total_ms"] or 0)
                print(f"  {a['scenario']:14s} {a['tier']:5s} {d:+.0f}ms")
    except Exception as e:
        print(f"  (no se pudo comparar con corrida previa: {e})")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(130)
