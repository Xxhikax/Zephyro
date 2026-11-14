# -*- coding: utf-8 -*-
"""Tests de la skill `expenses` (Fase 0 del roadmap de skills).

Cubre tres cosas, en orden de importancia:

1. **Desambiguación**: el dispatch del registry es primer-match-gana en orden
   alfabético de directorio, y `expenses` corre ANTES de `fitness`, `nutrition`
   y `schedule`. Si el matcher se vuelve laxo, les roba inputs. Estos tests son
   el guard de esa regresión (mismo rol que
   test_canvas_schedule_disambiguation.py).
2. **Parsing de montos**: "1,500" debe ser 1500, no 1.5.
3. **CRUD + presupuestos** sobre una DB temporal.
"""
from pathlib import Path

import pytest

from core.request_context import RequestContext
from modules.expenses.service import (
    ExpenseService,
    TOTAL_BUDGET_KEY,
    format_amount,
    infer_category,
    normalize_category,
)
from skills.builtin.expenses.skill import (
    ExpensesSkill,
    _extract_amount,
    _extract_description,
    _parse_money,
)
from skills.loader import SkillLoader
from skills.registry import SkillRegistry

_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "builtin"


@pytest.fixture(scope="module")
def reg():
    r = SkillRegistry()
    SkillLoader([_BUILTIN]).load_into(r)
    return r


@pytest.fixture
def svc(tmp_path):
    return ExpenseService(db_path=str(tmp_path / "expenses.db"))


@pytest.fixture
def skill(reg, svc):
    sk = reg.get_skill("expenses")
    assert sk is not None, "la skill expenses no se cargó"
    sk._service = svc  # DB temporal, no la real de storage/
    return sk


@pytest.fixture
def ctx():
    return RequestContext(
        user_role="admin", user_name="test", authenticated=True, session_id="s1"
    )


# ── Carga ────────────────────────────────────────────────────────────────────

def test_skill_loads_without_env(reg):
    """Sin requires.env: se registra siempre (el bug de canvas no se repite)."""
    sk = reg.get_skill("expenses")
    assert sk is not None
    assert sk.metadata.requires.get("env") is None


def test_dispatch_order_puts_expenses_first():
    """El registry despacha en el orden en que el loader registra, que es
    `sorted(skill_dir.iterdir())`. Se verifica sobre los directorios y no sobre
    el registry cargado porque nutrition/fitness necesitan Postgres y no siempre
    se registran en CI. Si esto se rompe, el contrato de desambiguación
    documentado en el SKILL.md deja de valer."""
    dirs = [d.name for d in sorted(_BUILTIN.iterdir()) if d.is_dir()]
    for later in ("fitness", "nutrition", "schedule"):
        assert dirs.index("expenses") < dirs.index(later)


# ── Matching: lo que SÍ es un gasto ──────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "/gasto 25 almuerzo",
    "/gasto almuerzo 25",
    "/gastos",
    "/gastos hoy",
    "/presupuesto",
    "/presupuesto 1500",
    "/presupuesto comida 400",
    "gasté 25 en el almuerzo",
    "gaste 40 de taxi",
    "pagué 120 de internet",
    "compré una laptop de 3500",
    "cuánto gasté este mes",
    "cuánto gasté hoy",
    "mis gastos de la semana",
    "en qué gasté este mes",
    "resumen de gastos",
    "borrar último gasto",
    "borrar gasto 12",
])
def test_matches_expense_inputs(skill, text):
    assert skill.matches_command(text) is True


# ── Matching: lo que NO es un gasto (colisiones reales) ──────────────────────

@pytest.mark.parametrize("text", [
    # nutrition
    "tomé un café",
    "acabo de comer pollo con arroz",
    "hice dos huevos revueltos",
    "/macros",
    # fitness / schedule
    "/peso 96.5",
    "/pasos 9500",
    "agendá el examen de cálculo mañana a las 10",
    "qué tengo en la semana",
    # chat: "gastar" sin monto no es un gasto
    "gasté mucho tiempo en eso",
    "pagué bastante por eso el año pasado",
    # el gasto de tokens del propio Zephyro no es un gasto personal
    "cuánto gasté en tokens este mes",
    "cuánto gasté en la api de anthropic",
])
def test_does_not_steal_other_domains(skill, text):
    assert skill.matches_command(text) is False


def test_nutrition_still_owns_its_inputs(reg):
    """Contraparte del test anterior: nutrition sigue matcheando lo suyo."""
    nut = reg.get_skill("nutrition")
    if nut is None:
        pytest.skip("nutrition no está cargada en este entorno")
    assert nut.matches_command("tomé un café") is True


# ── Parsing de montos ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("1500", 1500.0),
    ("1,500", 1500.0),     # separador de miles, NO 1.5
    ("1.500", 1500.0),
    ("25.50", 25.5),
    ("25,5", 25.5),
    ("1,234.56", 1234.56),
    ("1.234,56", 1234.56),
    ("7", 7.0),
])
def test_parse_money(raw, expected):
    assert _parse_money(raw) == pytest.approx(expected)


@pytest.mark.parametrize("text,expected", [
    ("gasté 25 en el almuerzo", 25.0),
    ("gasté 1,500 en la laptop", 1500.0),
    ("pagué 25.50 de taxi", 25.5),
    ("hace 2 horas gasté 40 en taxi", 40.0),  # ignora "2 horas"
])
def test_extract_amount(text, expected):
    assert _extract_amount(text) == pytest.approx(expected)


@pytest.mark.parametrize("text,amount,expected", [
    ("gasté 25 en el almuerzo", 25.0, "almuerzo"),
    ("pagué 40 de taxi", 40.0, "taxi"),
    ("gasté 1,500 en la laptop", 1500.0, "laptop"),
    ("/gasto 25 almuerzo", 25.0, "almuerzo"),
    ("gasté 30 soles en el mercado", 30.0, "mercado"),
])
def test_extract_description(text, amount, expected):
    assert _extract_description(text, amount) == expected


# ── Categorías ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("desc,category", [
    ("almuerzo", "food"),
    ("taxi al centro", "transport"),
    ("pensión de la universidad", "university"),
    ("netflix", "subscriptions"),
    ("farmacia", "health"),
    ("cine con amigos", "entertainment"),
    ("zapatillas", "shopping"),
    ("recarga claro", "services"),
    ("ahorro del mes", "savings"),
    ("cosa rara indescriptible", "other"),
])
def test_infer_category(desc, category):
    assert infer_category(desc) == category


def test_normalize_category_accepts_spanish_aliases():
    assert normalize_category("comida") == "food"
    assert normalize_category("food") == "food"
    assert normalize_category("inexistente") == "other"


# ── Service: CRUD y agregados ────────────────────────────────────────────────

def test_add_and_list(svc):
    e = svc.add_expense("admin", 25, "almuerzo")
    assert e.id > 0
    assert e.category == "food"
    assert svc.list_expenses("admin")[0].id == e.id


def test_add_rejects_non_positive(svc):
    with pytest.raises(ValueError):
        svc.add_expense("admin", 0, "nada")


def test_expenses_are_scoped_by_user(svc):
    svc.add_expense("admin", 10, "café")
    assert svc.list_expenses("otro") == []


def test_summary_totals_and_breakdown(svc):
    svc.add_expense("admin", 25, "almuerzo")
    svc.add_expense("admin", 15, "taxi")
    data = svc.summary("admin", "month")
    assert data["total"] == pytest.approx(40.0)
    assert data["count"] == 2
    assert dict((c, t) for c, t, _ in data["by_category"])["food"] == pytest.approx(25.0)


def test_currencies_are_never_summed_together(svc):
    svc.add_expense("admin", 100, "mercado", currency="PEN")
    svc.add_expense("admin", 20, "hosting", currency="USD")
    data = svc.summary("admin", "month", "PEN")
    assert data["total"] == pytest.approx(100.0)
    assert data["totals_by_currency"]["USD"] == pytest.approx(20.0)


def test_delete_and_delete_last(svc):
    a = svc.add_expense("admin", 10, "café")
    b = svc.add_expense("admin", 20, "taxi")
    assert svc.delete_expense("admin", a.id) is True
    assert svc.delete_expense("admin", a.id) is False
    assert svc.delete_last("admin").id == b.id
    assert svc.list_expenses("admin") == []


def test_budget_status_levels(svc):
    svc.set_budget("admin", 100)
    assert svc.budget_status("admin")[0]["level"] == "ok"
    svc.add_expense("admin", 85, "mercado")
    assert svc.budget_status("admin")[0]["level"] == "warn"
    svc.add_expense("admin", 30, "mercado")
    st = svc.budget_status("admin")[0]
    assert st["level"] == "over"
    assert st["remaining"] == pytest.approx(-15.0)


def test_budget_per_category_is_independent(svc):
    svc.set_budget("admin", 50, "comida")
    svc.add_expense("admin", 40, "taxi")  # transport, no toca el de comida
    st = next(s for s in svc.budget_status("admin") if s["category"] == "food")
    assert st["spent"] == pytest.approx(0.0)


def test_format_amount():
    assert format_amount(1500, "PEN") == "S/ 1,500.00"
    assert format_amount(20.5, "USD") == "$ 20.50"


# ── handle(): end-to-end de la skill ─────────────────────────────────────────

async def test_handle_log_confirms_and_totals(skill, ctx):
    out = await skill.handle("gasté 25 en el almuerzo", ctx)
    assert "S/ 25.00" in out
    assert "almuerzo" in out
    assert "comida" in out
    assert "Total del mes" in out


async def test_handle_log_without_amount_asks_for_it(skill, ctx):
    out = await skill.handle("/gasto", ctx)
    assert "monto" in out.lower()


async def test_handle_summary_empty(skill, ctx):
    out = await skill.handle("/gastos", ctx)
    assert "No tenés gastos registrados" in out


async def test_handle_summary_with_data(skill, ctx):
    await skill.handle("gasté 25 en el almuerzo", ctx)
    await skill.handle("pagué 15 de taxi", ctx)
    out = await skill.handle("/gastos", ctx)
    assert "S/ 40.00" in out
    assert "comida" in out and "transporte" in out


async def test_handle_budget_set_then_status(skill, ctx):
    out = await skill.handle("/presupuesto 100", ctx)
    assert "S/ 100.00" in out
    out = await skill.handle("/presupuesto", ctx)
    assert "total" in out and "S/ 100.00" in out


async def test_handle_log_warns_when_budget_exceeded(skill, ctx):
    await skill.handle("/presupuesto 50", ctx)
    out = await skill.handle("gasté 60 en el mercado", ctx)
    assert "presupuesto" in out.lower()


async def test_handle_delete_last(skill, ctx):
    await skill.handle("gasté 25 en el almuerzo", ctx)
    out = await skill.handle("borrar último gasto", ctx)
    assert "S/ 25.00" in out
    assert skill._service.list_expenses("admin") == []


async def test_handle_returns_none_for_foreign_input(skill, ctx):
    """Si no es nuestro dominio, devolvemos None y el input sigue su camino."""
    assert await skill.handle("tomé un café", ctx) is None
