"""Loading the labeled prompts and the benchmark runs into tidy frames."""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPO = ROOT.parents[1]

LEVELS = ["low", "medium", "high"]
ORD = {lvl: i for i, lvl in enumerate(LEVELS)}


def load_prompts() -> pd.DataFrame:
    """Both splits, one row per prompt, with the blind second annotator's label."""
    frames = []
    for split in ("dev", "holdout"):
        rows = [json.loads(x) for x in (DATA / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        df = pd.DataFrame(rows)
        df["split"] = split
        df["check_type"] = df["check"].map(lambda c: c["type"])
        frames.append(df)
    prompts = pd.concat(frames, ignore_index=True)
    llm = pd.read_json(DATA / "labels_deepseek_pro.jsonl", lines=True)
    prompts = prompts.merge(llm, on="id", how="left")
    prompts["expected_level"] = pd.Categorical(prompts["expected_level"], LEVELS, ordered=True)
    return prompts[["id", "split", "category", "prompt", "expected_level", "llm_level", "check_type"]]


def load_runs() -> pd.DataFrame:
    """One row per (prompt, config, repeat) from every saved benchmark run."""
    frames = []
    for split in ("dev", "holdout"):
        path = DATA / "runs" / f"{split}_deepseek.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["results"] if isinstance(payload, dict) else payload
        df = pd.DataFrame(rows)
        df["split"] = split
        frames.append(df)
    runs = pd.concat(frames, ignore_index=True)
    runs = runs[runs["error"].isna()] if "error" in runs else runs
    runs["thinking"] = runs["level"].ne("off")
    return runs


def heuristic():
    """The production router, imported from the backend (pure module, no deps)."""
    backend = str(REPO / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from core.reasoning_policy import pick_reasoning
    return pick_reasoning
