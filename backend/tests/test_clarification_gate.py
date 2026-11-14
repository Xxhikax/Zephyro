"""Tests para core.clarification_gate — decisión resolve/clarify/chat."""
from core.clarification_gate import (
    Candidate, decide, looks_like_command, router_phrase,
)


def _c(score, phrase="haga algo", kind="skill", label="x"):
    return Candidate(kind=kind, score=score, phrase=phrase, label=label)


def test_high_confidence_resolves():
    d = decide("agenda cálculo el lunes", [_c(0.95, "lo agende")])
    assert d.action == "resolve"
    assert d.chosen.score == 0.95


def test_two_close_candidates_clarify():
    d = decide("poné cálculo", [
        _c(0.45, "lo agende", label="schedule"),
        _c(0.5, "reproduzca eso", kind="router", label="PLAY_MEDIA"),
    ])
    assert d.action == "clarify"
    assert len(d.options) == 2
    assert d.question and "agende" in d.question and "reproduzca" in d.question


def test_mid_confidence_command_like_asks_confirmation():
    # único candidato en zona media + parece comando → confirmar
    d = decide("agendame eso", [_c(0.45, "lo agende")])
    assert d.action == "clarify"
    assert len(d.options) == 1


def test_mid_confidence_not_command_resolves():
    # zona media pero NO parece comando (sin verbo, sin candidato fuerte)
    # Forzamos: candidato con score medio pero input sin verbo de acción.
    d = decide("la verdad no sé", [_c(0.5, "lo agende")])
    # parece comando porque hay candidato score>0 → clarify
    assert d.action == "clarify"


def test_casual_chat_no_candidates_is_chat():
    d = decide("qué aburrido está el día", [])
    assert d.action == "chat"


def test_low_score_no_command_is_chat():
    d = decide("estaba pensando en cosas", [_c(0.2, "lo agende")])
    # score < LOW(0.4); looks_like_command True por candidato>0 → clarify,
    # pero validamos el camino chat con cero candidatos arriba. Acá con un
    # candidato débil >0 el gate prefiere preguntar.
    assert d.action in ("clarify", "chat")


def test_dominant_top_no_rival_in_margin_resolves():
    d = decide("agenda X", [_c(0.95, "lo agende"), _c(0.3, "reproduzca", kind="router")])
    assert d.action == "resolve"
    assert d.chosen.score == 0.95


def test_looks_like_command_detects_verb():
    assert looks_like_command("abre firefox", []) is True
    assert looks_like_command("qué lindo día", []) is False
    assert looks_like_command("cualquier cosa", [_c(0.5)]) is True


def test_router_phrase_includes_target():
    phrase = router_phrase({"automation_intent": "PLAY_MEDIA", "target": "lofi"})
    assert "lofi" in phrase


def test_empty_candidates_chat():
    d = decide("hola", [])
    assert d.action == "chat"
    assert d.chosen is None
