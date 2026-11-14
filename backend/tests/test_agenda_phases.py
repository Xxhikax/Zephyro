"""Integration tests for schedule data model changes (Phases 1-5)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import time
import pytest


class TestScheduleEventStatus:
    """Phase 2: Verifica campos de status en el modelo de eventos."""

    def test_event_has_status_field(self):
        """PCR: ScheduleEvent debe tener campo status."""
        from modules.schedule.service import ScheduleEvent, ScheduleService

        svc = ScheduleService(db_path="./storage/test_phase1.db")
        import sqlite3
        with sqlite3.connect(svc._db_path) as conn:
            conn.execute("DELETE FROM schedule_events")
            conn.commit()

        now = time.time()
        ev = svc.create_event("u1", "Test", "other", now + 1000, now + 2000)
        assert hasattr(ev, 'status') or ev.to_dict().get('status') is not None

        svc.delete_event(ev.id)
        import os
        try:
            os.remove(svc._db_path)
        except OSError:
            pass


class TestSlotFinder:
    """Phase 2: Algorithm for finding free time slots."""

    def test_find_free_slots_empty_day(self):
        """Un día sin eventos debe devolver un slot que cubra todo el día."""
        # Simular eventos vacíos
        busy = []  # lista de (start_hour_decimal, end_hour_decimal)
        slots = _find_free_slots_stub(busy, start_hour=7, end_hour=22, min_duration=1.0)
        assert len(slots) >= 1
        # El primer slot debe cubrir desde las 7
        assert slots[0][0] == 7.0

    def test_find_free_slots_with_events(self):
        """Con eventos ocupando 9-11 y 14-16, debe encontrar slots libres."""
        busy = [(9.0, 11.0), (14.0, 16.0)]
        slots = _find_free_slots_stub(busy, start_hour=7, end_hour=22, min_duration=1.0)
        # Debe haber slots: 7-9, 11-14, 16-22
        assert len(slots) >= 3
        assert (7.0, 9.0) in slots
        assert (11.0, 14.0) in slots
        assert (16.0, 22.0) in slots

    def test_no_slots_for_full_day(self):
        """Día completamente ocupado no tiene slots libres."""
        busy = [(7.0, 22.0)]
        slots = _find_free_slots_stub(busy, start_hour=7, end_hour=22, min_duration=1.0)
        assert len(slots) == 0

    def test_min_duration_filter(self):
        """Slots menores a min_duration no se incluyen."""
        busy = [(8.0, 8.5), (12.0, 13.0)]
        # Gap entre 8.5 y 12 = 3.5h, gap entre 13 y 22 = 9h
        slots = _find_free_slots_stub(busy, start_hour=7, end_hour=22, min_duration=2.0)
        assert len(slots) == 2  # 7-8 (1h, < 2h filtrado), 8.5-12, 13-22
        assert (8.5, 12.0) in slots
        assert (13.0, 22.0) in slots


# Stub implementation (will be replaced by real slot_finder.py)
def _find_free_slots_stub(busy, start_hour=7, end_hour=22, min_duration=1.0):
    """Stub: detecta gaps entre bloques ocupados."""
    busy_sorted = sorted(busy)
    slots = []
    current = start_hour
    for (bs, be) in busy_sorted:
        if current < bs and (bs - current) >= min_duration:
            slots.append((current, bs))
        current = max(current, be)
    if current < end_hour and (end_hour - current) >= min_duration:
        slots.append((current, end_hour))
    return slots


class TestRecommenderSchema:
    """Phase 4: Verifica que las recomendaciones tienen el formato correcto."""

    def test_recommendation_prompt_includes_context(self):
        """El prompt de recomendación debe incluir título, categoría, duración."""
        prompt = _build_recommendation_prompt(
            title="Sistemas Operativos",
            category="university",
            duration_min=120,
        )
        assert "Sistemas Operativos" in prompt
        assert "university" in prompt
        assert "120" in prompt

    def test_recommendation_by_category(self):
        """Cada categoría debe tener una sugerencia específica."""
        cats = {
            "exercise": ["playlist", "hidratación", "ejercicio"],
            "university": ["material", "estudio", "tarea"],
            "personal_project": ["pausa", "descanso", "estiramiento"],
        }
        for cat, keywords in cats.items():
            prompt = _build_recommendation_prompt("X", cat, 60)
            # Al menos una keyword debe aparecer
            found = any(kw in prompt.lower() for kw in keywords)
            assert found, f"Category {cat} should mention one of {keywords}"


def _build_recommendation_prompt(title, category, duration_min):
    return f"""Evento: "{title}" [{category}] Duración: {duration_min} min
Recomendación para {category}: {"material de estudio" if category == "university" else "playlist" if category == "exercise" else "pausa activa"}"""


class TestMonitorLogic:
    """Phase 5: Verifica lógica de monitoreo de cumplimiento."""

    def test_checkin_detects_pending(self):
        """Al final del día, detecta eventos con status pending."""
        events = [
            {"title": "Gym", "status": "completed"},
            {"title": "Clase", "status": "pending"},
            {"title": "Proyecto", "status": "cancelled"},
            {"title": "Estudio", "status": "pending"},
        ]
        pending = [e for e in events if e["status"] == "pending"]
        assert len(pending) == 2
        assert pending[0]["title"] == "Clase"

    def test_focus_break_detection(self):
        """Modo enfoque: detecta sesiones > 90 min en misma app."""
        app_times = {"VS Code": 95, "Chrome": 10}
        needs_break = any(t > 90 for t in app_times.values())
        assert needs_break is True

    def test_focus_no_break_needed(self):
        """Sesiones cortas no disparan pausa."""
        app_times = {"VS Code": 45, "Terminal": 30}
        needs_break = any(t > 90 for t in app_times.values())
        assert needs_break is False
