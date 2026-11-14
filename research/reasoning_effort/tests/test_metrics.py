import numpy as np
import pandas as pd
import pytest

from reasoning_eval import metrics
from reasoning_eval.models import MajorityRouter


def test_under_rate_counts_only_routing_below_the_label():
    y_true = ["high", "high", "low", "medium"]
    y_pred = ["low", "high", "high", "medium"]  # one under, one over, two exact
    assert metrics.under_rate(y_true, y_pred) == 0.25


def test_high_recall_ignores_other_classes_and_is_nan_without_high():
    assert metrics.high_recall(["high", "high", "low"], ["high", "medium", "low"]) == 0.5
    assert np.isnan(metrics.high_recall(["low"], ["low"]))


def test_bootstrap_ci_brackets_the_point_and_collapses_when_constant():
    point, lo, hi = metrics.bootstrap_ci(["a"] * 20, ["a"] * 20, lambda t, p: float(np.mean(t == p)))
    assert point == lo == hi == 1.0
    t = ["a", "b"] * 15
    p = ["a"] * 30
    point, lo, hi = metrics.bootstrap_ci(t, p, lambda t, p: float(np.mean(t == p)), n_boot=2000)
    assert lo < point < hi


def _runs(rows):
    return pd.DataFrame(rows, columns=["id", "cfg", "pass"])


def test_cluster_bootstrap_pairs_by_prompt_and_averages_repeats():
    runs = _runs([
        ("p1", "a", True), ("p1", "a", True), ("p1", "b", False), ("p1", "b", False),
        ("p2", "a", True), ("p2", "a", False), ("p2", "b", True), ("p2", "b", False),
    ])
    point, lo, hi = metrics.cluster_bootstrap_diff(runs, "a", "b", n_boot=500)
    assert point == pytest.approx(0.5)  # p1: 1 - 0, p2: 0.5 - 0.5
    assert lo <= point <= hi


def test_cluster_bootstrap_drops_prompts_missing_a_config():
    runs = _runs([("p1", "a", True), ("p1", "b", True), ("p2", "a", True)])
    assert metrics.cluster_bootstrap_diff(runs, "a", "b", n_boot=50)[0] == 0.0


def test_mcnemar_uses_only_discordant_prompts():
    rows = [(f"q{i}", "a", True) for i in range(6)] + [(f"q{i}", "b", i < 1) for i in range(6)]
    result = metrics.mcnemar_exact(_runs(rows), "a", "b")
    assert result["only_a"] == 5 and result["only_b"] == 0 and result["discordant"] == 5
    assert result["p_value"] == pytest.approx(0.0625)


def test_majority_router_predicts_the_mode():
    router = MajorityRouter().fit(["x"] * 3, np.array(["low", "high", "low"]))
    assert list(router.predict(["y", "z"])) == ["low", "low"]
