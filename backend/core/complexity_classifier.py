import math
import re
from typing import Dict, Optional, Tuple

class ComplexityClassifier:
    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        threshold: float = 0.5,
    ):
        self.weights = weights or {
            "bias": -0.45,
            "length": 0.015,
            "sentences": 0.18,
            "questions": 0.7,
            "colon": 0.35,
            "why_terms": 0.9,
            "tech_terms": 0.6,
            "stop_terms": -0.4,
            "numbers": 0.25,
        }
        self.threshold = threshold
        self._complex_terms = [
            "por qué",
            "porque",
            "cómo funciona",
            "explica",
            "detall",
            "configura",
            "instala",
            "error",
            "traceback",
            "analiza",
            "investiga",
            "optimiza",
            "arquitectura",
        ]
        self._simple_terms = [
            "hola",
            "buenas",
            "gracias",
            "ok",
            "de nada",
            "adios",
            "chau",
        ]

    def featurize(self, text: str) -> Dict[str, float]:
        cleaned = text.strip()
        lower = cleaned.lower()
        tokens = re.findall(r"\w+", lower)
        length = len(tokens)
        sentences = max(cleaned.count("."), cleaned.count("!"), cleaned.count("?"), cleaned.count("\n")) or 1
        features = {
            "length": length,
            "sentences": sentences,
            "questions": lower.count("?"),
            "colon": lower.count(":"),
            "why_terms": sum(1 for term in self._complex_terms if term in lower),
            "tech_terms": sum(1 for kw in ["config", "install", "docker", "api", "latencia", "modelo", "pipeline"] if kw in lower),
            "stop_terms": sum(1 for term in self._simple_terms if term in lower),
            "numbers": min(sum(1 for token in tokens if token.isdigit()), 1),
        }
        return features

    def _score(self, features: Dict[str, float], latency_hint: Optional[float]) -> float:
        score = self.weights.get("bias", 0.0)
        for name, value in features.items():
            score += self.weights.get(name, 0.0) * value
        if latency_hint is not None:
            score += min(latency_hint, 2.0) * 0.05
        return score

    def classify(self, text: str, latency_hint: Optional[float] = None) -> Dict[str, float]:
        features = self.featurize(text)
        # Guard: entradas MUY cortas sin señales de complejidad son triviales aunque tengan
        # dígitos. Sin esto, "1, 2, 3, probando." puntuaba ~0.51 → "complex" sólo por el
        # feature `numbers` (por eso "probando uno dos tres" en palabras sí iba a fast-path).
        if (features["length"] <= 4
                and features["why_terms"] == 0
                and features["tech_terms"] == 0
                and features["questions"] == 0):
            return {"label": "simple", "probability": 0.0, "features": features}
        score = self._score(features, latency_hint)
        try:
            probability = 1.0 / (1.0 + math.exp(-score))
        except OverflowError:
            probability = 1.0 if score > 0 else 0.0
        label = "complex" if probability >= self.threshold else "simple"
        return {
            "label": label,
            "probability": probability,
            "features": features,
        }