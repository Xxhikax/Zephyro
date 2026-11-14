"""Sitios distractores detectados por TÍTULO de ventana, no solo por URL.

Bug en vivo (2026-10-04): Bruno estuvo ~1 min en YouTube durante un Pomodoro y no
recibió ninguna alerta. Causa: `_DISTRACTION_URLS` (que contiene "youtube.com/watch")
se compara SOLO contra la URL, y la URL la trae Playwright, que no puede leer el Edge
real del usuario salvo que se haya lanzado con `--remote-debugging-port=9222`. Medido:
ese puerto estaba CERRADO, así que `current_url` llegaba vacío.

La prueba de que era una asimetría y no "la detección no funciona": en la MISMA sesión,
WhatsApp sí se detectó ("(27) WhatsApp y 9 páginas más : ~1 min"), porque "whatsapp"
vive en `_MESSAGING_APPS`, que se compara contra el TÍTULO.

Título real observado: "YouTube y 9 páginas más - Personal: Microsoft Edge".
"""
import pytest

from modules.schedule.monitor import (
    FocusMonitor,
    _BROWSER_TITLE_HINTS,
    _DISTRACTION_SITE_TITLES,
    _MUSIC_APPS,
)


@pytest.fixture
def m():
    return FocusMonitor()


# ── El bug original ───────────────────────────────────────────────────────────

class TestSitiosPorTitulo:
    def test_youtube_en_edge_sin_url_es_distraccion(self, m):
        """El caso exacto que falló en vivo."""
        title = "YouTube y 9 páginas más - Personal: Microsoft Edge"
        assert m._classify_distraction(title, "") == (True, "browser")

    @pytest.mark.parametrize("title", [
        "Instagram - Google Chrome",
        "reddit: the front page of the internet - Mozilla Firefox",
        "TikTok - Microsoft Edge",
        "Twitch - Google Chrome",
        "Netflix - Microsoft Edge",
        "(3) Facebook - Mozilla Firefox",
    ])
    def test_otros_sitios_sin_url(self, m, title):
        assert m._classify_distraction(title, "") == (True, "browser")

    def test_whatsapp_sigue_siendo_messaging(self, m):
        """La regresión a evitar: no reclasificar lo que ya funcionaba."""
        title = "(27) WhatsApp y 9 páginas más - Personal: Microsoft Edge"
        assert m._classify_distraction(title, "") == (True, "messaging")


# ── Lo que NO debe matchear ───────────────────────────────────────────────────

class TestSinFalsosPositivos:
    def test_archivo_del_editor_no_matchea(self, m):
        """Sin el gate de navegador, "youtube" matchearía un archivo abierto."""
        assert m._classify_distraction("youtube.py - backend - Cursor", "") == (False, "")
        assert m._classify_distraction(
            "youtube_dl.md - notas - Visual Studio Code", "") == (False, "")

    def test_musica_no_es_distraccion(self, m):
        """Por título se trata igual que por URL: whitelisteada.

        Escuchar música mientras trabajás no es distraerse. Sin este guard, agregar
        "youtube" a la tabla de títulos habría empezado a marcar YouTube Music.
        """
        assert m._classify_distraction("YouTube Music - Microsoft Edge", "") == (False, "")
        assert m._classify_distraction("Spotify Premium - Microsoft Edge", "") == (False, "")

    def test_sitios_de_trabajo_siguen_limpios(self, m):
        for title in (
            "Stack Overflow - cómo hacer X - Microsoft Edge",
            "GitHub - zephyro - Microsoft Edge",
            "monitor.py - Zephyro - Visual Studio Code",
            "Obsidian - .brain",
        ):
            assert m._classify_distraction(title, "") == (False, ""), title

    def test_whitelist_gana_sobre_el_titulo(self, m):
        """`agregá X a la whitelist` tiene que poder desactivar un falso positivo."""
        m._session_whitelist.add("youtube")
        assert m._classify_distraction(
            "YouTube y 9 páginas más - Personal: Microsoft Edge", "") == (False, "")


# ── Estabilidad de la etiqueta del episodio ───────────────────────────────────

class TestEtiquetaEstable:
    """Un sitio distractor tiene que dar la MISMA etiqueta aunque cambie el título.

    Bug medido en vivo (2026-10-04): `_normalize_label` devolvía `window[:30]` cuando no
    había URL, y `_check_distraction` compara esa etiqueta para decidir si sigue siendo
    la misma distracción. Cada video nuevo = etiqueta nueva = episodio nuevo = grace
    reseteado y `alert_level` de vuelta a 0. Bruno vio CUATRO alertas de nivel 1
    seguidas en vez de una escalada; zapeando videos nunca habría llegado al nivel 2.
    """

    TITULOS_REALES = [
        "(6) SUWIE quiere jugar con FARFADOX - YouTube - Microsoft Edge",
        "(6) farfadox y suwie - YouTube - Microsoft Edge",
        "(6) YouTube y 10 páginas más - Personal: Microsoft Edge",
        "(6) el ICEBERG DEFINITIVO de los juegos - YouTube - Microsoft Edge",
    ]

    def test_videos_distintos_son_un_solo_episodio(self, m):
        labels = {m._normalize_label(t, "") for t in self.TITULOS_REALES}
        assert labels == {"youtube"}, f"etiquetas inestables: {labels}"

    def test_sitios_distintos_siguen_separados(self, m):
        a = m._normalize_label("Instagram - Google Chrome", "")
        b = m._normalize_label("reddit - Mozilla Firefox", "")
        assert a != b and a == "instagram" and b == "reddit"

    def test_no_colapsa_lo_que_no_es_sitio_conocido(self, m):
        """Solo los sitios de la tabla se colapsan; el resto conserva su título."""
        assert m._normalize_label("monitor.py - Zephyro - Visual Studio Code", "") \
            not in _DISTRACTION_SITE_TITLES
        assert m._normalize_label("Discord", "") == "Discord"

    def test_la_url_sigue_teniendo_prioridad(self, m):
        """Con CDP abierto la URL es más precisa y manda."""
        assert m._normalize_label(
            "(6) algo - YouTube - Microsoft Edge",
            "https://www.youtube.com/watch?v=abc") == "youtube.com"


# ── Invariantes de las tablas ─────────────────────────────────────────────────

class TestTablas:
    def test_todo_en_minuscula(self):
        """La comparación es contra `title.lower()`: una entrada con mayúsculas
        sería inalcanzable."""
        for s in _DISTRACTION_SITE_TITLES | _BROWSER_TITLE_HINTS:
            assert s == s.lower(), s

    def test_los_sitios_de_url_tienen_contraparte_por_titulo(self):
        """Cada dominio distractor debería ser reconocible también sin URL.

        Si se agrega un sitio a `_DISTRACTION_URLS` y no acá, vuelve el bug original:
        invisible en cuanto el puerto CDP esté cerrado (o sea, casi siempre).
        """
        from modules.schedule.monitor import _DISTRACTION_URLS
        faltantes = []
        for url_pattern in _DISTRACTION_URLS:
            host = url_pattern.split("/")[0]          # "youtube.com/watch" → "youtube.com"
            name = host.rsplit(".", 1)[0]             # "youtube.com" → "youtube"
            if not any(name in s or s in name for s in _DISTRACTION_SITE_TITLES):
                faltantes.append(url_pattern)
        assert not faltantes, f"sin contraparte por título: {faltantes}"

    def test_musica_no_esta_en_la_tabla_de_distraccion(self):
        """Solapar las dos tablas haría el resultado dependiente del orden."""
        for music in _MUSIC_APPS:
            assert music not in _DISTRACTION_SITE_TITLES, music
