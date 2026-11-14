"""
Benchmark: end-to-end response latency matrix (case x provider x mode) over the
live WebSocket.

Measures, per request class (greeting / chat / web search), cloud provider
(anthropic / deepseek) and mode (text / call-mode streaming TTS):
  - t_response   : ms until the `response` frame
  - t_first_tts  : ms until the first `tts_chunk` (time-to-first-audio)
  - t_last_tts   : ms until the `is_last` chunk
  - chunk order, whether `is_last` arrived, backend timings (llm/router/context)
and flags spoken-text quality issues ("Listo." mid-reply, URLs, markdown).

Usage (backend must be running on localhost:8000):
    set ZEPHYRO_BENCH=1
    set ZEPHYRO_ADMIN_PASSWORD=...
    backend/venv/Scripts/python.exe backend/tests/bench_latency_matrix.py \
        [--providers anthropic,deepseek] [--modes text,call] [--repeats 3] \
        [--cases greet_exact,chat_long] [--out report.json]

Costs cloud tokens. NOT collected by pytest (__test__ = False).
"""
__test__ = False

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
import uuid
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)

# 127.0.0.1: "localhost" costs ~2 s per connection on Windows (IPv6 ::1 tried first).
BASE = os.environ.get("ZEPHYRO_BASE", "http://127.0.0.1:8000")
WS_URL = BASE.replace("http", "ws", 1) + "/api/ws"
TIMEOUT_TOTAL_S = 150.0
TIMEOUT_FRAME_S = 90.0
WS_MAX_SIZE = 20 * 1024 * 1024

CASES = [
    {"id": "greet_exact", "kind": "greeting", "msg": "hola"},
    {"id": "greet_free", "kind": "greeting", "msg": "hola zephyro, ¿cómo estás hoy?"},
    {"id": "chat_short", "kind": "chat", "msg": "¿Qué me recomendás para dormir mejor?"},
    {"id": "chat_long", "kind": "chat", "msg": "Explicame cómo funciona la fotosíntesis paso a paso"},
    {"id": "web_fact", "kind": "web", "msg": "¿Cuál es el precio actual del bitcoin en dólares?"},
    {"id": "web_news", "kind": "web", "msg": "¿Cuáles son las últimas noticias de tecnología de hoy?"},
]

_RE_URL = re.compile(r"https?://|www\.")
_RE_MD = re.compile(r"\*\*|__|`|^#{1,6}\s|\|.*\|", re.M)


def _spoken_issues(chunks: list[dict]) -> list[str]:
    issues = []
    texts = [c.get("text") or "" for c in chunks]
    for i, t in enumerate(texts):
        if t.strip().rstrip(".").lower() == "listo" and len(texts) > 1:
            issues.append(f"'Listo.' spoken at chunk {i}")
        if _RE_URL.search(t):
            issues.append(f"URL spoken at chunk {i}")
        if _RE_MD.search(t):
            issues.append(f"markdown spoken at chunk {i}")
    idx = [c.get("chunk_index") for c in chunks if c.get("chunk_index") is not None]
    if idx != sorted(idx):
        issues.append(f"chunks out of order: {idx}")
    return issues


async def _login(httpx) -> str:
    pw = os.environ.get("ZEPHYRO_ADMIN_PASSWORD")
    if not pw:
        print("[FAIL] ZEPHYRO_ADMIN_PASSWORD not set")
        sys.exit(1)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{BASE}/api/auth/login", json={"password": pw})
    data = r.json()
    if not data.get("success") or data.get("role") != "admin":
        print(f"[FAIL] admin login failed: {data.get('message') or data.get('role')}")
        sys.exit(1)
    return data["token"]


async def _set_provider(httpx, token: str, provider: str) -> bool:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{BASE}/api/settings/provider",
            params={"provider": provider},
            headers={"Authorization": f"Bearer {token}"},
        )
    ok = r.status_code == 200 and r.json().get("success")
    print(f"[INFO] provider -> {provider}: {'OK' if ok else 'FAILED ' + r.text[:120]}")
    return bool(ok)


async def _run_one(websockets, token: str, case: dict, mode: str) -> dict:
    call = mode == "call"
    payload = {
        "token": token,
        "message": case["msg"],
        "mode": "cloud",
        "stream": False,
        "tts": call,
        "stream_tts": call,
        "request_id": str(uuid.uuid4()),
    }
    out = {"t_response": None, "t_first_tts": None, "t_last_tts": None, "is_last": False,
           "chunks": [], "response": "", "timings": {}, "error": None}
    t0 = time.perf_counter()
    try:
        async with websockets.connect(WS_URL, open_timeout=5, max_size=WS_MAX_SIZE,
                                      additional_headers={"Authorization": f"Bearer {token}"}) as ws:
            await ws.send(json.dumps(payload))
            deadline = t0 + TIMEOUT_TOTAL_S
            while (remaining := deadline - time.perf_counter()) > 0:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(TIMEOUT_FRAME_S, remaining))
                except asyncio.TimeoutError:
                    out["error"] = "frame timeout"
                    break
                ms = int((time.perf_counter() - t0) * 1000)
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mtype = msg.get("type")
                if mtype == "response":
                    out["t_response"] = ms
                    out["response"] = msg.get("response") or ""
                    out["timings"] = {k: msg.get(k) for k in ("llm_ms", "router_ms", "context_build_ms", "total_ms")}
                    if not call or out["is_last"]:
                        break
                elif mtype == "tts_chunk":
                    if out["t_first_tts"] is None and msg.get("audio"):
                        out["t_first_tts"] = ms
                    if msg.get("audio"):
                        out["chunks"].append({"chunk_index": msg.get("chunk_index"), "text": msg.get("text"),
                                          "has_audio": bool(msg.get("audio")), "ms": ms})
                    if msg.get("is_last"):
                        out["is_last"] = True
                        out["t_last_tts"] = ms
                        if out["t_response"] is not None:
                            break
                elif mtype == "tts_audio":
                    out["t_first_tts"] = out["t_last_tts"] = ms
                    out["is_last"] = True
                    break
                elif mtype in ("error", "cancelled"):
                    out["error"] = msg.get("message") or mtype
                    break
    except Exception as exc:
        out["error"] = str(exc)
    out["total_ms"] = int((time.perf_counter() - t0) * 1000)
    out["issues"] = _spoken_issues(out["chunks"]) if call else []
    if call and out["t_response"] is not None and not out["is_last"]:
        out["issues"].append("no is_last received")
    return out


def _p50(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return int(statistics.median(vals)) if vals else None


def _max(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return max(vals) if vals else None


async def main() -> None:
    if not os.environ.get("ZEPHYRO_BENCH"):
        print("Skipping: set ZEPHYRO_BENCH=1 (costs cloud tokens, needs backend on :8000)")
        return
    import httpx
    import websockets

    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="anthropic,deepseek")
    ap.add_argument("--modes", default="text,call")
    ap.add_argument("--cases", default=",".join(c["id"] for c in CASES))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default=str(Path(__file__).with_name("_latency_matrix_report.json")))
    args = ap.parse_args()

    wanted = set(args.cases.split(","))
    cases = [c for c in CASES if c["id"] in wanted]
    token = await _login(httpx)
    results = []
    try:
        for provider in args.providers.split(","):
            if not await _set_provider(httpx, token, provider):
                continue
            await _run_one(websockets, token, {"msg": "hola"}, "text")  # warm-up
            for mode in args.modes.split(","):
                for case in cases:
                    runs = []
                    for _ in range(args.repeats):
                        runs.append(await _run_one(websockets, token, case, mode))
                    row = {
                        "provider": provider, "mode": mode, "case": case["id"], "kind": case["kind"],
                        "t_response_p50": _p50(r["t_response"] for r in runs),
                        "t_response_max": _max(r["t_response"] for r in runs),
                        "t_first_tts_p50": _p50(r["t_first_tts"] for r in runs),
                        "t_last_tts_p50": _p50(r["t_last_tts"] for r in runs),
                        "llm_ms_p50": _p50(r["timings"].get("llm_ms") for r in runs),
                        "router_ms_p50": _p50(r["timings"].get("router_ms") for r in runs),
                        "chunks_p50": _p50(len(r["chunks"]) for r in runs),
                        "errors": [r["error"] for r in runs if r["error"]],
                        "issues": sorted({i for r in runs for i in r["issues"]}),
                        "sample_response": runs[-1]["response"][:400],
                    }
                    results.append(row)
                    print(f"  {provider:<9} {mode:<4} {case['id']:<12} "
                          f"resp={row['t_response_p50']}ms tts1={row['t_first_tts_p50']}ms "
                          f"last={row['t_last_tts_p50']}ms llm={row['llm_ms_p50']} "
                          f"router={row['router_ms_p50']} issues={row['issues'] or '-'} "
                          f"errors={row['errors'] or '-'}")
    finally:
        await _set_provider(httpx, token, "auto")

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[INFO] report -> {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
