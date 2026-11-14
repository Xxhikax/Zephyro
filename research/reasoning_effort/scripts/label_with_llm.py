"""Blind second annotator for the reasoning-effort labels.

DeepSeek V4 Pro labels every prompt from the rubric alone: it never sees the
author's labels or the heuristic. The agreement between both annotators is the
ceiling any router can be measured against.

    python scripts/label_with_llm.py        # writes data/labels_deepseek_pro.jsonl
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
OUT = HERE / "data" / "labels_deepseek_pro.jsonl"
MODEL = "deepseek-v4-pro"

RUBRIC = """You label how much reasoning a personal assistant needs before answering a
message written in Rioplatense Spanish. Pick exactly one level:

- low: small talk, greetings, thanks, a single well-known fact, a short rewrite.
  Answering well needs no intermediate steps.
- medium: an explanation, a comparison, advice or an open plan with no hard
  constraints. Quality improves with structure, but there is no single right
  answer to get wrong.
- high: there IS a right answer that is easy to get wrong without working it out:
  multi-step arithmetic or percentages, date/calendar arithmetic, logic puzzles,
  writing or fixing code, a schedule that must satisfy several constraints, or a
  question built on a false premise.

Message:
<<<
{prompt}
>>>

Reply with ONLY a JSON object: {{"level": "low"}} or {{"level": "medium"}} or {{"level": "high"}}"""


def label(client: OpenAI, row: dict) -> dict:
    resp = client.chat.completions.create(
        model=MODEL, max_tokens=4096,
        messages=[{"role": "user", "content": RUBRIC.format(prompt=row["prompt"])}])
    raw = resp.choices[0].message.content or ""
    m = re.search(r'"level"\s*:\s*"(low|medium|high)"', raw)
    return {"id": row["id"], "llm_level": m.group(1) if m else None}


def main() -> int:
    load_dotenv(REPO / "backend" / ".env")
    client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com", timeout=120)
    rows = []
    for split in ("dev", "holdout"):
        for line in (HERE / "data" / f"{split}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    with ThreadPoolExecutor(max_workers=4) as pool:
        labels = list(pool.map(lambda r: label(client, r), rows))
    OUT.write_text("".join(json.dumps(x) + "\n" for x in labels), encoding="utf-8")
    missing = [x["id"] for x in labels if x["llm_level"] is None]
    print(f"labeled {len(labels) - len(missing)}/{len(labels)} -> {OUT}")
    if missing:
        print("unparsed:", missing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
