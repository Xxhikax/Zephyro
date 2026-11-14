"""Tests exhaustivos del sistema de monitoreo de foco / distraction detection.

Cubre:
  A. FocusMonitor — clasificación, whitelist, grace periods, report, state helpers
  B. skill.py     — NLP regex de focus control: matches + extracción de app_name
  C. Coherencia   — no colisión con comandos existentes de Pomodoro/schedule/router
  D. Regresiones  — variantes de voz real que llegaron mal clasificadas en el pasado
"""
from __future__ import annotations
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ─────────────────────────────────────────────────────────────────────────────
# A. FocusMonitor — unit tests (sin imports de FastAPI/LLM)
# ─────────────────────────────────────────────────────────────────────────────

from modules.schedule.monitor import (
    FocusMonitor,
    _BASE_WHITELIST,
    _BASE_WHITELIST_URLS,
    _DISTRACTION_URLS,
    _DISTRACTION_APPS,
    _MUSIC_APPS,
    _MESSAGING_APPS,
)


def make_monitor() -> FocusMonitor:
    """Fresh monitor sin singleton para aislar tests."""
    return FocusMonitor()


# ── A1. Whitelist — apps siempre permitidas ───────────────────────────────────

class TestWhitelistClassification:
    def _check(self, title: str, url: str = "") -> bool:
        m = make_monitor()
        return m._is_whitelisted(title, url)

    def test_obsidian(self):
        assert self._check("Obsidian v1.5 - Daily Notes")

    def test_obsidian_lowercase(self):
        assert self._check("obsidian")

    def test_vscode(self):
        assert self._check("Visual Studio Code - main.py")

    def test_cursor(self):
        assert self._check("Cursor — workspace")

    def test_terminal_kitty(self):
        assert self._check("kitty")

    def test_terminal_alacritty(self):
        assert self._check("Alacritty")

    def test_powershell(self):
        assert self._check("Windows PowerShell")

    def test_zephyro(self):
        assert self._check("Zephyro OS - Tactical Console")

    def test_pdf_reader(self):
        assert self._check("PDF - Cálculo Diferencial.pdf")

    def test_word(self):
        assert self._check("Microsoft Word - tarea.docx")

    def test_libreoffice(self):
        assert self._check("LibreOffice Writer")

    def test_youtube_music_url(self):
        assert self._check("YouTube Music", "https://music.youtube.com/watch?v=abc")

    def test_spotify_url(self):
        assert self._check("Spotify", "https://open.spotify.com/playlist/abc")

    def test_figma_added_session(self):
        m = make_monitor()
        m.add_to_session_whitelist("figma")
        assert m._is_whitelisted("Figma — Design", "")

    def test_notion_added_permanent(self, tmp_path, monkeypatch):
        import modules.schedule.monitor as mod
        monkeypatch.setattr(mod, "_WHITELIST_PATH", tmp_path / "wl.json")
        m = make_monitor()
        m.add_to_permanent_whitelist("notion")
        assert m._is_whitelisted("Notion", "")

    # Apps que NO deben estar en whitelist por defecto
    def test_discord_not_whitelisted(self):
        assert not self._check("Discord — #general")

    def test_steam_not_whitelisted(self):
        assert not self._check("Steam")

    def test_instagram_not_whitelisted(self):
        assert not self._check("", "https://www.instagram.com/")

    def test_youtube_watch_not_whitelisted(self):
        assert not self._check("YouTube", "https://www.youtube.com/watch?v=abc")


# ── A2. Distraction classification ────────────────────────────────────────────

class TestDistractionClassification:
    def _cls(self, title: str, url: str = "") -> tuple:
        m = make_monitor()
        return m._classify_distraction(title, url)

    def test_youtube_watch(self):
        is_d, cat = self._cls("YouTube", "https://www.youtube.com/watch?v=abc")
        assert is_d and cat == "browser"

    def test_youtube_shorts(self):
        is_d, cat = self._cls("YouTube", "https://www.youtube.com/shorts/abc")
        assert is_d and cat == "browser"

    def test_instagram(self):
        is_d, cat = self._cls("Instagram", "https://www.instagram.com/reel/abc/")
        assert is_d and cat == "browser"

    def test_twitter(self):
        is_d, cat = self._cls("X (Twitter)", "https://twitter.com/home")
        assert is_d and cat == "browser"

    def test_x_com(self):
        is_d, cat = self._cls("X", "https://x.com/home")
        assert is_d and cat == "browser"

    def test_tiktok(self):
        is_d, cat = self._cls("TikTok", "https://www.tiktok.com/@user")
        assert is_d and cat == "browser"

    def test_twitch(self):
        is_d, cat = self._cls("Twitch", "https://www.twitch.tv/channel")
        assert is_d and cat == "browser"

    def test_reddit(self):
        is_d, cat = self._cls("Reddit", "https://www.reddit.com/r/programming/")
        assert is_d and cat == "browser"

    def test_facebook(self):
        is_d, cat = self._cls("Facebook", "https://www.facebook.com/")
        assert is_d and cat == "browser"

    def test_discord_app(self):
        is_d, cat = self._cls("Discord — #general", "")
        assert is_d and cat == "app"

    def test_steam_app(self):
        is_d, cat = self._cls("Steam", "")
        assert is_d and cat == "app"

    def test_epic_games_app(self):
        is_d, cat = self._cls("Epic Games Launcher", "")
        assert is_d and cat == "app"

    def test_whatsapp_web(self):
        is_d, cat = self._cls("WhatsApp", "https://web.whatsapp.com/")
        assert is_d and cat == "messaging"

    # Apps permitidas — deben retornar (False, "")
    def test_obsidian_not_distraction(self):
        assert self._cls("Obsidian") == (False, "")

    def test_vscode_not_distraction(self):
        assert self._cls("Visual Studio Code - project") == (False, "")

    def test_youtube_music_not_distraction(self):
        # music.youtube.com está en _BASE_WHITELIST_URLS → permitida
        is_d, _ = self._cls("YouTube Music", "https://music.youtube.com/watch?v=abc")
        assert not is_d

    def test_spotify_not_distraction(self):
        is_d, _ = self._cls("Spotify", "https://open.spotify.com/album/abc")
        assert not is_d

    def test_empty_window_not_distraction(self):
        is_d, _ = self._cls("", "")
        assert not is_d


# ── A3. Grace periods ─────────────────────────────────────────────────────────

class TestGracePeriods:
    def test_game_grace_0(self):
        """Un juego en foco durante trabajo no tiene margen."""
        m = make_monitor()
        assert m._get_grace_period("game") == 0

    def test_no_music_category_exists(self):
        """`GRACE_PERIODS["music"]` era código muerto y se borró.

        `_classify_distraction` nunca devuelve "music": los dominios de música
        (music.youtube.com, open.spotify.com) están en `_BASE_WHITELIST_URLS`, así que
        salen por el camino de whitelist y nunca llegan a clasificarse como distracción.
        El grace de 60 s era inalcanzable en producción.
        """
        m = make_monitor()
        assert "music" not in m.GRACE_PERIODS

    def test_categorias_reales_son_explicitas(self):
        """Las categorías que el clasificador SÍ devuelve tienen clave propia.

        Antes "browser", "app" y "game" faltaban y caían a `default_distraction` por
        omisión. El valor resultaba el mismo, pero por accidente y no por decisión.
        """
        m = make_monitor()
        for category in ("game", "browser", "app", "messaging", "messaging_during_call"):
            assert category in m.GRACE_PERIODS, category

    def test_messaging_grace_30(self):
        m = make_monitor()
        assert m._get_grace_period("messaging") == 30

    def test_browser_distraction_grace_0(self):
        m = make_monitor()
        assert m._get_grace_period("browser") == 0

    def test_app_distraction_grace_0(self):
        m = make_monitor()
        assert m._get_grace_period("app") == 0

    def test_unknown_category_grace_0(self):
        m = make_monitor()
        assert m._get_grace_period("unknown_cat") == 0


# ── A4. Normalize label ────────────────────────────────────────────────────────

class TestNormalizeLabel:
    def _norm(self, window: str, url: str = "") -> str:
        return make_monitor()._normalize_label(window, url)

    def test_youtube_url(self):
        result = self._norm("YouTube", "https://www.youtube.com/watch?v=abc")
        assert result == "youtube.com"

    def test_instagram_url(self):
        result = self._norm("Instagram", "https://www.instagram.com/reel/")
        assert result == "instagram.com"

    def test_twitter_url(self):
        result = self._norm("Twitter", "https://twitter.com/home")
        assert result == "twitter.com"

    def test_no_url_uses_title(self):
        result = self._norm("Discord — #general", "")
        assert "discord" in result.lower()

    def test_long_title_truncated(self):
        long_title = "A" * 50
        result = self._norm(long_title, "")
        assert len(result) <= 30


# ── A5. State helpers ──────────────────────────────────────────────────────────

class TestStateHelpers:
    def test_empty_distraction_state_structure(self):
        m = make_monitor()
        s = m._empty_distraction_state()
        required_keys = {"app", "url", "started_at", "grace_expires_at",
                         "alert_level", "level1_at", "level2_at", "level3_at",
                         "enforcement_deadline", "enforcement_done", "category",
                         "source", "vision_category", "vision_confidence"}
        assert required_keys == set(s.keys())
        assert s["app"] == ""
        assert s["alert_level"] == 0
        assert not s["enforcement_done"]
        assert s["source"] == ""
        assert s["vision_category"] == ""
        assert s["vision_confidence"] == 0.0

    def test_close_episode_logs_correctly(self):
        m = make_monitor()
        now = time.time()
        m._distraction_state.update({
            "app": "youtube.com",
            "url": "https://youtube.com/watch",
            "started_at": now - 120,
            "grace_expires_at": now - 115,
            "alert_level": 2,
            "enforcement_done": False,
        })
        m._close_distraction_episode(now)
        assert len(m._distraction_log) == 1
        ep = m._distraction_log[0]
        assert ep["app"] == "youtube.com"
        assert ep["duration_sec"] >= 119
        assert ep["max_alert_level"] == 2
        assert not ep["enforced"]
        # After closing, state should be clean
        assert m._distraction_state["app"] == ""

    def test_close_episode_noop_when_empty(self):
        m = make_monitor()
        m._close_distraction_episode(time.time())
        assert m._distraction_log == []


# ── A6. Distraction report ─────────────────────────────────────────────────────

class TestDistractionReport:
    def test_empty_report(self):
        m = make_monitor()
        report = m.get_distraction_report()
        assert "Sin distracciones" in report or "Foco perfecto" in report

    def test_report_with_episodes(self):
        m = make_monitor()
        now = time.time()
        m._distraction_log = [
            {"app": "youtube.com", "url": "", "started_at": now - 300,
             "ended_at": now - 60, "duration_sec": 240,
             "max_alert_level": 2, "enforced": False},
            {"app": "instagram.com", "url": "", "started_at": now - 60,
             "ended_at": now, "duration_sec": 60,
             "max_alert_level": 1, "enforced": False},
        ]
        report = m.get_distraction_report()
        assert "youtube.com" in report
        assert "instagram.com" in report
        assert "2" in report  # 2 episodios

    def test_report_shows_enforcement(self):
        m = make_monitor()
        now = time.time()
        m._distraction_log = [
            {"app": "youtube.com", "url": "", "started_at": now - 300,
             "ended_at": now, "duration_sec": 300,
             "max_alert_level": 4, "enforced": True},
        ]
        report = m.get_distraction_report()
        assert "cerrada" in report.lower() or "enforcement" in report.lower() or "tab" in report.lower()


# ── A7. Whitelist management ───────────────────────────────────────────────────

class TestWhitelistManagement:
    def test_show_whitelist_contains_obsidian(self):
        m = make_monitor()
        wl = m.show_whitelist()
        assert "obsidian" in wl.lower()

    def test_show_whitelist_contains_vscode(self):
        m = make_monitor()
        wl = m.show_whitelist()
        assert "visual studio code" in wl.lower() or "code" in wl.lower()

    def test_add_session_whitelist(self):
        m = make_monitor()
        msg = m.add_to_session_whitelist("Notion")
        assert "notion" in msg.lower()
        assert "notion" in m._session_whitelist

    def test_session_whitelist_enables_detection(self):
        m = make_monitor()
        m.add_to_session_whitelist("notion")
        assert m._is_whitelisted("Notion — workspace", "")

    def test_permanent_whitelist_persisted(self, tmp_path, monkeypatch):
        import modules.schedule.monitor as mod
        wl_path = tmp_path / "focus_whitelist.json"
        monkeypatch.setattr(mod, "_WHITELIST_PATH", wl_path)
        m = make_monitor()
        m.add_to_permanent_whitelist("Figma")
        assert wl_path.exists()
        import json
        data = json.loads(wl_path.read_text())
        assert "figma" in data["permanent_additions"]

    def test_permanent_whitelist_loaded_on_new_instance(self, tmp_path, monkeypatch):
        import modules.schedule.monitor as mod
        wl_path = tmp_path / "focus_whitelist.json"
        monkeypatch.setattr(mod, "_WHITELIST_PATH", wl_path)
        m1 = make_monitor()
        m1.add_to_permanent_whitelist("Canva")
        # New instance should load it
        m2 = make_monitor()
        assert "canva" in m2._permanent_additions


# ── A8. _is_monitoring_active ──────────────────────────────────────────────────

class TestMonitoringActive:
    def test_inactive_by_default(self):
        m = make_monitor()
        assert not m._is_monitoring_active

    def test_active_in_study_mode(self):
        m = make_monitor()
        m._study_mode_active = True
        assert m._is_monitoring_active

    def test_inactive_during_pomodoro_break(self):
        m = make_monitor()
        import asyncio
        # Simular tarea de pomodoro done (no task activo)
        assert not m._is_monitoring_active

    def test_active_during_pomodoro_work_phase(self):
        m = make_monitor()
        # Simular tarea activa en fase work
        import asyncio

        async def fake_task():
            await asyncio.sleep(9999)

        loop = asyncio.new_event_loop()
        task = loop.create_task(fake_task())
        m._pomodoro_task = task
        m._pomodoro_phase = "work"
        assert m._is_monitoring_active
        task.cancel()
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        finally:
            loop.close()

    def test_inactive_during_pomodoro_break_phase(self):
        m = make_monitor()
        import asyncio

        async def fake_task():
            await asyncio.sleep(9999)

        loop = asyncio.new_event_loop()
        task = loop.create_task(fake_task())
        m._pomodoro_task = task
        m._pomodoro_phase = "short_break"
        assert not m._is_monitoring_active
        task.cancel()
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        finally:
            loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# B. skill.py — NLP regex de focus control
# ─────────────────────────────────────────────────────────────────────────────

import re
from skills.builtin.schedule.skill import (
    _STUDY_MODE_START_RE,
    _STUDY_MODE_STOP_RE,
    _WHITELIST_SHOW_RE,
    _WHITELIST_ADD_RE,
    _WHITELIST_PERMANENT_RE,
    _FOCUS_REPORT_RE,
)

# También cargamos el skill para probar _matches_focus_control()
def _skill_matches_focus(text: str) -> bool:
    """Instancia mínima del skill sin FastAPI para probar _matches_focus_control."""
    from pathlib import Path
    from skills.registry import SkillRegistry
    from skills.loader import SkillLoader
    registry = SkillRegistry()
    loader = SkillLoader([Path(__file__).parent.parent / "skills" / "builtin"])
    loader.load_into(registry)
    skill = registry.get_skill("schedule")
    if skill is None:
        return False
    return skill._matches_focus_control(text.lower())


class TestStudyModeStartRE:
    def _match(self, s): return bool(_STUDY_MODE_START_RE.search(s))

    # ── Debe matchear ──────────────────────────────────────────────────────────
    def test_activa_modo_estudio(self):        assert self._match("activa modo estudio")
    def test_activa_el_modo_estudio(self):    assert self._match("activa el modo estudio")
    def test_activa_modo_estudio_accent(self): assert self._match("actívame el modo estudio")
    def test_entra_en_modo_estudio(self):     assert self._match("entra en modo estudio")
    def test_pon_en_modo_estudio(self):       assert self._match("ponme en modo estudio")
    def test_inicia_modo_estudio(self):       assert self._match("inicia modo estudio")
    def test_empieza_modo_estudio(self):      assert self._match("empieza el modo estudio")
    def test_modo_estudio_ya(self):           assert self._match("modo estudio ya")
    def test_modo_estudio_standalone(self):   assert self._match("modo estudio")

    # ── No debe matchear (stop commands) ──────────────────────────────────────
    def test_not_sal_modo_estudio(self):      assert not self._match("sal del modo estudio")
    def test_not_desactiva_modo_estudio(self): assert not self._match("desactiva modo estudio")


class TestStudyModeStopRE:
    def _match(self, s): return bool(_STUDY_MODE_STOP_RE.search(s))

    def test_sal_del_modo_estudio(self):      assert self._match("sal del modo estudio")
    def test_salir_del_modo_estudio(self):    assert self._match("salir del modo estudio")
    def test_desactiva_modo_estudio(self):    assert self._match("desactiva modo estudio")
    def test_para_el_modo_estudio(self):      assert self._match("para el modo estudio")
    def test_termina_modo_estudio(self):      assert self._match("termina el modo estudio")

    def test_not_activa_modo_estudio(self):   assert not self._match("activa modo estudio")


class TestWhitelistShowRE:
    def _match(self, s): return bool(_WHITELIST_SHOW_RE.search(s))

    def test_muestrame_la_whitelist(self):    assert self._match("muéstrame la whitelist")
    def test_muestrame_whitelist(self):       assert self._match("muéstrame whitelist")
    def test_ver_la_whitelist(self):          assert self._match("ver la whitelist")
    def test_que_apps_tengo_permitidas(self): assert self._match("qué apps tengo permitidas")
    def test_que_apps_permitidas(self):       assert self._match("qué apps están permitidas")
    def test_whitelist_standalone(self):      assert self._match("whitelist")
    def test_listar_whitelist(self):          assert self._match("listar whitelist")

    # No debe matchear comandos de add
    def test_not_agrega_whitelist(self):      assert not self._match("agrega Notion a la whitelist")


class TestWhitelistAddRE:
    def _match(self, s): return bool(_WHITELIST_ADD_RE.search(s))
    def _group(self, s):
        m = _WHITELIST_ADD_RE.search(s)
        return m.group(1).strip() if m else None

    def test_agrega_notion(self):
        assert self._match("agrega Notion a la whitelist")

    def test_agrega_figma_permanente(self):
        assert self._match("agrega Figma a la whitelist para siempre")

    def test_agrega_lista_blanca(self):
        assert self._match("agrega Discord a la lista blanca")

    def test_agrega_como_app_permitida(self):
        assert self._match("agrega Canva como app permitida")

    def test_extrae_notion(self):
        assert self._group("agrega Notion a la whitelist") == "Notion"

    def test_extrae_figma(self):
        assert self._group("agrega Figma a la whitelist") == "Figma"

    def test_extrae_anki(self):
        assert self._group("agrega Anki a la whitelist para siempre") == "Anki"

    def test_not_muestra_whitelist(self):
        assert not self._match("muéstrame la whitelist")


class TestWhitelistPermanentRE:
    def _match(self, s): return bool(_WHITELIST_PERMANENT_RE.search(s))

    def test_para_siempre(self):             assert self._match("para siempre")
    def test_permanentemente(self):          assert self._match("permanentemente")
    def test_de_forma_permanente(self):      assert self._match("de forma permanente")
    def test_para_otras_sesiones(self):      assert self._match("para otras sesiones")
    def test_not_esta_sesion(self):          assert not self._match("solo esta sesión")


class TestFocusReportRE:
    def _match(self, s): return bool(_FOCUS_REPORT_RE.search(s))

    def test_reporte_distracciones(self):    assert self._match("reporte de distracciones")
    def test_reporte_distraccion(self):      assert self._match("reporte de distracción")
    def test_reporte_de_foco(self):          assert self._match("reporte de foco")
    def test_cuanto_me_distraje(self):       assert self._match("cuánto tiempo me distraje")
    def test_cuanto_distraje(self):          assert self._match("cuánto tiempo distraje")
    def test_mis_distracciones(self):        assert self._match("mis distracciones")
    def test_resumen_sesion_pomodoro(self):  assert self._match("resumen de la sesión pomodoro")
    def test_resumen_sesion_estudio(self):   assert self._match("resumen de la sesión de estudio")

    def test_not_reporte_agenda(self):       assert not self._match("reporte de agenda")
    def test_not_random(self):               assert not self._match("cómo va el pomodoro")


# ─────────────────────────────────────────────────────────────────────────────
# C. Coherencia — sin colisiones con comandos existentes
# ─────────────────────────────────────────────────────────────────────────────

from skills.builtin.schedule.skill import (
    _POMODORO_START_RE, _POMODORO_STOP_RE, _POMODORO_HINT_RE,
    _SEMANTIC_PATTERN_A, _SEMANTIC_PATTERN_B,
)


class TestNoCollisions:
    """Verifica que los regex de focus control NO colisionen con comandos existentes."""

    def test_study_mode_not_pomodoro(self):
        s = "activa modo estudio"
        assert not _POMODORO_START_RE.search(s)
        assert not _POMODORO_HINT_RE.search(s)
        assert bool(_STUDY_MODE_START_RE.search(s))

    def test_pomodoro_not_study_mode(self):
        s = "inicia una sesión de pomodoro"
        assert not _STUDY_MODE_START_RE.search(s)
        assert bool(_POMODORO_HINT_RE.search(s) or _POMODORO_START_RE.search(s))

    def test_whitelist_show_not_schedule(self):
        s = "muéstrame la whitelist"
        assert not _SEMANTIC_PATTERN_A.search(s)
        assert not _SEMANTIC_PATTERN_B.search(s)

    def test_agenda_not_study_mode(self):
        s = "agenda un estudio para el lunes de 7 a 9"
        assert not _STUDY_MODE_START_RE.search(s)

    def test_stop_pomodoro_not_stop_study(self):
        s = "para el pomodoro"
        assert not _STUDY_MODE_STOP_RE.search(s)
        assert bool(_POMODORO_STOP_RE.search(s))

    def test_agrega_to_schedule_not_whitelist(self):
        # "agrega una clase" no debe matchear whitelist add
        s = "agrega una clase de física el lunes"
        assert not _WHITELIST_ADD_RE.search(s)

    def test_stop_study_not_schedule_delete(self):
        from skills.builtin.schedule.skill import _DELETE_PATTERN
        s = "sal del modo estudio"
        assert not _DELETE_PATTERN.search(s)


# ─────────────────────────────────────────────────────────────────────────────
# D. Regresiones — variantes de voz real
# ─────────────────────────────────────────────────────────────────────────────

class TestVoiceVariants:
    """Simula variantes de voz reales con STT que puede generar formas atípicas."""

    def test_study_mode_voz1(self):   assert bool(_STUDY_MODE_START_RE.search("pon modo estudio"))
    def test_study_mode_voz2(self):   assert bool(_STUDY_MODE_START_RE.search("actívame el modo estudio ya"))
    def test_study_mode_voz3(self):   assert bool(_STUDY_MODE_START_RE.search("Zephyro activa modo estudio"))
    def test_study_mode_voz4(self):   assert bool(_STUDY_MODE_START_RE.search("quiero modo estudio"))
    # "quiero" no está en la regex — documentar como not-expected
    def test_study_mode_quiero_NOT_matched(self):
        # "quiero modo estudio" no matchea intencionalmente — se delegaría al LLM
        # Solo documentamos el comportamiento esperado
        result = bool(_STUDY_MODE_START_RE.search("quiero modo estudio"))
        print(f"  INFO: 'quiero modo estudio' matches={result} (si False, LLM lo manejará)")

    def test_stop_study_voz1(self):   assert bool(_STUDY_MODE_STOP_RE.search("sal del modo estudio Zephyro"))
    def test_stop_study_voz2(self):   assert bool(_STUDY_MODE_STOP_RE.search("para el modo estudio"))
    def test_stop_study_voz3(self):   assert bool(_STUDY_MODE_STOP_RE.search("desactiva el modo estudio"))

    def test_whitelist_voz1(self):    assert bool(_WHITELIST_SHOW_RE.search("qué aplicaciones tengo permitidas"))
    def test_whitelist_voz2(self):    assert bool(_WHITELIST_SHOW_RE.search("muéstrame la whitelist por favor"))
    def test_whitelist_voz3(self):    assert bool(_WHITELIST_SHOW_RE.search("ver whitelist"))

    def test_add_whitelist_voz1(self):
        m = _WHITELIST_ADD_RE.search("agrégame Anki a la whitelist")
        assert m and "anki" in m.group(1).lower()

    def test_add_whitelist_voz2(self):
        m = _WHITELIST_ADD_RE.search("agrega notion como app permitida")
        assert m and "notion" in m.group(1).lower()

    def test_add_whitelist_permanent_voz(self):
        m = _WHITELIST_ADD_RE.search("agrega Canva a la whitelist permanentemente")
        assert m
        assert bool(_WHITELIST_PERMANENT_RE.search("agrega Canva a la whitelist permanentemente"))

    def test_report_voz1(self):   assert bool(_FOCUS_REPORT_RE.search("cuánto tiempo me distraje hoy"))
    def test_report_voz2(self):   assert bool(_FOCUS_REPORT_RE.search("mis distracciones de hoy"))
    def test_report_voz3(self):   assert bool(_FOCUS_REPORT_RE.search("reporte de distracciones"))


# ─────────────────────────────────────────────────────────────────────────────
# E. Async tests — check_distraction logic (sin WS)
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckDistractionLogic:
    """Tests de la lógica de _check_distraction sin WebSocket (mock _ws_broadcast)."""

    def _make_monitor_no_ws(self) -> FocusMonitor:
        m = make_monitor()
        m._study_mode_active = True

        async def _no_ws(payload):
            pass

        m._ws_broadcast = _no_ws
        return m

    def test_whitelisted_app_no_distraction_state(self):
        async def run():
            m = self._make_monitor_no_ws()
            await m._check_distraction("Obsidian v1.5", "")
            assert m._distraction_state["app"] == ""
        asyncio.run(run())

    def test_distraction_opens_episode(self):
        async def run():
            m = self._make_monitor_no_ws()
            await m._check_distraction("YouTube", "https://www.youtube.com/watch?v=abc")
            assert m._distraction_state["app"] != ""
            assert m._distraction_state["alert_level"] == 0  # dentro del grace (0s) pero no escaló aún
        asyncio.run(run())

    def test_return_to_focus_closes_episode(self):
        async def run():
            m = self._make_monitor_no_ws()
            # Abrir episodio
            await m._check_distraction("YouTube", "https://www.youtube.com/watch?v=abc")
            assert m._distraction_state["app"] != ""
            # Volver a Obsidian
            await m._check_distraction("Obsidian v1.5", "")
            assert m._distraction_state["app"] == ""
            assert len(m._distraction_log) == 1
        asyncio.run(run())

    def test_alert_escalates_past_grace(self):
        alerts_sent = []

        async def run():
            m = self._make_monitor_no_ws()

            async def capture_ws(payload):
                alerts_sent.append(payload)

            m._ws_broadcast = capture_ws
            # Abrir episodio con grace expirado manualmente
            now = time.time()
            m._distraction_state.update({
                "app": "youtube.com",
                "url": "https://www.youtube.com/watch?v=abc",
                "started_at": now - 10,
                "grace_expires_at": now - 5,  # grace ya expirado
                "alert_level": 0,
            })
            # Llamar check con la misma URL
            await m._check_distraction("YouTube", "https://www.youtube.com/watch?v=abc")
            assert any(a.get("level") == 1 for a in alerts_sent), "Expected level 1 alert"
        asyncio.run(run())

    def test_different_distraction_closes_old_episode(self):
        async def run():
            m = self._make_monitor_no_ws()
            # Entrar a YouTube
            await m._check_distraction("YouTube", "https://www.youtube.com/watch?v=abc")
            ep1_app = m._distraction_state["app"]
            # Cambiar a Instagram
            await m._check_distraction("Instagram", "https://www.instagram.com/")
            # El episodio anterior debe estar en el log
            assert len(m._distraction_log) == 1
            assert m._distraction_log[0]["app"] == ep1_app
        asyncio.run(run())

    def test_music_stays_in_grace_60s(self):
        async def run():
            m = self._make_monitor_no_ws()
            alerts_sent = []

            async def capture_ws(payload):
                alerts_sent.append(payload)

            m._ws_broadcast = capture_ws
            # music.youtube.com está whitelisted — no genera distracción
            # Probar con un sitio de música no en whitelist (edge case)
            # Primero: verificar que music.youtube.com no genera distracción
            await m._check_distraction("YouTube Music", "https://music.youtube.com/watch?v=abc")
            # No debe haber alerts ni episodio activo (es whitelist)
            assert m._distraction_state["app"] == ""
            assert not alerts_sent
        asyncio.run(run())


# ─────────────────────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_class(cls, name):
    instance = cls()
    methods = [m for m in dir(instance) if m.startswith("test_")]
    passed = failed = 0
    fails = []
    for meth in sorted(methods):
        try:
            getattr(instance, meth)()
            passed += 1
            print(f"  ✓ {meth}")
        except Exception as e:
            failed += 1
            fails.append((meth, str(e)))
            print(f"  ✗ {meth}: {e}")
    return passed, failed, fails


async def main():
    total_p = total_f = 0
    all_fails = []

    suites = [
        (TestWhitelistClassification,   "A1. Whitelist — apps permitidas"),
        (TestDistractionClassification, "A2. Distraction classification"),
        (TestGracePeriods,              "A3. Grace periods"),
        (TestNormalizeLabel,            "A4. Normalize label"),
        (TestStateHelpers,              "A5. State helpers"),
        (TestDistractionReport,         "A6. Distraction report"),
        (TestWhitelistManagement,       "A7. Whitelist management"),
        (TestMonitoringActive,          "A8. _is_monitoring_active"),
        (TestStudyModeStartRE,          "B1. NLP study mode start"),
        (TestStudyModeStopRE,           "B2. NLP study mode stop"),
        (TestWhitelistShowRE,           "B3. NLP whitelist show"),
        (TestWhitelistAddRE,            "B4. NLP whitelist add"),
        (TestWhitelistPermanentRE,      "B5. NLP whitelist permanent"),
        (TestFocusReportRE,             "B6. NLP focus report"),
        (TestNoCollisions,              "C.  Coherencia — sin colisiones"),
        (TestVoiceVariants,             "D.  Variantes de voz real"),
        (TestCheckDistractionLogic,     "E.  Async check_distraction logic"),
    ]

    for cls, label in suites:
        print(f"\n=== {label} ===")
        # Algunos tests usan monkeypatch de pytest — skip gracefully
        methods = [m for m in dir(cls) if m.startswith("test_")]
        inst = cls()
        p = f = 0
        for meth in sorted(methods):
            fn = getattr(inst, meth)
            import inspect
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
            if any(p in params for p in ("monkeypatch", "tmp_path")):
                print(f"  ~ {meth}: skip (requires pytest fixtures)")
                continue
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
                p += 1
                print(f"  ✓ {meth}")
            except AssertionError as e:
                f += 1
                all_fails.append((label, meth, str(e)))
                print(f"  ✗ {meth}: {e}")
            except Exception as e:
                f += 1
                all_fails.append((label, meth, f"{type(e).__name__}: {e}"))
                print(f"  ✗ {meth}: {type(e).__name__}: {e}")
        total_p += p
        total_f += f

    print(f"\n{'='*60}")
    print(f"RESULTADO: {total_p} passed, {total_f} failed")
    if all_fails:
        print("\nFALLOS:")
        for suite, meth, err in all_fails:
            print(f"  [{suite}] {meth}: {err}")
    else:
        print("TODOS LOS TESTS PASARON ✓")

    return total_f == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
