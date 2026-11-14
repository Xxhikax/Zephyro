"""`self.page` nunca puede apuntar a una pestaña del usuario.

En modo CDP, `self.context` ES el contexto real del usuario (el attach toma
`contexts[0]`), así que `context.pages` trae su Canvas, su mail y sus
formularios a medio llenar. El attach tiene cuidado — abre una pestaña nueva —
pero dos líneas rompían la invariante:

    _close_tab:                     self.page = pages[-2]
    _close_distraction_tabs_impl:   self.page = remaining[-1]

Las dos leen de `self.context.pages`. Después de cerrar nuestra pestaña,
`self.page` apuntaba a una del usuario, y desde ahí TODO `keyboard.press`,
`keyboard.type`, `goto`, `click` y `mouse.wheel` del controller caía en su
página. Ese es el mecanismo por el que un comando mal ruteado le tipeó una
`k` a la pestaña de Canvas de Bruno (ea22754).

⚠️ Estos tests NUNCA instancian BrowserController ni BrowserConfig: en Windows
`BrowserConfig` hace CDP attach al Edge REAL y lo mata si no tiene el puerto
de debugging (ver .claude/rules/tests-safety.md). Se usa `__new__` + stubs.
"""
import asyncio

import pytest

from modules.automation.browser_controller import BrowserController


class FakePage:
    """Stub de Page: solo lo que la lógica de propiedad consulta.

    `resists_close` simula una página con handler `beforeunload` — un
    formulario a medio llenar. Con `run_before_unload=True` el navegador corre
    el handler, Playwright descarta el diálogo, y la página NO se cierra.
    """

    def __init__(self, url="about:blank", resists_close=False):
        self.url = url
        self._closed = False
        self.closed_by_us = False
        self.resists_close = resists_close
        self.ran_before_unload = None

    def is_closed(self):
        return self._closed

    # keyword-only, igual que la API real:
    #   Page.close(self, *, run_before_unload=None, reason=None)
    # Verificado contra el Playwright del venv. Importa que el stub sea fiel:
    # si el nombre del kwarg estuviera mal, el `except Exception: pass` de
    # `_close_distraction_tabs_impl` se tragaría el TypeError y no cerraría
    # nada en silencio — un stub permisivo no lo detectaría.
    async def close(self, *, run_before_unload=False, reason=None):
        self.ran_before_unload = run_before_unload
        if self.resists_close and run_before_unload:
            return
        self._closed = True
        self.closed_by_us = True


class FakeContext:
    def __init__(self, pages):
        self.pages = list(pages)


def _code_only(src: str) -> str:
    """Quita líneas de comentario.

    Los tests de abajo inspeccionan el fuente, así que tienen que ignorar los
    comentarios — si no, un comentario que DOCUMENTA el patrón prohibido hace
    fallar el test que lo prohíbe.
    """
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )


def _controller(own_pages=(), page=None, pages=()):
    """BrowserController sin __init__: no abre ni toca ningún navegador."""
    c = BrowserController.__new__(BrowserController)
    c._own_pages = set(own_pages)
    c.page = page
    c.context = FakeContext(pages)
    return c


# ── La invariante, verificada sobre el código fuente ────────────────────
def test_self_page_is_only_ever_assigned_from_new_page_or_none():
    """Ninguna asignación a self.page puede venir de context.pages.

    Es la red que atrapa una regresión futura: si alguien vuelve a escribir
    `self.page = pages[-2]`, acá se cae aunque no haya un test de integración
    que lo ejercite.
    """
    import inspect
    import re

    src = _code_only(inspect.getsource(BrowserController))
    assignments = re.findall(r"self\.page\s*=\s*([^\n#]+)", src)
    assert assignments, "no encontre ninguna asignacion: cambio el nombre?"
    for rhs in assignments:
        rhs = rhs.strip()
        ok = rhs == "None" or "new_page()" in rhs
        assert ok, (
            f"self.page = {rhs!r} — solo se permite None o context.new_page(). "
            f"Adoptar una pestaña de context.pages la apunta a la del usuario."
        )


# ── Registro y consulta de propiedad ────────────────────────────────────
def test_own_page_is_recognized():
    p = FakePage()
    c = _controller(page=p)
    c._register_own_page(p)
    assert c._owns_current_page() is True


def test_foreign_page_is_not_recognized():
    ours, theirs = FakePage(), FakePage("https://canvas.edu/grades")
    c = _controller(own_pages={ours}, page=theirs)
    assert c._owns_current_page() is False


def test_foreign_page_is_released_not_used():
    theirs = FakePage("https://canvas.edu/grades")
    c = _controller(own_pages=set(), page=theirs)
    c._release_page_if_foreign()
    assert c.page is None, "una pagina ajena tiene que soltarse"
    assert not theirs.closed_by_us, "y NO cerrarse: es del usuario"


def test_own_page_survives_the_release_check():
    ours = FakePage()
    c = _controller(own_pages={ours}, page=ours)
    c._release_page_if_foreign()
    assert c.page is ours


def test_register_prunes_closed_pages():
    old = FakePage()
    old._closed = True
    fresh = FakePage()
    c = _controller(own_pages={old})
    c._register_own_page(fresh)
    assert old not in c._own_pages, "las cerradas se podan"
    assert fresh in c._own_pages


# ── close_tab ───────────────────────────────────────────────────────────
def test_close_tab_refuses_to_close_a_foreign_page():
    """"cerra la pestaña" jamas debe llevarse una pestaña del usuario."""
    theirs = FakePage("https://canvas.edu/grades")
    c = _controller(own_pages=set(), page=theirs, pages=[theirs, FakePage()])
    c._state = None  # el guard de BrowserState.CLOSED no aplica
    c._ensure_browser = lambda: asyncio.sleep(0)

    result = asyncio.run(BrowserController._close_tab(c))

    assert not theirs.closed_by_us, "cerro una pestaña del usuario"
    assert c.page is None
    assert "no tengo" in result.lower()


def test_close_tab_closes_our_page_and_does_not_adopt_another():
    ours = FakePage()
    theirs = FakePage("https://canvas.edu/grades")
    c = _controller(own_pages={ours}, page=ours, pages=[theirs, ours])
    c._state = None
    c._ensure_browser = lambda: asyncio.sleep(0)

    asyncio.run(BrowserController._close_tab(c))

    assert ours.closed_by_us, "no cerro la nuestra"
    assert c.page is None, (
        "adopto otra pestaña; antes hacia self.page = pages[-2], que en modo "
        "CDP es una del usuario"
    )
    assert not theirs.closed_by_us
    assert ours not in c._own_pages


# ── close_distraction_tabs ──────────────────────────────────────────────
def test_closing_distractions_releases_our_page_without_adopting():
    ours = FakePage("https://youtube.com/watch?v=x")
    theirs = FakePage("https://canvas.edu/grades")
    c = _controller(own_pages={ours}, page=ours, pages=[theirs, ours])

    asyncio.run(BrowserController._close_distraction_tabs_impl(c, "youtube.com"))

    assert ours.closed_by_us
    assert c.page is None, "adopto remaining[-1], que es la pestaña del usuario"
    assert not theirs.closed_by_us, "canvas.edu no matchea youtube.com"


# ── Enforcement autonomo: respetar el trabajo sin guardar ───────────────
# Es la UNICA accion destructiva que Zephyro dispara sin que el usuario diga
# nada: la lanza el detector de distracciones por temporizador.
def test_distraction_close_runs_before_unload_handlers():
    """Sin el flag, `page.close()` OMITE `beforeunload`.

    O sea que se llevaba el formulario a medio llenar sin el dialogo
    "¿seguro que queres salir?" que el usuario habria visto al cerrar con la X.
    """
    p = FakePage("https://reddit.com/r/x")
    c = _controller(own_pages={p}, page=p, pages=[p])

    asyncio.run(BrowserController._close_distraction_tabs_impl(c, "reddit.com"))

    assert p.ran_before_unload is True, (
        "close() sin run_before_unload se lleva el trabajo sin guardar"
    )


def test_a_page_with_unsaved_work_is_kept_and_reported():
    form = FakePage("https://reddit.com/submit", resists_close=True)
    plain = FakePage("https://reddit.com/r/all")
    c = _controller(own_pages={form, plain}, page=plain, pages=[form, plain])

    result = asyncio.run(
        BrowserController._close_distraction_tabs_impl(c, "reddit.com")
    )

    assert not form.closed_by_us, "cerro una pagina con cambios sin guardar"
    assert plain.closed_by_us
    assert "sin guardar" in result, (
        f"no avisa que dejo pestañas abiertas: {result!r}"
    )


def test_a_page_that_resisted_is_not_counted_as_closed():
    """`close(run_before_unload=True)` no espera: hay que verificar el estado."""
    form = FakePage("https://reddit.com/submit", resists_close=True)
    c = _controller(own_pages={form}, page=form, pages=[form])

    result = asyncio.run(
        BrowserController._close_distraction_tabs_impl(c, "reddit.com")
    )

    assert result.startswith("0 "), f"conto como cerrada una que sigue viva: {result!r}"
    assert c.page is form, "solto una pagina que no se cerro"


# ── El selector que clickeaba cualquier boton ───────────────────────────
def test_cookie_selectors_all_filter_by_text_or_label():
    """`#content ytd-button-renderer` no filtraba por texto.

    Clickeaba CUALQUIER boton dentro de #content, y `_handle_cookie_popups`
    corre como efecto colateral de cualquier comando de musica. El texto que
    busca ("Aceptar"/"Accept"/"Allow") es justamente lo que
    `action_risk_rules._IRREVERSIBLE_CLICK_RE` declara HIGH.
    """
    import inspect

    src = _code_only(inspect.getsource(BrowserController._handle_cookie_popups))
    assert "#content ytd-button-renderer" not in src, (
        "selector sin filtro de texto: clickea cualquier boton de #content"
    )
