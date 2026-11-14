"""Tests de la feature Respuestas Ricas (Fase 1).

Cubre:
- prepare_for_tts: tablas/fences/LaTeX nunca llegan al audio; prosa y precios sí.
- make_stream_tts_cleaner: cleaner stateful por-chunk del path streaming TTS
  (bug fix: antes se usaba sanitize() y las tablas se leían en voz alta).
- build_prompt: reglas de formato enriquecido solo cuando NO es voz; la
  excepción visual de tabla solo en voz + cloud tier.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.response_sanitizer import (
    make_stream_tts_cleaner,
    prepare_for_tts,
)
from modules.voice.tts_chunking import iter_stream_chunks

RESPONSE_WITH_TABLE = (
    "Comparativa lista.\n\n"
    "| Lenguaje | Velocidad | Curva |\n"
    "|---|---|---|\n"
    "| Python | Media | Suave |\n"
    "| Rust | Alta | Dura |\n\n"
    "La formula es $$x = \\frac{-b}{2a}$$ y en codigo:\n"
    "```python\nx = 1\n```\n"
    "Eso cuesta $50 y $30 en total. Mas info en https://ejemplo.com"
)


class TestPrepareForTtsRichContent:
    def test_table_rows_never_spoken(self):
        out = prepare_for_tts(RESPONSE_WITH_TABLE)
        assert "|" not in out
        assert "Python" not in out and "Rust" not in out

    def test_block_math_and_fences_never_spoken(self):
        out = prepare_for_tts(RESPONSE_WITH_TABLE)
        assert "$$" not in out and "frac" not in out
        assert "x = 1" not in out and "`" not in out

    def test_prose_and_prices_preserved(self):
        out = prepare_for_tts(RESPONSE_WITH_TABLE)
        assert "Comparativa lista." in out
        # Guard anti-precios: $50 y $30 no son LaTeX, deben sobrevivir.
        assert "50" in out and "30" in out

    def test_inline_math_stripped_but_prices_kept(self):
        out = prepare_for_tts("El valor de $x^2$ crece. Vale $100 hoy.")
        assert "x^2" not in out
        assert "100" in out

    def test_complexity_notation_stripped(self):
        # $O(n)$ no tiene chars TeX pero es math (notación de función).
        out = prepare_for_tts("Espacio: $O(n)$ por el array auxiliar.")
        assert "$" not in out and "O(n)" not in out
        assert "array auxiliar" in out

    def test_pure_table_falls_back_to_listo(self):
        out = prepare_for_tts("| a | b |\n|---|---|\n| 1 | 2 |")
        assert out == "Listo."


class TestStreamTtsCleaner:
    def test_fence_state_across_chunks(self):
        clean = make_stream_tts_cleaner()
        seq = [
            "Aqui va la comparacion.",
            "```python",
            "x = 1",
            "```",
            "Listo el resumen.",
        ]
        out = [clean(s) for s in seq]
        assert out[0] == "Aqui va la comparacion."
        assert out[1:4] == ["", "", ""]
        assert out[4] == "Listo el resumen."

    def test_table_rows_and_fragments_dropped(self):
        clean = make_stream_tts_cleaner()
        assert clean("| Python | Media | Suave |") == ""
        # Fragmento de fila partida por el flush de min_words.
        assert clean("Alta | Dura |") == ""

    def test_block_math_state_across_chunks(self):
        clean = make_stream_tts_cleaner()
        assert clean("$$") == ""
        assert clean("x = 2a") == ""
        assert clean("$$") == ""
        assert clean("Y eso es todo.") == "Y eso es todo."

    def test_state_does_not_leak_between_instances(self):
        c1 = make_stream_tts_cleaner()
        c1("```python")  # deja c1 dentro de un fence
        c2 = make_stream_tts_cleaner()
        assert c2("Frase normal.") == "Frase normal."

    def test_markdown_symbols_cleaned_in_prose(self):
        clean = make_stream_tts_cleaner()
        out = clean("**Rust** es mas rapido, ver [docs](https://rust-lang.org).")
        assert "*" not in out and "[" not in out and "https" not in out
        assert "Rust" in out and "docs" in out

    def test_through_iter_stream_chunks(self):
        tokens = [
            "Aqui va", " la comparacion.", "\n",
            "| Python | Media |", "\n",
            "| Rust | Alta |", "\n",
            "```python", "\n", "x = 1", "\n", "```", "\n",
            "Listo el resumen.",
        ]
        clean = make_stream_tts_cleaner()
        spoken = list(iter_stream_chunks(iter(tokens), clean=clean))
        assert any("comparacion" in s for s in spoken)
        assert any("resumen" in s for s in spoken)
        joined = " ".join(spoken)
        assert "|" not in joined and "`" not in joined and "x = 1" not in joined


class TestBuildPromptRichRules:
    @classmethod
    def setup_class(cls):
        from core.context_manager import ContextManager

        cls.cm = ContextManager(use_vector_memory=False)

    def _prompt(self, **kwargs):
        return self.cm.build_prompt("comparame python y rust", skip_memory=True, **kwargs)

    def test_text_mode_enables_rich_format(self):
        p = self._prompt(speak=False)
        assert "FORMATO ENRIQUECIDO" in p
        assert "tabla Markdown" in p

    def test_text_mode_table_hint(self):
        p = self._prompt(speak=False, rich_hint="table")
        assert "presentá la comparación como tabla" in p

    def test_voice_local_keeps_plain_speech(self):
        p = self._prompt(speak=True, cloud_tier=False)
        assert "PROHIBIDO: markdown" in p
        assert "FORMATO ENRIQUECIDO" not in p
        assert "EXCEPCIÓN VISUAL" not in p

    def test_voice_cloud_allows_trailing_table(self):
        p = self._prompt(speak=True, cloud_tier=True)
        assert "PROHIBIDO: markdown" in p
        assert "EXCEPCIÓN VISUAL" in p

    def test_detail_normal_gets_rich_rules(self):
        p = self._prompt(detail_level="normal", speak=False)
        assert "FORMATO ENRIQUECIDO" in p

    def test_detail_voice_cloud_gets_visual_exception(self):
        p = self._prompt(detail_level="normal", speak=True, cloud_tier=True)
        assert "EXCEPCIÓN VISUAL" in p

    def test_detail_voice_local_stays_plain(self):
        p = self._prompt(detail_level="normal", speak=True, cloud_tier=False)
        assert "EXCEPCIÓN VISUAL" not in p
        assert "PROHIBIDO: markdown" in p

    def test_mermaid_rule_only_on_cloud_tier(self):
        assert "```mermaid" in self._prompt(speak=False, cloud_tier=True)
        assert "```mermaid" not in self._prompt(speak=False, cloud_tier=False)
        # En voz jamás se sugiere mermaid, ni en cloud.
        assert "```mermaid" not in self._prompt(speak=True, cloud_tier=True)

    def test_chart_rule_only_on_cloud_tier(self):
        assert "```chart" in self._prompt(speak=False, cloud_tier=True)
        assert "```chart" not in self._prompt(speak=False, cloud_tier=False)
        assert "```chart" not in self._prompt(speak=True, cloud_tier=True)

    def test_algorithm_rule_only_on_cloud_tier(self):
        assert "tabla de TRAZA" in self._prompt(speak=False, cloud_tier=True)
        assert "tabla de TRAZA" not in self._prompt(speak=False, cloud_tier=False)
        assert "tabla de TRAZA" not in self._prompt(speak=True, cloud_tier=True)

    def test_algorithm_rule_leads_with_concept(self):
        p = self._prompt(speak=False, cloud_tier=True)
        assert "empezá SIEMPRE con el concepto" in p
        assert "qué problema resuelve" in p


class TestChartRenderer:
    VALID_SPEC = {
        "type": "bar",
        "title": "Poblacion",
        "labels": ["1970", "1980", "1990"],
        "series": [{"label": "Argentina", "values": [23.9, 28.1, 32.6]}],
        "ylabel": "millones",
    }

    def test_valid_spec_renders_decodable_png(self):
        import base64
        import io

        from PIL import Image

        from modules.charts.renderer import render_chart_spec

        uri = render_chart_spec(dict(self.VALID_SPEC))
        assert uri is not None and uri.startswith("data:image/png;base64,")
        img = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
        assert img.format == "PNG" and img.width > 100

    def test_invalid_specs_return_none(self):
        from modules.charts.renderer import render_chart_spec

        assert render_chart_spec({"type": "evil"}) is None
        assert render_chart_spec({"type": "bar", "series": []}) is None
        assert render_chart_spec({"type": "bar", "series": [{"values": ["x"]}]}) is None
        assert render_chart_spec(
            {"type": "line", "series": [{"values": list(range(501))}]}
        ) is None
        assert render_chart_spec("no soy un dict") is None

    def test_replace_chart_blocks_valid(self):
        import json

        from modules.charts.renderer import replace_chart_blocks

        text = (
            "Aca va el grafico:\n```chart\n"
            + json.dumps(self.VALID_SPEC)
            + "\n```\nFin."
        )
        out = replace_chart_blocks(text)
        assert "![Poblacion](data:image/png;base64," in out
        assert "```chart" not in out
        assert "Aca va el grafico" in out and "Fin." in out

    def test_replace_chart_blocks_invalid_json_leaves_placeholder(self):
        from modules.charts.renderer import replace_chart_blocks

        out = replace_chart_blocks("Mira:\n```chart\n{esto no es json}\n```")
        assert "(gráfico inválido)" in out
        assert "{esto no es json}" not in out

    def test_text_without_chart_untouched(self):
        from modules.charts.renderer import replace_chart_blocks

        text = "Respuesta normal con ```python\nx=1\n``` adentro."
        assert replace_chart_blocks(text) == text


class TestCapa2CommandGuard:
    """Bug de call mode: Haiku clasifica imperativos ("Compará X e Y en una
    tabla") como 'command' y CAPA 2 los ruteaba a automation con target vacío
    → muerte segura en app_controller ("No estoy seguro de como ejecutar esa
    accion"). El guard exige un verbo de acción real (AUTO_VERBS) y excluye
    pedidos de tabla/comparación."""

    def _classify(self, text, haiku_intent="command"):
        import asyncio

        import core.router as router_mod

        orig_detect = router_mod.detect_intent
        orig_extract = router_mod.extract_browser_intent

        async def _fake_detect(_text):
            return haiku_intent

        async def _fake_extract(_text, _llm):
            return None

        router_mod.detect_intent = _fake_detect
        router_mod.extract_browser_intent = _fake_extract
        try:
            r = router_mod.IntentRouter(llm_manager=None)
            return asyncio.run(r.classify_and_parse(text))
        finally:
            router_mod.detect_intent = orig_detect
            router_mod.extract_browser_intent = orig_extract

    def test_comparison_in_table_never_automation(self):
        res = self._classify("Compara Java y JavaScript en una tabla.")
        assert res["intent"] != "automation", res

    def test_stt_mishear_with_tabla_never_automation(self):
        res = self._classify("Y ahora me llame java y javascript en una tabla.")
        assert res["intent"] != "automation", res

    def test_command_without_action_verb_is_information(self):
        res = self._classify("decime cual es la capital de francia")
        assert res["intent"] != "automation", res

    def test_real_action_verb_keeps_automation_safety_net(self):
        res = self._classify("ejecutá el protocolo de siempre")
        assert res["intent"] == "automation", res

    def test_table_worthy_pattern_shared(self):
        from core.verbs import TABLE_WORTHY_PATTERN

        assert TABLE_WORTHY_PATTERN.search("comparáme python y rust")
        assert TABLE_WORTHY_PATTERN.search("diferencias entre java y javascript")
        assert not TABLE_WORTHY_PATTERN.search("abrí spotify")


class TestTtsImageStripping:
    def test_prepare_for_tts_drops_markdown_images(self):
        chart_md = "El grafico:\n![Poblacion](data:image/png;base64,iVBORw0KGgo=)\nListo."
        out = prepare_for_tts(chart_md)
        assert "data:image" not in out and "![" not in out and "Poblacion" not in out
        assert "El grafico:" in out and "Listo." in out

    def test_stream_cleaner_drops_image_chunks(self):
        clean = make_stream_tts_cleaner()
        assert clean("![Poblacion](data:image/png;base64,iVBORw0KGgo=)") == ""
        assert clean("Como se ve arriba.") == "Como se ve arriba."
