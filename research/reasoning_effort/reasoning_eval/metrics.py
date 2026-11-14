"""Metrics with honest uncertainty for small, repeated-measures evaluations.

The benchmark runs every prompt twice, so runs are not independent: two runs of the
same prompt share its difficulty. Every interval here resamples PROMPTS (a cluster
bootstrap), never individual runs. Resampling runs would treat 2 x 64 correlated
samples as 128 independent ones and report intervals that are too narrow.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd
from scipy.stats import binomtest

from .data import ORD


def under_rate(y_true, y_pred) -> float:
    """Share of prompts routed BELOW their label: the costly error (too little thought)."""
    t = np.array([ORD[x] for x in y_true])
    p = np.array([ORD[x] for x in y_pred])
    return float(np.mean(p < t))


def high_recall(y_true, y_pred) -> float:
    """Of the prompts that need `high`, how many get it. NaN when there are none."""
    t = np.asarray(y_true)
    mask = t == "high"
    if not mask.any():
        return float("nan")
    return float(np.mean(np.asarray(y_pred)[mask] == "high"))


def bootstrap_ci(y_true, y_pred, metric: Callable, n_boot: int = 5000, seed: int = 0,
                 alpha: float = 0.05) -> tuple[float, float, float]:
    """Percentile bootstrap over prompts: (point estimate, low, high)."""
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    rng = np.random.default_rng(seed)
    n = len(y_true)
    stats = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        value = metric(y_true[idx], y_pred[idx])
        if not np.isnan(value):
            stats.append(value)
    lo, hi = np.quantile(stats, [alpha / 2, 1 - alpha / 2])
    return float(metric(y_true, y_pred)), float(lo), float(hi)


def cluster_bootstrap_diff(runs: pd.DataFrame, a: str, b: str, value: str = "pass",
                           n_boot: int = 5000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float, float]:
    """Mean(value | config a) - mean(value | config b), paired by prompt.

    Each prompt contributes its mean over repeats for each config; prompts are
    resampled with replacement. Only prompts measured under both configs count.
    """
    per_prompt = (runs[runs["cfg"].isin([a, b])]
                  .groupby(["id", "cfg"])[value].mean().astype(float).unstack("cfg").dropna())
    diff = (per_prompt[a] - per_prompt[b]).to_numpy()
    rng = np.random.default_rng(seed)
    boots = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(n_boot)]
    lo, hi = np.quantile(boots, [alpha / 2, 1 - alpha / 2])
    return float(diff.mean()), float(lo), float(hi)


def mcnemar_exact(runs: pd.DataFrame, a: str, b: str, value: str = "pass") -> dict:
    """Exact McNemar on prompts where the two configs disagree.

    A prompt counts as solved by a config when it passed in the majority of repeats
    (ties count as not solved), which keeps one observation per prompt.
    """
    solved = (runs[runs["cfg"].isin([a, b])]
              .groupby(["id", "cfg"])[value].mean().unstack("cfg").dropna() > 0.5)
    only_a = int((solved[a] & ~solved[b]).sum())
    only_b = int((~solved[a] & solved[b]).sum())
    n = only_a + only_b
    p = 1.0 if n == 0 else binomtest(only_a, n, 0.5).pvalue
    return {"only_" + a: only_a, "only_" + b: only_b, "discordant": n, "p_value": float(p)}
