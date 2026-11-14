"""Tests para core.confidence — señal de confianza derivada de _source."""
from core.confidence import confidence_of, SOURCE_CONFIDENCE


def test_explicit_confidence_wins_over_source():
    a = {"_source": "capa2_haiku", "confidence": 0.95}
    assert confidence_of(a) == 0.95


def test_underscore_confidence_field():
    a = {"automation_intent": "PLAY_MEDIA", "_confidence": 0.6}
    assert confidence_of(a) == 0.6


def test_source_mapping():
    assert confidence_of({"_source": "capa0_rules"}) == 1.0
    assert confidence_of({"_source": "capa1_compound"}) == 0.9
    assert confidence_of({"_source": "capa2_haiku"}) == 0.6
    assert confidence_of({"_source": "cloud_fast_lane"}) == 0.4


def test_intent_fallback_when_no_source():
    assert confidence_of({"intent": "automation"}) == 0.5
    assert confidence_of({"intent": "chat"}) == 0.4
    assert confidence_of({"intent": "vision"}) == 0.6


def test_default_when_nothing_recognizable():
    assert confidence_of({}) == 0.5
    assert confidence_of({"intent": "wat"}) == 0.5


def test_non_mapping_input_is_safe():
    assert confidence_of(None) == 0.5
    assert confidence_of("nope") == 0.5


def test_explicit_confidence_is_clamped():
    assert confidence_of({"confidence": 1.5}) == 1.0
    assert confidence_of({"confidence": -0.2}) == 0.0


def test_all_known_router_sources_have_entries():
    # Higiene: las fuentes que el router emite deben estar mapeadas.
    emitted = {
        "capa0_rules", "capa0_6_playback_ctl", "capa1_14c_shopping",
        "capa1_browse_extract", "capa1_media_entities", "capa1_nav_subpath",
        "capa1_compound", "capa1_sequence_skip_llm", "capa2_haiku",
        "capa2_skipped_no_effect",
    }
    assert emitted.issubset(set(SOURCE_CONFIDENCE))
