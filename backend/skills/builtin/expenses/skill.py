"""ExpensesSkill — tracking manual de gastos con presupuestos mensuales.

Sigue el patrón de backend/skills/builtin/nutrition/skill.py: regex de matching a
nivel módulo (testeables sin instanciar la skill) + servicio inyectado perezoso.

REGLA DE MATCHING (ver sección "Desambiguación" del SKILL.md): el dispatch del
registry es primer-match-gana en orden alfabético de directorio, y `expenses`
corre antes que `fitness`, `nutrition` y `schedule`. Por eso registrar un gasto
exige verbo de gasto + monto numérico: sin monto no matcheamos y el input sigue
su camino normal.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill

log = logging.getLogger("zephyro.skill.expenses")

# ── Regex de matching ────────────────────────────────────────────────────────

# Verbos de gasto. "compré" incluido: sin monto no matchea, así que no le roba
# inputs al router de e-commerce ("compra una laptop en Amazon" no tiene monto).
_SPEND_VERB = r"(?:gast[eé]|pagu[eé]|compr[eé]|invert[ií]|me\s+cost[oó]|cost[oó]|desembols[eé])"

# Acepta separador de miles: "1500", "1,500", "1.500", "25.50", "25,5".
_MONEY_NUM = r"\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?"

# Registro: verbo de gasto + monto en el mismo input.
_LOG_RE = re.compile(rf"\b{_SPEND_VERB}\b(?!\s+(?:mucho|poco|bastante|demasiado)\b).*?{_MONEY_NUM}", re.IGNORECASE | re.DOTALL)

# /gasto 25 almuerzo | /gasto almuerzo 25 | /gasto (sin monto → pide el monto).
# El (?!s) deja "/gastos" para el resumen.
_SLASH_LOG_RE = re.compile(r"^/gasto(?!s)\b\s*(.*)$", re.IGNORECASE | re.DOTALL)

# Consultas de resumen. Van ANTES del registro en handle(): "cuánto gasté" es
# pregunta, "gasté 50" es registro.
_SUMMARY_RE = re.compile(
    r"^/gastos\b|mis\s+gastos|cu[aá]nto\s+(?:gast[eé]|llevo\s+gastado)|"
    r"resumen\s+de\s+gastos|gastos\s+de\s+(?:hoy|la\s+semana|este\s+mes|ayer)|"
    r"gastos\s+del\s+mes|en\s+qu[eé]\s+gast[eé]|balance\s+de\s+gastos",
    re.IGNORECASE,
)

_BREAKDOWN_RE = re.compile(r"en\s+qu[eé]\s+gast[eé]|por\s+categor[ií]a|desglose", re.IGNORECASE)

# /presupuesto            → estado
# /presupuesto 1500       → total mensual
# /presupuesto comida 400 → por categoría
_BUDGET_RE = re.compile(
    rf"^/presupuesto\b\s*(?:([a-záéíóúñ]+)\s+)?({_MONEY_NUM})?\s*$|"
    rf"(?:pon|fij[aá]|estable(?:c[eé]|ce))\s+(?:un\s+)?presupuesto\s+"
    rf"(?:de\s+)?({_MONEY_NUM})|mi\s+presupuesto|c[oó]mo\s+voy\s+(?:con|de)\s+presupuesto",
    re.IGNORECASE,
)

_DELETE_RE = re.compile(
    r"(?:borr[aá]|elimin[aá]|deshac[eé]|anul[aá])\s+(?:el\s+)?(?:[uú]ltimo\s+gasto|gasto\s+(\d+))|"
    r"borrar\s+(?:[uú]ltimo\s+gasto|gasto\s+(\d+))",
    re.IGNORECASE,
)

# Guard: gasto de tokens/API del propio Zephyro NO es un gasto personal.
_API_SPEND_GUARD = re.compile(
    r"\b(?:token|tokens|api|llm|modelo|openai|anthropic|deepseek|gemini|claude|"
    r"ollama|provider|proveedor)\b",
    re.IGNORECASE,
)

# Periodo pedido en la consulta.
_TODAY_RE = re.compile(r"\bhoy\b|^/gastos?\s+hoy", re.IGNORECASE)
_WEEK_RE = re.compile(r"\bsemana\b|semanal", re.IGNORECASE)

# Moneda explícita.
_USD_RE = re.compile(r"\$|\bd[oó]lares?\b|\busd\b", re.IGNORECASE)
_EUR_RE = re.compile(r"€|\beuros?\b", re.IGNORECASE)

# Fecha relativa simple.
_YESTERDAY_RE = re.compile(r"\bayer\b", re.IGNORECASE)
_DAY_BEFORE_RE = re.compile(r"\banteayer\b|\bantes\s+de\s+ayer\b", re.IGNORECASE)

# Palabras a limpiar del arranque de la descripción.
_DESC_STOPWORDS = re.compile(
    r"^(?:en|de|del|la|el|los|las|un|una|por|para|al|a|mi|soles?|sol|d[oó]lares?|"
    r"euros?|pen|usd|eur|s/)\s+",
    re.IGNORECASE,
)


def _detect_currency(text: str) -> str:
    if _USD_RE.search(text):
        return "USD"
    if _EUR_RE.search(text):
        return "EUR"
    return "PEN"


def _parse_money(raw: str) -> Optional[float]:
    """Convierte un monto escrito a float, resolviendo separador de miles.

    Regla: un separador seguido de EXACTAMENTE 3 dígitos es de miles
    ("1,500" y "1.500" → 1500); con 1 o 2 dígitos es decimal ("25,5" → 25.5,
    "25.50" → 25.5). Sin esto, "gasté 1,500 en la laptop" se guardaba como 1.5.
    """
    s = raw.strip()
    if "." in s and "," in s:
        # El último separador es el decimal; el otro es de miles.
        dec_sep = "." if s.rfind(".") > s.rfind(",") else ","
        thou_sep = "," if dec_sep == "." else "."
        s = s.replace(thou_sep, "").replace(dec_sep, ".")
    else:
        for sep in (",", "."):
            if sep in s:
                s = s.replace(sep, "") if len(s.split(sep)[-1]) == 3 else s.replace(sep, ".")
                break
    try:
        return float(s)
    except ValueError:
        return None


def _extract_amount(text: str) -> Optional[float]:
    """Primer monto plausible del input.

    Ignora números pegados a unidades de tiempo ("hace 2 horas"), que no son
    montos.
    """
    for m in re.finditer(rf"({_MONEY_NUM})", text):
        tail = text[m.end():m.end() + 14].lower()
        if re.match(r"\s*(?:h(?:oras?)?|min(?:utos?)?|d[ií]as?|semanas?|meses?|a[nñ]os?)\b", tail):
            continue
        val = _parse_money(m.group(1))
        if val is not None and val > 0:
            return val
    return None


def _extract_description(text: str, amount: float) -> str:
    """Descripción del gasto: el texto sin el verbo, el monto ni la moneda."""
    cleaned = re.sub(_SPEND_VERB, " ", text, flags=re.IGNORECASE)
    # Quitar el token del monto tal como fue escrito ("1,500", "25.50"), no una
    # reconstrucción del float: f"{amount:g}" pasa a notación científica con
    # montos grandes y nunca matchearía.
    for m in re.finditer(rf"{_MONEY_NUM}", cleaned):
        if _parse_money(m.group(0)) == amount:
            cleaned = cleaned[:m.start()] + " " + cleaned[m.end():]
            break
    cleaned = re.sub(
        r"\bs/\.?|\$|€|\bsoles?\b|\bd[oó]lares?\b|\beuros?\b|\busd\b|\bpen\b|\beur\b",
        " ", cleaned, flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^/gasto\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:hoy|ayer|anteayer)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;:-")
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _DESC_STOPWORDS.sub("", cleaned).strip()
    return cleaned


def _progress_bar(current: float, target: float, width: int = 10) -> str:
    """Barra ASCII, igual que nutrition."""
    if target <= 0:
        return "-" * width
    pct = min(1.0, current / target)
    filled = int(pct * width)
    return "#" * filled + "." * (width - filled)


class ExpensesSkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._service = None

    def _get_service(self):
        if self._service is None:
            from modules.expenses.service import get_expense_service
            self._service = get_expense_service()
        return self._service

    # ── Matching ─────────────────────────────────────────────────────────

    def matches_command(self, user_input: str, ctx=None) -> bool:
        lower = user_input.lower().strip()

        # El gasto de tokens/API del propio Zephyro no es un gasto personal.
        if _API_SPEND_GUARD.search(lower):
            return False

        if _SLASH_LOG_RE.match(lower):
            return True
        if _SUMMARY_RE.search(lower):
            return True
        if _BUDGET_RE.search(lower):
            return True
        if _DELETE_RE.search(lower):
            return True
        # Registro: verbo de gasto + monto. Sin monto NO matcheamos, para no
        # robarle inputs a nutrition ("tomé un café") ni al chat ("gasté mucho
        # tiempo en eso").
        if _LOG_RE.search(lower) and _extract_amount(lower) is not None:
            return True
        return False

    def match_confidence(self, user_input: str) -> float:
        lower = user_input.lower().strip()
        if _API_SPEND_GUARD.search(lower):
            return 0.0
        if lower.startswith("/gasto") or lower.startswith("/presupuesto"):
            return 1.0
        if not self.matches_command(user_input):
            return 0.0
        # Verbo + monto explícito es señal fuerte; una consulta suelta, media.
        return 0.9 if _LOG_RE.search(lower) else 0.7

    # ── Handle ───────────────────────────────────────────────────────────

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        lower = user_input.lower().strip()
        if _API_SPEND_GUARD.search(lower):
            return None

        user_id = getattr(ctx, "user_role", None) or "admin"
        svc = self._get_service()

        try:
            if _DELETE_RE.search(lower):
                return self._handle_delete(lower, user_id, svc)
            if _BUDGET_RE.search(lower):
                return self._handle_budget(lower, svc, user_id)
            # Las consultas van antes del registro: "cuánto gasté" es pregunta.
            if _SUMMARY_RE.search(lower):
                return self._handle_summary(lower, user_id, svc)
            if _SLASH_LOG_RE.match(lower) or _LOG_RE.search(lower):
                return self._handle_log(user_input, user_id, svc)
        except ValueError as exc:
            return f"No pude registrarlo: {exc}"
        except Exception as exc:  # noqa: BLE001 — nunca tumbar el dispatch
            log.error("[expenses] fallo manejando %r: %s", user_input[:60], exc)
            return "Hubo un error con el registro de gastos. Revisá el log del backend."

        return None

    # ── Handlers ─────────────────────────────────────────────────────────

    def _handle_log(self, user_input: str, user_id: str, svc) -> str:
        from modules.expenses.service import (
            CATEGORY_LABELS, format_amount, local_now,
        )

        text = user_input.strip()
        amount = _extract_amount(text)
        if amount is None:
            return "¿Cuánto fue? Decime el monto, por ejemplo: gasté 25 en el almuerzo."

        currency = _detect_currency(text)
        description = _extract_description(text, amount)

        spent_ts = None
        if _DAY_BEFORE_RE.search(text):
            spent_ts = (local_now() - timedelta(days=2)).timestamp()
        elif _YESTERDAY_RE.search(text):
            spent_ts = (local_now() - timedelta(days=1)).timestamp()

        expense = svc.add_expense(
            user_id=user_id,
            amount=amount,
            description=description,
            currency=currency,
            spent_ts_utc=spent_ts,
        )

        label = CATEGORY_LABELS.get(expense.category, expense.category)
        desc_part = f" en {expense.description}" if expense.description else ""
        lines = [
            f"Registrado: {format_amount(expense.amount, expense.currency)}{desc_part} "
            f"[{label}] (#{expense.id})"
        ]

        summary = svc.summary(user_id, "month", currency)
        lines.append(
            f"Total del mes: {format_amount(summary['total'], currency)} "
            f"en {summary['count']} gasto(s)."
        )
        alert = self._budget_alert(user_id, svc, expense.category)
        if alert:
            lines.append(alert)
        return "\n".join(lines)

    def _budget_alert(self, user_id: str, svc, category: str) -> Optional[str]:
        """Aviso si el gasto recién registrado empuja un presupuesto a warn/over."""
        from modules.expenses.service import (
            CATEGORY_LABELS, TOTAL_BUDGET_KEY, format_amount,
        )
        for st in svc.budget_status(user_id):
            if st["category"] not in (TOTAL_BUDGET_KEY, category):
                continue
            if st["level"] == "ok":
                continue
            scope = (
                "total" if st["category"] == TOTAL_BUDGET_KEY
                else CATEGORY_LABELS.get(st["category"], st["category"])
            )
            if st["level"] == "over":
                return (
                    f"Atención: pasaste el presupuesto {scope} del mes — "
                    f"{format_amount(st['spent'], st['currency'])} de "
                    f"{format_amount(st['limit'], st['currency'])} "
                    f"({st['pct']:.0f}%)."
                )
            return (
                f"Ojo: vas {st['pct']:.0f}% del presupuesto {scope} "
                f"({format_amount(st['remaining'], st['currency'])} disponibles)."
            )
        return None

    def _handle_summary(self, lower: str, user_id: str, svc) -> str:
        from modules.expenses.service import (
            CATEGORY_LABELS, TOTAL_BUDGET_KEY, format_amount,
        )

        if _TODAY_RE.search(lower):
            period = "today"
        elif _WEEK_RE.search(lower):
            period = "week"
        else:
            period = "month"

        currency = _detect_currency(lower)
        data = svc.summary(user_id, period, currency)

        if data["count"] == 0 and not data["totals_by_currency"]:
            return f"No tenés gastos registrados {data['label']}."

        lines = [
            f"Gastos {data['label']}: {format_amount(data['total'], currency)} "
            f"({data['count']} registro(s))"
        ]

        # Otras monedas se listan aparte: sumarlas sería un dato falso.
        for cur, total in data["totals_by_currency"].items():
            if cur != currency:
                lines.append(f"  + {format_amount(total, cur)} en {cur}")

        prev = data["previous_total"]
        if prev > 0:
            diff = data["total"] - prev
            trend = "más" if diff > 0 else "menos"
            pct_change = abs(diff) / prev * 100
            lines.append(
                f"Comparado con el período anterior: {format_amount(abs(diff), currency)} {trend} "
                f"({pct_change:.0f}%)."
            )

        if data["by_category"]:
            lines.append("Por categoría:")
            top = data["by_category"] if _BREAKDOWN_RE.search(lower) else data["by_category"][:5]
            for cat, total, count in top:
                label = CATEGORY_LABELS.get(cat, cat)
                share = (total / data["total"] * 100) if data["total"] > 0 else 0
                lines.append(
                    f"  {label}: {format_amount(total, currency)} "
                    f"({share:.0f}%, {count} gasto(s))"
                )

        for st in svc.budget_status(user_id):
            if st["category"] == TOTAL_BUDGET_KEY:
                lines.append(
                    f"Presupuesto del mes: {_progress_bar(st['spent'], st['limit'])} "
                    f"{st['pct']:.0f}% — {format_amount(st['remaining'], st['currency'])} disponibles."
                )
                break

        return "\n".join(lines)

    def _handle_budget(self, lower: str, svc, user_id: str) -> str:
        from modules.expenses.service import (
            CATEGORY_LABELS, TOTAL_BUDGET_KEY, format_amount, normalize_category,
        )

        m = _BUDGET_RE.search(lower)
        raw_category = m.group(1) if m else None
        raw_amount = (m.group(2) or m.group(3)) if m else None

        if raw_amount:
            amount = _parse_money(str(raw_amount))
            if amount is None or amount <= 0:
                return "No entendí el monto del presupuesto. Ejemplo: /presupuesto 1500"
            category = normalize_category(raw_category) if raw_category else TOTAL_BUDGET_KEY
            currency = _detect_currency(lower)
            svc.set_budget(user_id, amount, category, currency)
            scope = (
                "total mensual" if category == TOTAL_BUDGET_KEY
                else f"de {CATEGORY_LABELS.get(category, category)}"
            )
            return f"Presupuesto {scope} fijado en {format_amount(amount, currency)}."

        statuses = svc.budget_status(user_id)
        if not statuses:
            return (
                "No tenés presupuestos definidos. Fijá uno con: /presupuesto 1500 "
                "(o por categoría: /presupuesto comida 400)."
            )

        lines = ["Presupuestos del mes:"]
        for st in statuses:
            scope = (
                "total" if st["category"] == TOTAL_BUDGET_KEY
                else CATEGORY_LABELS.get(st["category"], st["category"])
            )
            state = {"ok": "", "warn": " — cerca del límite", "over": " — EXCEDIDO"}[st["level"]]
            lines.append(
                f"  {scope}: {_progress_bar(st['spent'], st['limit'])} "
                f"{format_amount(st['spent'], st['currency'])} de "
                f"{format_amount(st['limit'], st['currency'])} "
                f"({st['pct']:.0f}%){state}"
            )
        return "\n".join(lines)

    def _handle_delete(self, lower: str, user_id: str, svc) -> str:
        from modules.expenses.service import format_amount

        m = _DELETE_RE.search(lower)
        expense_id = next((g for g in (m.groups() if m else ()) if g), None)

        if expense_id:
            expense = svc.get_expense(user_id, int(expense_id))
            if expense is None:
                return f"No encontré el gasto #{expense_id}."
            svc.delete_expense(user_id, int(expense_id))
            return f"Gasto #{expense_id} borrado ({format_amount(expense.amount, expense.currency)})."

        removed = svc.delete_last(user_id)
        if removed is None:
            return "No hay gastos para borrar."
        desc = f" en {removed.description}" if removed.description else ""
        return f"Borré el último gasto: {format_amount(removed.amount, removed.currency)}{desc}."
