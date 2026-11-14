# -*- coding: utf-8 -*-
"""
Regresión del "msedge ghost bug" (call-mode en vivo, 2026-07-02).

"Zephyro, también escuché sobre el fundador de Antropic, Dario Modei, en una
conferencia estaba diciendo que los modelos de IA Open Source eran peligrosos."
→ "✓ Nueva pestaña abierta en msedge".

Causa: la palabra inglesa "Open" en "Open Source" matcheaba el verbo automation
`open` (verbs.py), el regex last-resort extraía "source eran" como query, y sin
target conocido caía al browser default. Los guards conversacionales no cubrían
"estaba diciendo" ni el relato de terceros ("escuché que...").
"""
import asyncio
import unittest
from unittest.mock import MagicMock

from core.router import IntentRouter


def _make_router():
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent": "chat"}')
    return IntentRouter(mock_llm, context_manager=None)


def _classify(text):
    return asyncio.run(_make_router().classify_and_parse(text, skip_llm=True))


class TestOpenSourceNotAutomation(unittest.TestCase):
    """Menciones de "open source"/"open ai" y relatos de terceros NO son comandos."""

    CASES = [
        "también escuché sobre el fundador de antropic, en una conferencia "
        "estaba diciendo que los modelos de ia open source eran peligrosos",
        "cuéntame sobre ia open source",
        "vi un proyecto open source interesante",
        "qué opinas de openai vs open source",
        "me estaba diciendo que probara ese modelo",
        "escuché que hay un bug en el reproductor",
        "dijo que el open source es mejor",
    ]

    def test_not_automation(self):
        for text in self.CASES:
            result = _classify(text)
            self.assertNotEqual(
                result.get("intent"), "automation",
                f"{text!r} → {result.get('intent')}/{result.get('automation_intent')}",
            )


class TestOpenCommandStillWorks(unittest.TestCase):
    """Los comandos ingleses pineados en test_auto_verbs siguen siendo automation."""

    def test_open_firefox_still_automation(self):
        result = _classify("open firefox")
        self.assertEqual(result.get("intent"), "automation")

    def test_open_spotify_still_automation(self):
        result = _classify("open spotify")
        self.assertEqual(result.get("intent"), "automation")

    def test_launch_spotify_still_automation(self):
        result = _classify("launch spotify")
        self.assertEqual(result.get("intent"), "automation")


if __name__ == "__main__":
    unittest.main()
