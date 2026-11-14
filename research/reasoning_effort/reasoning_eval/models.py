"""Routers compared against the hand-written regex heuristic."""
from __future__ import annotations

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut, cross_val_predict
from sklearn.pipeline import make_pipeline


class MajorityRouter:
    """Always predicts the most frequent training label: the floor to beat."""

    def fit(self, texts, labels):
        values, counts = np.unique(labels, return_counts=True)
        self.label_ = values[np.argmax(counts)]
        return self

    def predict(self, texts):
        return np.array([self.label_] * len(texts))


def tfidf_logreg(C: float = 1.0):
    """Character n-grams instead of words: Spanish inflects heavily ("calculá",
    "calcular", "calculame") and the prompts are short, so word features are sparse.
    Balanced class weights keep `high` from being drowned by the easier classes."""
    return make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, strip_accents="unicode"),
        LogisticRegression(C=C, class_weight="balanced", max_iter=5000),
    )


def loocv_predictions(texts, labels, C: float = 1.0) -> np.ndarray:
    """Leave-one-out predictions: with 40 training prompts, every example counts."""
    return cross_val_predict(tfidf_logreg(C), list(texts), np.asarray(labels), cv=LeaveOneOut())
