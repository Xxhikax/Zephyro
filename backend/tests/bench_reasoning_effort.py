"""
Benchmark: answer quality vs latency per reasoning-effort config.

Runs every prompt of a dataset (tests/data/reasoning_eval*.jsonl) against several
configs and scores each answer:
  - regex        : pattern on the final "Respuesta:" line (answer_format) or the whole answer
  - python_tests : exec the answer's ```python block + assert lines, in a subprocess (timeout)
  - judge        : blind pairwise judge (Opus) vs the reference config, both orders

Configs (provider:level):
  sonnet:default|low|medium|high|policy      reference for the judge: sonnet:high
  deepseek:off|low|medium|high|policy        reference for the judge: deepseek:off
`policy` asks core.reasoning_policy.pick_reasoning(prompt) for the level.

Usage:
    set ZEPHYRO_BENCH=1
    backend/venv/Scripts/python.exe backend/tests/bench_reasoning_effort.py \
        [--dataset tests/data/reasoning_eval.jsonl] [--configs sonnet:low,sonnet:high] \
        [--repeats 2] [--ids h03,p01] [--no-judge | --judge-reps 1] [--workers 4] [--out report.json]

Calls the Anthropic and DeepSeek APIs directly (no backend needed). Costs tokens.
NOT collected by pytest (__test__ = False).
"""
__test__ = False

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

DEFAULT_CONFIGS = "sonnet:low,sonnet:medium,sonnet:high,deepseek:off,deepseek:low,deepseek:high"
REFERENCE = {"sonnet": "sonnet:high", "deepseek": "deepseek:off"}
ANSWER_SUFFIX = "\n\nTerminá con una línea 'Respuesta: <valor>'."
SYSTEM = (
    "Eres Zephyro, el asistente personal de Bruno. Respondés en español rioplatense, "
    "claro y directo. Si te piden código, dalo en un bloque ```python. "
    "Usá Markdown solo cuando ayude a la lectura."
)
JUDGE_PROMPT = """You compare two answers (A and B) to the same message sent to a Spanish-speaking personal assistant.
Judge correctness first, then whether it actually solves what was asked, then clarity. Do not reward length.
If both are equally good, say tie.

User message:
<<<
{prompt}
>>>

Answer A:
<<<
{a}
>>>

Answer B:
<<<
{b}
>>>

Reply with ONLY a JSON object: {{"better": "A"}} or {{"better": "B"}} or {{"better": "tie"}}"""

_anthropic = None
_deepseek = None


def _env():
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")


def _claude():
    global _anthropic
    if _anthropic is None:
        _env()
        import anthropic
        _anthropic = anthropic.Anthropic()
    return _anthropic


def _ds():
    global _deepseek
    if _deepseek is None:
        _env()
        from openai import OpenAI
        _deepseek = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com",
                           timeout=120)
    return _deepseek


def resolve_level(cfg: str, prompt: str) -> str:
    provider, level = cfg.split(":", 1)
    if level != "policy":
        return level
    from core.reasoning_policy import pick_reasoning
    decision = pick_reasoning(prompt)
    if provider == "deepseek":
        return decision.deepseek_thinking or "off"
    return decision.claude_effort


def run_one(cfg: str, prompt: str, raw_prompt: str) -> dict:
    """`prompt` goes to the API; the policy only sees `raw_prompt` (what a user types),
    never the answer-format suffix the bench appends."""
    from core import model_catalog as mc
    provider = cfg.split(":", 1)[0]
    level = resolve_level(cfg, raw_prompt)
    t0 = time.perf_counter()
    ttft, parts = None, []
    if provider == "sonnet":
        kw = {} if level == "default" else {"output_config": {"effort": level}}
        with _claude().messages.stream(model=mc.ANTHROPIC_MODELS["balanced"], max_tokens=8192, system=SYSTEM,
                                    messages=[{"role": "user", "content": prompt}], **kw) as stream:
            for ev in stream:
                if ev.type == "content_block_delta" and getattr(ev.delta, "type", "") == "text_delta":
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    parts.append(ev.delta.text)
            final = stream.get_final_message()
        meta = {"out_tokens": final.usage.output_tokens,
                "thinking_blocks": sum(1 for b in final.content if b.type == "thinking")}
    else:
        extra = (mc.DEEPSEEK_THINKING_OFF if level == "off"
                 else {"thinking": {"type": "enabled"}, "reasoning_effort": level})
        stream = _ds().chat.completions.create(
            model=mc.DEEPSEEK_CHAT_MODEL, stream=True, extra_body=extra, temperature=0.7,
            stream_options={"include_usage": True},
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
        usage = None
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            if d.content:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                parts.append(d.content)
        meta = {"out_tokens": getattr(usage, "completion_tokens", None)}
    return {"level": level, "ttft": ttft, "total": time.perf_counter() - t0, "text": "".join(parts), **meta}


# ── scoring ───────────────────────────────────────────────────────────────────
# A line that STARTS with the label ("**Respuesta:** 20:30" included); the last one wins.
_ANSWER_LINE = re.compile(r"^\W*respuesta\W*:\W*(.+)$", re.I | re.M)
_PY_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)
_TESTS_DONE = "__BENCH_TESTS_PASSED__"


def score_regex(check: dict, text: str) -> bool:
    target = text
    if check.get("answer_format"):
        hits = _ANSWER_LINE.findall(text)
        if not hits:
            return False  # the answer must come in the requested final line
        value = hits[-1].strip()
        # Datasets anchor either the value ("^4450$") or the whole line
        # ("^Respuesta:\s*4450$"); markdown around the label is dropped first.
        return any(re.search(check["pattern"], t, re.I) for t in (value, f"Respuesta: {value}"))
    return bool(re.search(check["pattern"], target, re.I))


def score_python(check: dict, text: str) -> bool:
    blocks = [b for b in _PY_BLOCK.findall(text) if "def " in b]
    if not blocks:
        return False
    # The marker prints only after every assert ran: a sys.exit(0) in the answer's
    # code can't pass the case by exiting before the tests.
    src = blocks[-1] + "\n\n" + "\n".join(check["tests"]) + f"\nprint({_TESTS_DONE!r})\n"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run([sys.executable, "-I", "-c", src], cwd=tmp, capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            return False
    return r.returncode == 0 and _TESTS_DONE.encode() in r.stdout


def _ask_judge(judge_model: str, content: str) -> str:
    from core import model_catalog as mc
    if judge_model == "opus":
        msg = _claude().messages.create(model=mc.ANTHROPIC_MODELS["power"], max_tokens=4096,
                                        messages=[{"role": "user", "content": content}])
        return "".join(bl.text for bl in msg.content if bl.type == "text")
    # deepseek-pro: thinking stays at its default (on); the verdict is in `content`.
    resp = _ds().chat.completions.create(model=mc.DEEPSEEK_PRO_MODEL, max_tokens=8192,
                                         messages=[{"role": "user", "content": content}])
    return resp.choices[0].message.content or ""


def judge(prompt: str, cand: str, ref: str, judge_model: str = "deepseek-pro") -> str:
    """Blind pairwise, both orders. Returns better|worse|tie for the candidate, or
    invalid when the judge did not answer the requested JSON.

    A deepseek-pro judge grading deepseek answers may favour its own family; swapping
    the order cancels position bias, not that one."""
    votes = []
    for a, b, cand_is in ((cand, ref, "A"), (ref, cand, "B")):
        raw = _ask_judge(judge_model, JUDGE_PROMPT.format(prompt=prompt, a=a, b=b))
        m = re.search(r'"better"\s*:\s*"(A|B|tie)"', raw)
        if not m:
            return "invalid"
        v = m.group(1)
        votes.append("tie" if v == "tie" else ("cand" if v == cand_is else "ref"))
    if votes == ["cand", "cand"]:
        return "better"
    if votes == ["ref", "ref"]:
        return "worse"
    return "tie"


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(BACKEND / "tests/data/reasoning_eval.jsonl"))
    ap.add_argument("--configs", default=DEFAULT_CONFIGS)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--ids", default="")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--judge-model", choices=("deepseek-pro", "opus"), default="deepseek-pro")
    ap.add_argument("--judge-reps", type=int, default=1, help="judge only the first N repeats (cost)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if os.environ.get("ZEPHYRO_BENCH") != "1":
        print("Set ZEPHYRO_BENCH=1 to run (costs API tokens).")
        return 2

    rows = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.ids:
        wanted = set(args.ids.split(","))
        rows = [r for r in rows if r["id"] in wanted]
    configs = args.configs.split(",")
    for provider in {c.split(":")[0] for c in configs}:
        if not args.no_judge and REFERENCE[provider] not in configs:
            configs.append(REFERENCE[provider])

    jobs = [(r, cfg, rep) for rep in range(args.repeats) for r in rows for cfg in configs]

    def _run(job):
        r, cfg, rep = job
        prompt = r["prompt"] + (ANSWER_SUFFIX if r["check"].get("answer_format") else "")
        try:
            res = run_one(cfg, prompt, r["prompt"])
        except Exception as e:
            return {"id": r["id"], "cfg": cfg, "rep": rep, "error": f"{type(e).__name__}: {e}"[:300]}
        out = {"id": r["id"], "category": r["category"], "expected_level": r["expected_level"],
               "cfg": cfg, "rep": rep, **res}
        kind = r["check"]["type"]
        if kind == "regex":
            out["pass"] = score_regex(r["check"], res["text"])
        elif kind == "python_tests":
            out["pass"] = score_python(r["check"], res["text"])
        tag = "" if "pass" not in out else (" PASS" if out["pass"] else " FAIL")
        print(f"{cfg:16s} {r['id']} rep{rep} lvl={res['level']:7s} ttft={res['ttft'] or -1:5.1f}s "
              f"total={res['total']:5.1f}s{tag}", flush=True)
        return out

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(_run, jobs))

    by_key = {(x["id"], x["cfg"], x["rep"]): x for x in results if "error" not in x}
    if not args.no_judge:
        judge_jobs = []
        prompts = {r["id"]: r["prompt"] for r in rows}
        for x in by_key.values():
            ref_cfg = REFERENCE[x["cfg"].split(":")[0]]
            ref = by_key.get((x["id"], ref_cfg, x["rep"]))
            if x["rep"] < args.judge_reps and x["cfg"] != ref_cfg and ref is not None and next(
                    r for r in rows if r["id"] == x["id"])["check"]["type"] == "judge":
                judge_jobs.append((x, ref))

        def _judge(pair):
            x, ref = pair
            try:
                x["judge"] = judge(prompts[x["id"]], x["text"], ref["text"], args.judge_model)
            except Exception as e:
                x["judge_error"] = f"{type(e).__name__}: {e}"[:200]

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(_judge, judge_jobs))

    _summary(results, configs)
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print("saved", args.out)
    return 0


def _summary(results: list, configs: list) -> None:
    ok = [x for x in results if "error" not in x]
    errors = [x for x in results if "error" in x]
    cats = list(dict.fromkeys(x["category"] for x in ok))
    print("\n== per config ==")
    print(f"{'config':16s} {'verif':>9s} {'judge w/t/b':>12s} {'ttft p50':>9s} {'total p50':>10s} {'out tok':>8s}")
    for cfg in configs:
        xs = [x for x in ok if x["cfg"] == cfg]
        if not xs:
            continue
        ver = [x["pass"] for x in xs if "pass" in x]
        jd = [x["judge"] for x in xs if x.get("judge") in ("worse", "tie", "better")]
        ttft = [x["ttft"] for x in xs if x["ttft"] is not None]
        toks = [x["out_tokens"] for x in xs if x.get("out_tokens")]
        print(f"{cfg:16s} {sum(ver):>4d}/{len(ver):<4d} "
              f"{jd.count('worse'):>4d}/{jd.count('tie'):d}/{jd.count('better'):<4d} "
              f"{statistics.median(ttft) if ttft else -1:>8.2f}s "
              f"{statistics.median(x['total'] for x in xs):>9.2f}s "
              f"{int(statistics.mean(toks)) if toks else -1:>8d}")
    print("\n== verifiable pass rate per category ==")
    print(f"{'config':16s} " + " ".join(f"{c[:9]:>9s}" for c in cats))
    for cfg in configs:
        cells = []
        for c in cats:
            ver = [x["pass"] for x in ok if x["cfg"] == cfg and x["category"] == c and "pass" in x]
            cells.append(f"{sum(ver)}/{len(ver)}" if ver else "-")
        print(f"{cfg:16s} " + " ".join(f"{s:>9s}" for s in cells))
    fails = sorted({(x["id"], x["cfg"]) for x in ok if x.get("pass") is False})
    if fails:
        print("\nverifiable failures:", ", ".join(f"{i}@{c}" for i, c in fails))
    worse = sorted({(x["id"], x["cfg"]) for x in ok if x.get("judge") == "worse"})
    if worse:
        print("judge worse-than-reference:", ", ".join(f"{i}@{c}" for i, c in worse))
    invalid = sum(1 for x in ok if x.get("judge") == "invalid")
    judge_errors = [x for x in ok if "judge_error" in x]
    if invalid or judge_errors:
        print(f"judge: {invalid} invalid verdicts, {len(judge_errors)} errors"
              + (f" (e.g. {judge_errors[0]['judge_error'][:120]})" if judge_errors else ""))
    if errors:
        print(f"\n{len(errors)} errors, e.g. {errors[0]['cfg']} {errors[0]['id']}: {errors[0]['error']}")


if __name__ == "__main__":
    sys.exit(main())
