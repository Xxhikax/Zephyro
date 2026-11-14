"""Local vision classifier — Phase A stub.

Future (Phase B): wraps Moondream2 (~1.6B params, ~3GB RAM) loaded via the
HuggingFace transformers library. Provides a free, on-device first-pass
classification of whether the user is on-task during a Pomodoro work phase.

When LOCAL_VISION_AVAILABLE is True and the result has high confidence, the
caller can skip the cloud Haiku call entirely. When False or low confidence,
the caller falls back to the existing cloud vision pipeline.

Phase A: this module is a stub. LOCAL_VISION_AVAILABLE stays False so the
monitor falls through to Haiku at the same 15s cadence.
"""
from __future__ import annotations

from typing import Optional


# Phase A: no local model loaded. Phase B will flip this to True once the
# Moondream2 model is installed and successfully initialized.
LOCAL_VISION_AVAILABLE: bool = False


def classify_screen(image_path: str) -> Optional[dict]:
    """Classify a screen capture as on-task vs off-task.

    Returns a dict with keys:
      - on_task (bool): True if the user appears to be studying/working.
      - category (str): "study" | "social" | "video" | "game" | "chat" | "other" | "".
      - confidence (float): 0.0–1.0. Confidence in the classification.
      - available (bool): mirrors LOCAL_VISION_AVAILABLE for caller convenience.

    Phase A returns a sentinel indicating the local classifier is unavailable.
    The caller is expected to check `available` (or LOCAL_VISION_AVAILABLE
    directly) and fall back to the cloud vision pipeline.
    """
    return {
        "available": LOCAL_VISION_AVAILABLE,
        "on_task": None,
        "category": "",
        "confidence": 0.0,
    }
