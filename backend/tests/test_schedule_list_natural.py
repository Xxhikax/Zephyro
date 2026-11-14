# -*- coding: utf-8 -*-
"""
Listado de agenda en lenguaje natural ("mostrame la agenda de la semana").

Bug encontrado en pruebas en vivo: el verbo de visualización adelante
("mostrame la agenda...") rompía el matching por prefijo de schedule → el
comando se iba al chat LLM en vez de listar la agenda. Estos tests fijan el
_LIST_AGENDA_RE.
"""
from pathlib import Path

import pytest

from skills.registry import SkillRegistry
from skills.loader import SkillLoader
from skills.builtin.schedule.skill import _LIST_AGENDA_RE

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"


@pytest.fixture(scope="module")
def schedule():
    reg = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(reg)
    return reg.get_skill("schedule")


@pytest.mark.parametrize("cmd", [
    "mostrame la agenda de la semana",
    "mostrame la agenda",
    "muéstrame el calendario",
    "ver mi agenda",
    "ver la agenda de la semana",
    "dame los eventos de la semana",
    "mostrame la semana",
    "revisá la agenda",
    "listame los eventos",
    # Bug en vivo (2026-07-04): frases de consulta por voz que caían al chat.
    "dime si tengo eventos para el día de hoy.",
    "decime los eventos de la semana",
])
def test_list_commands_match(schedule, cmd):
    assert schedule.matches_command(cmd) is True, f"no matcheó: {cmd!r}"
    assert schedule.match_confidence(cmd) >= 0.8, f"confianza baja: {cmd!r}"


def test_tengo_eventos_query_matches(schedule):
    # "Tengo eventos para mañana." (STT sin signos de pregunta) es una CONSULTA
    # y debe entrar al skill (vía hint keyword "eventos" + temporal "mañana").
    assert schedule.matches_command("tengo eventos para mañana.") is True


@pytest.mark.parametrize("cmd", [
    "mostrame una foto",
    "ver el video de youtube",
    "dame un consejo",
    "mostrame cómo se hace",
])
def test_non_agenda_show_commands_dont_match(schedule, cmd):
    # "mostrame X" sin agenda/calendario/semana NO debe disparar schedule.
    assert schedule.matches_command(cmd) is False, f"falso positivo: {cmd!r}"


def test_list_regex_directly():
    assert _LIST_AGENDA_RE.search("mostrame la agenda de la semana")
    assert _LIST_AGENDA_RE.search("ver mi calendario")
    assert not _LIST_AGENDA_RE.search("mostrame una foto")


@pytest.mark.asyncio
async def test_list_command_routes_to_listing_not_create(schedule):
    # "mostrame la agenda de la semana" debe LISTAR (no crear un evento basura).
    # _handle_list devuelve un texto de listado; _handle_create crearía un evento.
    from core.request_context import RequestContext
    ctx = RequestContext(user_role="admin", user_name="Bruno",
                         authenticated=True, session_id="t")
    result = await schedule.handle("mostrame la agenda de la semana", ctx)
    assert result is not None
    # No debe haber creado un evento (los mensajes de creación dicen "agendado")
    assert "agendado" not in result.lower(), \
        f"se creó un evento en vez de listar: {result!r}"
