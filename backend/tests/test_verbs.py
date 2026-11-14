# -*- coding: utf-8 -*-
"""
Tests de la fuente única de verbos (core/verbs.py).

Verifican las funciones derivadas Y la coherencia interna: que DOMAIN_VERBS y
VOSEO_TO_TUTEO no diverjan (todo voseo declarado tiene mapeo, todo destino existe).
"""
import re

from core import verbs


def test_normalize_voseo_basic():
    assert verbs.normalize_voseo("agendá X") == "agenda X"
    assert verbs.normalize_voseo("poné lofi") == "pon lofi"
    assert verbs.normalize_voseo("recordame pan") == "recuérdame pan"
    assert verbs.normalize_voseo("leé el archivo") == "lee el archivo"
    assert verbs.normalize_voseo("buscá gatos") == "busca gatos"


def test_normalize_voseo_nfc_and_empty():
    assert verbs.normalize_voseo("") == ""
    # NFC: 'á' descompuesto (a + combining acute) debe normalizar igual
    decomposed = "agendá X"  # "agendá X" en NFD
    assert verbs.normalize_voseo(decomposed) == "agenda X"


def test_is_action_verb():
    assert verbs.is_action_verb("abrí")
    assert verbs.is_action_verb("poné")
    assert verbs.is_action_verb("ABRE")  # case-insensitive
    assert not verbs.is_action_verb("hola")
    assert not verbs.is_action_verb("aburrido")


def test_verbs_for_domains():
    media = verbs.verbs_for("media")
    assert "reproduce" in media and "poné" in media
    assert "agenda" not in media  # schedule, no media
    # include_voseo=False excluye las formas voseo
    no_voseo = verbs.verbs_for("automation", include_voseo=False)
    assert "abre" in no_voseo and "abrí" not in no_voseo


def test_verbs_pattern_is_valid_regex():
    pat = verbs.verbs_pattern("automation", "media")
    compiled = re.compile(r"\b" + pat + r"\b", re.IGNORECASE)
    assert compiled.search("quiero que abrí discord")
    assert compiled.search("poné música")


def test_coherence_every_voseo_form_has_mapping():
    """Toda forma voseo en DOMAIN_VERBS (termina en á/é/í) debe tener entrada
    en VOSEO_TO_TUTEO, salvo que sea ella misma un destino."""
    destinos = set(verbs.VOSEO_TO_TUTEO.values())
    for forms in verbs.DOMAIN_VERBS.values():
        for f in forms:
            if f and f[-1] in "áéí" and len(f) > 2:
                assert f in verbs.VOSEO_TO_TUTEO or f in destinos, \
                    f"forma voseo {f!r} sin mapeo en VOSEO_TO_TUTEO"


def test_coherence_normalize_is_idempotent_on_tuteo():
    """Normalizar una forma tuteo no la cambia (no hay voseo que mapear)."""
    for tuteo in ("abre discord", "pon lofi", "busca gatos", "agenda algo"):
        assert verbs.normalize_voseo(tuteo) == tuteo


def test_app_config_auto_verbs_derives_from_source():
    from core.app_config import AUTO_VERBS
    expected = verbs.verbs_for("automation", "media", "search")
    assert AUTO_VERBS == expected
    assert "abrí" in AUTO_VERBS and "poné" in AUTO_VERBS  # voseo presente


def test_no_voseo_form_maps_to_another_voseo():
    """Los destinos del mapeo no deben ser a su vez formas voseo (evita cadenas)."""
    for voseo, destino in verbs.VOSEO_TO_TUTEO.items():
        assert not (destino and destino[-1] in "áéí" and destino not in ("pásalo",)), \
            f"{voseo!r} mapea a otro voseo {destino!r}"
