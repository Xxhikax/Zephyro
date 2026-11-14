"""CAPA 2 se saltea cuando su resultado no puede cambiar el ruteo.

Hallazgo (Codex, verificado contra `router.py`): en `classify_and_parse`, CAPA 2 solo
devuelve algo distinto de `information/respond` si se cumplen LAS TRES condiciones:

    haiku_intent == "command"
    and not TABLE_WORTHY_PATTERN.search(user_input_lower)
    and re.search(rf'\\b{AUTO_VERBS_PATTERN}\\b', user_input_lower)

Las dos últimas NO dependen del modelo. Si cualquiera de ellas ya falla, el resultado
es `information/respond` pase lo que pase — y la llamada a Haiku es un round-trip cuya
respuesta se descarta por construcción.

Medido sobre 100 utterances reales del propio Zephyro (historial de conversaciones, logs
y fixtures): 76 nunca llegan a CAPA 2, y de las 24 que llegan, 20 caen en este caso.
Una llamada a Haiku que no logra conectarse cuesta ~3s antes de rendirse.

El argumento de seguridad acá es ESTRUCTURAL, no estadístico: estos tests prueban que
el camino salteado produce el mismo análisis que producía la rama `else`, y que
`detect_intent` no se invoca. No dependen de que ninguna API responda.
"""
import re

import pytest

from core import verbs as _verbs
from core.app_config import AUTO_VERBS_PATTERN
from core.confidence import SOURCE_CONFIDENCE, confidence_of
from core.router import IntentRouter


def _predicate(text: str) -> bool:
    """Réplica del predicado, para razonar sobre los casos de prueba."""
    low = text.lower()
    table_worthy = bool(_verbs.TABLE_WORTHY_PATTERN.search(low))
    has_auto_verb = re.search(rf'\b{AUTO_VERBS_PATTERN}\b', low) is not None
    return table_worthy or not has_auto_verb


@pytest.fixture
def router():
    # Mismo patrón que test_voseo_routing / test_router_open_source_guard: CAPA 0-2
    # no necesita un LLM manager real, y pasarlo None garantiza que ningún test de
    # ruteo haga una llamada paga por accidente.
    return IntentRouter(None, context_manager=None)


class TestNoLlamaAlModelo:
    @pytest.mark.asyncio
    async def test_sin_verbo_de_accion_no_llama_a_haiku(self, router, monkeypatch):
        """Sin verbo de acción el guard ya falla → la etiqueta del modelo es irrelevante."""
        called = {"n": 0}

        async def spy(_text):
            called["n"] += 1
            return "command"

        monkeypatch.setattr("core.router.detect_intent", spy)
        # Verificado empíricamente que esta frase LLEGA a CAPA 2 (las capas de arriba
        # no la resuelven) y cae en el predicado. Elegir una que se resuelva antes
        # haría pasar el test sin probar nada.
        out = await router.classify_and_parse("el equipo quiere migrar todo a microservicios")
        assert called["n"] == 0, "no debería haber llamado al clasificador LLM"
        assert out["intent"] == "information"
        assert out["_source"] == "capa2_skipped_no_effect"

    @pytest.mark.asyncio
    async def test_pedido_de_tabla_no_llama_a_haiku(self, router, monkeypatch):
        """TABLE_WORTHY gana sobre todo: una comparación es chat, nunca automation."""
        called = {"n": 0}

        async def spy(_text):
            called["n"] += 1
            return "command"

        monkeypatch.setattr("core.router.detect_intent", spy)
        out = await router.classify_and_parse("compará Java y JavaScript en una tabla")
        assert called["n"] == 0
        assert out["intent"] == "information"


class TestMismoResultadoQueAntes:
    """El análisis del camino salteado es el que devolvía la rama `else` de CAPA 2."""

    # Frases verificadas que LLEGAN a CAPA 2 y caen en el predicado.
    REACHES_CAPA2 = [
        "compará Java y JavaScript en una tabla",     # table-worthy
        "estaba pensando en el diseño del módulo",     # sin verbo de acción
        "el equipo quiere migrar todo a microservicios",
        "nada importante igual",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", REACHES_CAPA2)
    async def test_dict_es_el_que_devolvia_la_rama_else(self, router, text):
        assert _predicate(text), f"este caso debe caer en el predicado: {text!r}"
        out = await router.classify_and_parse(text)
        assert out["_source"] == "capa2_skipped_no_effect", (
            f"se resolvió antes de CAPA 2 ({out.get('_source')}), el caso no prueba nada"
        )
        # Exactamente el dict que producía la rama `else` de CAPA 2.
        assert out["intent"] == "information"
        assert out["action"] == "respond"
        assert out["target"] == ""
        assert out["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_misma_confianza_para_el_gate(self, router):
        """El ClarificationGate tiene que decidir igual que antes.

        `confidence_of` prioriza el campo explícito `confidence` sobre el mapa de
        `_source`, y la rama `else` de CAPA 2 también devolvía `confidence: 0.9`.
        Así que lo que hay que comparar es contra el dict REAL que producía esa rama,
        no contra un `{"_source": "capa2_haiku"}` pelado que producción nunca emite.
        """
        out = await router.classify_and_parse("nada importante igual")
        assert out["_source"] == "capa2_skipped_no_effect"
        rama_else_real = {
            "intent": "information", "action": "respond", "target": "",
            "confidence": 0.9, "_source": "capa2_haiku",
        }
        assert confidence_of(out) == confidence_of(rama_else_real)

    def test_el_source_nuevo_esta_mapeado(self):
        """Higiene que pide el docstring de confidence.py: todo `_source` del router
        debe estar en el mapa, para el caso en que el dict llegue sin `confidence`."""
        assert SOURCE_CONFIDENCE["capa2_skipped_no_effect"] == SOURCE_CONFIDENCE["capa2_haiku"]


class TestElPredicadoEsLaNegacionExacta:
    """Si el predicado y los guards se desincronizan, el skip cambia comportamiento."""

    @pytest.mark.asyncio
    async def test_con_verbo_de_accion_si_llama_a_haiku(self, router, monkeypatch):
        """El safety-net de automation tiene que seguir vivo."""
        called = {"n": 0}

        async def spy(_text):
            called["n"] += 1
            return "command"

        monkeypatch.setattr("core.router.detect_intent", spy)
        # Una frase con verbo de acción y sin keyword de app conocida: CAPA 1 no la
        # resuelve, así que llega a CAPA 2 y el modelo SÍ puede cambiar el ruteo.
        out = await router.classify_and_parse("ejecutá el flujo de facturación de la empresa")
        if out.get("_source") == "capa2_skipped_no_effect":
            pytest.fail("el predicado se comió un caso donde el modelo sí decide")
        if called["n"] == 0 and out.get("_source") not in (None, "capa2_haiku"):
            # CAPA 0/1 lo resolvió antes: válido, pero entonces no prueba nada.
            pytest.skip(f"resuelto antes de CAPA 2 por {out.get('_source')}")
        assert called["n"] == 1

    def test_el_predicado_usa_los_mismos_patrones_que_el_guard(self):
        """Mismo `\\b...\\b` que el guard: sin los bordes, el predicado matchearía de más."""
        import inspect
        src = inspect.getsource(IntentRouter.classify_and_parse)
        assert "_table_worthy = bool(_verbs.TABLE_WORTHY_PATTERN.search(user_input_lower))" in src
        assert r"_has_auto_verb = re.search(rf'\b{AUTO_VERBS_PATTERN}\b', user_input_lower)" in src
        # El guard de abajo reusa las variables, no recalcula: si recalculara con otro
        # patrón, podrían divergir en silencio.
        assert "and not _table_worthy" in src
        assert "and _has_auto_verb" in src
