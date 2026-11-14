# -*- coding: utf-8 -*-
"""
Regresión de triggers del skill de fitness (barrido NLP 2026-07-02).

Gaps arreglados:
- _LOG_SESSION_RE solo aceptaba el posesivo "mi" ("Completé la rutina" no matcheaba).
- _GENERATE_RE no conocía "programa"/"ejercicios".
- _UPGRADE_RE no conocía "piscina" (que _context_from_text SÍ reconoce) ni el
  artículo ("cambiar a la piscina"); además ctx_map no tenía la key "piscina",
  con lo cual habría degradado silenciosamente a "home".
- _WEEKLY_RE no aceptaba "resumen de la semana".
"""
from skills.builtin.fitness.skill import (
    _LOG_SESSION_RE, _GENERATE_RE, _UPGRADE_RE, _WEEKLY_RE,
)


class TestLogSession:
    def test_accepts_articles(self):
        for phrase in [
            "completé la rutina",
            "hice el entreno",
            "terminé una sesión",
            "hice mi rutina",
            "completé mi entrenamiento",
        ]:
            assert _LOG_SESSION_RE.search(phrase), phrase


class TestGenerate:
    def test_accepts_programa_ejercicios(self):
        assert _GENERATE_RE.search("hazme un programa de entrenamiento")
        assert _GENERATE_RE.search("dame ejercicios para hoy")
        assert _GENERATE_RE.search("quiero una rutina nueva")


class TestUpgrade:
    def test_accepts_piscina(self):
        m = _UPGRADE_RE.search("cambiar a piscina")
        assert m and m.group("context") == "piscina"

    def test_accepts_piscina_with_article(self):
        m = _UPGRADE_RE.search("voy a cambiar a la piscina")
        assert m and m.group("context") == "piscina"

    def test_ctx_map_has_piscina(self):
        # Compound fix: the regex alone would silently upgrade to "home"
        # because the handler's ctx_map lacked the "piscina" key.
        import inspect
        from skills.builtin.fitness import skill as fitness_skill
        source = inspect.getsource(fitness_skill.FitnessSkill.handle)
        assert '"piscina": "swimming"' in source

    def test_existing_contexts_still_work(self):
        for phrase, expected in [
            ("cambiar a gym", "gym"),
            ("pasarme a natación", "natación"),
            ("modo casa", "casa"),
        ]:
            m = _UPGRADE_RE.search(phrase)
            assert m and m.group("context") == expected, phrase


class TestWeekly:
    def test_accepts_de_la_semana(self):
        assert _WEEKLY_RE.search("resumen de la semana")
        assert _WEEKLY_RE.search("resumen de mi semana")
        assert _WEEKLY_RE.search("resumen semanal")
        assert _WEEKLY_RE.search("cómo me fue esta semana")
