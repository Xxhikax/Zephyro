"""ExpenseService — tracking de gastos personales con persistencia SQLite.

Sigue el patrón exacto de backend/modules/schedule/service.py: SQLite en
``storage/``, lock de threading, singleton perezoso. Se usa SQLite y no Postgres
(como nutrition/fitness) a propósito: el volumen es chico y los gastos deben
seguir registrables aunque el pool de Postgres no esté disponible.

Los totales se agrupan SIEMPRE por moneda — sumar PEN y USD en un solo número
sería un dato falso.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("zephyro.expenses")

LOCAL_TZ = ZoneInfo("America/Lima")

DEFAULT_CURRENCY = "PEN"
CURRENCY_SYMBOLS = {"PEN": "S/", "USD": "$", "EUR": "€"}

# Presupuesto global: se guarda como una categoría reservada.
TOTAL_BUDGET_KEY = "__total__"

DEFAULT_CATEGORIES = (
    "food",
    "transport",
    "university",
    "subscriptions",
    "health",
    "entertainment",
    "shopping",
    "services",
    "savings",
    "other",
)

CATEGORY_LABELS = {
    "food": "comida",
    "transport": "transporte",
    "university": "universidad",
    "subscriptions": "suscripciones",
    "health": "salud",
    "entertainment": "entretenimiento",
    "shopping": "compras",
    "services": "servicios",
    "savings": "ahorro/inversión",
    "other": "otros",
}

# Alias en español → categoría canónica (para "/presupuesto comida 400").
CATEGORY_ALIASES = {
    "comida": "food",
    "comidas": "food",
    "alimentacion": "food",
    "alimentación": "food",
    "transporte": "transport",
    "movilidad": "transport",
    "pasajes": "transport",
    "universidad": "university",
    "uni": "university",
    "estudios": "university",
    "suscripciones": "subscriptions",
    "suscripcion": "subscriptions",
    "suscripción": "subscriptions",
    "salud": "health",
    "entretenimiento": "entertainment",
    "ocio": "entertainment",
    "compras": "shopping",
    "servicios": "services",
    "ahorro": "savings",
    "inversion": "savings",
    "inversión": "savings",
    "otros": "other",
    "otro": "other",
}

# Inferencia de categoría por palabra clave. Orden = prioridad: la primera
# tupla que matchee gana, así que lo específico va antes que lo genérico
# ("recarga claro" → services antes de que "celular" lo lleve a shopping).
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("services", (
        "recarga", "recibo", "luz", "agua potable", "internet", "wifi", "alquiler",
        "claro", "movistar", "entel", "bitel", "cable", "mantenimiento",
    )),
    ("subscriptions", (
        "netflix", "spotify", "suscripcion", "suscripción", "disney", "hbo", "max",
        "prime", "youtube premium", "chatgpt", "claude", "copilot", "dominio",
        "hosting", "vps", "cloud", "notion", "figma",
    )),
    ("food", (
        "almuerzo", "almuerce", "comida", "cena", "desayuno", "café", "cafe", "menu",
        "menú", "restaurante", "mercado", "supermercado", "wong", "tottus",
        "plaza vea", "delivery", "rappi", "pedidosya", "polleria", "pollería",
        "chifa", "ceviche", "cevicheria", "cevichería", "helado", "snack", "pan",
    )),
    ("transport", (
        "taxi", "uber", "indrive", "didi", "cabify", "combi", "bus", "metropolitano",
        "pasaje", "pasajes", "gasolina", "grifo", "tren", "peaje", "estacionamiento",
        "cochera", "scooter",
    )),
    ("university", (
        "universidad", "pension", "pensión", "matricula", "matrícula", "libro",
        "libros", "fotocopia", "impresion", "impresión", "cuaderno", "utiles",
        "útiles", "curso", "certificado", "tramite", "trámite",
    )),
    ("health", (
        "farmacia", "medicina", "medicamento", "pastilla", "doctor", "clinica",
        "clínica", "dentista", "gym", "gimnasio", "suplemento", "proteina",
        "proteína", "creatina", "inkafarma", "mifarma", "boticas", "seguro",
    )),
    ("entertainment", (
        "cine", "juego", "steam", "concierto", "bar", "cerveza", "trago",
        "discoteca", "fiesta", "salida", "netflix party", "billar",
    )),
    ("shopping", (
        "ropa", "zapatillas", "polo", "pantalon", "pantalón", "celular", "laptop",
        "audifonos", "audífonos", "teclado", "mouse", "monitor", "regalo",
        "mochila", "cargador", "cable usb",
    )),
    ("savings", (
        "ahorro", "ahorre", "ahorré", "inversion", "inversión", "cripto", "bitcoin",
        "fondo mutuo", "plazo fijo", "acciones",
    )),
)


def infer_category(text: str) -> str:
    """Deduce la categoría a partir de la descripción del gasto."""
    lower = (text or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in lower:
                return category
    return "other"


def normalize_category(raw: Optional[str]) -> str:
    """Normaliza una categoría escrita por el usuario (español o canónica)."""
    if not raw:
        return "other"
    key = raw.strip().lower()
    if key in DEFAULT_CATEGORIES:
        return key
    return CATEGORY_ALIASES.get(key, "other")


def format_amount(amount: float, currency: str = DEFAULT_CURRENCY) -> str:
    """Formatea un monto con el símbolo de su moneda: 'S/ 50.00'."""
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    return f"{symbol} {amount:,.2f}"


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def day_bounds_utc(day: Optional[datetime] = None) -> tuple[float, float]:
    """Timestamps UTC del inicio y fin del día local dado (default: hoy)."""
    ref = (day or local_now()).astimezone(LOCAL_TZ)
    start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def month_bounds_utc(day: Optional[datetime] = None) -> tuple[float, float]:
    """Timestamps UTC del inicio y fin del mes local del día dado."""
    ref = (day or local_now()).astimezone(LOCAL_TZ)
    start = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.timestamp(), end.timestamp()


def week_bounds_utc(day: Optional[datetime] = None) -> tuple[float, float]:
    """Timestamps UTC de la semana local (lunes a lunes) del día dado."""
    ref = (day or local_now()).astimezone(LOCAL_TZ)
    start = ref.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=ref.weekday()
    )
    return start.timestamp(), (start + timedelta(days=7)).timestamp()


@dataclass
class Expense:
    id: int
    user_id: str
    amount: float
    currency: str
    category: str
    description: str
    spent_ts_utc: float
    source: str
    notes: str
    created_at: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["spent_at_local"] = datetime.fromtimestamp(
            self.spent_ts_utc, timezone.utc
        ).astimezone(LOCAL_TZ).isoformat()
        d["category_label"] = CATEGORY_LABELS.get(self.category, self.category)
        d["amount_display"] = format_amount(self.amount, self.currency)
        return d


class ExpenseService:
    """Persistencia y CRUD de gastos + presupuestos mensuales."""

    DB_PATH = str(
        Path(__file__).resolve().parent.parent.parent / "storage" / "expenses.db"
    )

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or self.DB_PATH
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'PEN',
                    category TEXT NOT NULL DEFAULT 'other',
                    description TEXT NOT NULL DEFAULT '',
                    spent_ts_utc REAL NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    notes TEXT DEFAULT '',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expense_budgets (
                    user_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    monthly_limit REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'PEN',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, category)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expenses_user_ts "
                "ON expenses(user_id, spent_ts_utc)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expenses_user_cat "
                "ON expenses(user_id, category)"
            )
            conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────

    def add_expense(
        self,
        user_id: str,
        amount: float,
        description: str = "",
        category: Optional[str] = None,
        currency: str = DEFAULT_CURRENCY,
        spent_ts_utc: Optional[float] = None,
        source: str = "manual",
        notes: str = "",
    ) -> Expense:
        """Registra un gasto. Si no se pasa categoría, se infiere de la descripción."""
        if amount <= 0:
            raise ValueError("El monto debe ser mayor a 0")
        cat = normalize_category(category) if category else infer_category(description)
        if cat not in DEFAULT_CATEGORIES:
            cat = "other"
        ts = spent_ts_utc if spent_ts_utc is not None else time.time()
        now = time.time()
        with self._lock, sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO expenses (
                    user_id, amount, currency, category, description,
                    spent_ts_utc, source, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, float(amount), currency, cat, description.strip(),
                 ts, source, notes, now),
            )
            conn.commit()
            expense_id = int(cur.lastrowid)
        logger.info(
            "[expenses] gasto #%s registrado: %s %s (%s)",
            expense_id, amount, currency, cat,
        )
        return Expense(
            id=expense_id, user_id=user_id, amount=float(amount), currency=currency,
            category=cat, description=description.strip(), spent_ts_utc=ts,
            source=source, notes=notes, created_at=now,
        )

    def get_expense(self, user_id: str, expense_id: int) -> Optional[Expense]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT id, user_id, amount, currency, category, description, "
                "spent_ts_utc, source, notes, created_at FROM expenses "
                "WHERE id = ? AND user_id = ?",
                (expense_id, user_id),
            ).fetchone()
        return Expense(*row) if row else None

    def list_expenses(
        self,
        user_id: str,
        since_ts: Optional[float] = None,
        until_ts: Optional[float] = None,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> list[Expense]:
        sql = (
            "SELECT id, user_id, amount, currency, category, description, "
            "spent_ts_utc, source, notes, created_at FROM expenses WHERE user_id = ?"
        )
        params: list = [user_id]
        if since_ts is not None:
            sql += " AND spent_ts_utc >= ?"
            params.append(since_ts)
        if until_ts is not None:
            sql += " AND spent_ts_utc < ?"
            params.append(until_ts)
        if category:
            sql += " AND category = ?"
            params.append(normalize_category(category))
        sql += " ORDER BY spent_ts_utc DESC LIMIT ?"
        params.append(int(limit))
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Expense(*row) for row in rows]

    def delete_expense(self, user_id: str, expense_id: int) -> bool:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM expenses WHERE id = ? AND user_id = ?",
                (expense_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_last(self, user_id: str) -> Optional[Expense]:
        """Borra el gasto más reciente — deshace un registro mal tipeado."""
        recent = self.list_expenses(user_id, limit=1)
        if not recent:
            return None
        return recent[0] if self.delete_expense(user_id, recent[0].id) else None

    # ── Agregados ─────────────────────────────────────────────────────────

    def totals_by_currency(
        self, user_id: str, since_ts: float, until_ts: float
    ) -> dict[str, float]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT currency, SUM(amount) FROM expenses "
                "WHERE user_id = ? AND spent_ts_utc >= ? AND spent_ts_utc < ? "
                "GROUP BY currency",
                (user_id, since_ts, until_ts),
            ).fetchall()
        return {cur: float(total or 0) for cur, total in rows}

    def totals_by_category(
        self,
        user_id: str,
        since_ts: float,
        until_ts: float,
        currency: str = DEFAULT_CURRENCY,
    ) -> list[tuple[str, float, int]]:
        """[(category, total, count)] de una moneda, ordenado desc por total."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT category, SUM(amount), COUNT(*) FROM expenses "
                "WHERE user_id = ? AND currency = ? "
                "AND spent_ts_utc >= ? AND spent_ts_utc < ? "
                "GROUP BY category ORDER BY SUM(amount) DESC",
                (user_id, currency, since_ts, until_ts),
            ).fetchall()
        return [(cat, float(total or 0), int(count)) for cat, total, count in rows]

    def summary(
        self,
        user_id: str,
        period: str = "month",
        currency: str = DEFAULT_CURRENCY,
    ) -> dict:
        """Resumen de 'today' | 'week' | 'month' con desglose y comparativa."""
        if period == "today":
            since, until = day_bounds_utc()
            prev_since, prev_until = day_bounds_utc(local_now() - timedelta(days=1))
            label = "hoy"
        elif period == "week":
            since, until = week_bounds_utc()
            prev_since, prev_until = week_bounds_utc(local_now() - timedelta(days=7))
            label = "esta semana"
        else:
            period = "month"
            since, until = month_bounds_utc()
            first_of_month = datetime.fromtimestamp(since, timezone.utc).astimezone(LOCAL_TZ)
            prev_since, prev_until = month_bounds_utc(first_of_month - timedelta(days=1))
            label = "este mes"

        totals = self.totals_by_currency(user_id, since, until)
        prev_totals = self.totals_by_currency(user_id, prev_since, prev_until)
        by_category = self.totals_by_category(user_id, since, until, currency)
        count = sum(c for _, _, c in by_category)

        return {
            "period": period,
            "label": label,
            "currency": currency,
            "total": totals.get(currency, 0.0),
            "totals_by_currency": totals,
            "previous_total": prev_totals.get(currency, 0.0),
            "by_category": by_category,
            "count": count,
            "since_ts": since,
            "until_ts": until,
        }

    # ── Presupuestos ──────────────────────────────────────────────────────

    def set_budget(
        self,
        user_id: str,
        monthly_limit: float,
        category: str = TOTAL_BUDGET_KEY,
        currency: str = DEFAULT_CURRENCY,
    ) -> dict:
        if monthly_limit <= 0:
            raise ValueError("El presupuesto debe ser mayor a 0")
        cat = category if category == TOTAL_BUDGET_KEY else normalize_category(category)
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO expense_budgets (user_id, category, monthly_limit, currency, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, category) DO UPDATE SET
                    monthly_limit = excluded.monthly_limit,
                    currency = excluded.currency,
                    updated_at = excluded.updated_at
                """,
                (user_id, cat, float(monthly_limit), currency, time.time()),
            )
            conn.commit()
        return {"category": cat, "monthly_limit": float(monthly_limit), "currency": currency}

    def get_budgets(self, user_id: str) -> list[dict]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT category, monthly_limit, currency FROM expense_budgets "
                "WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [
            {"category": cat, "monthly_limit": float(limit), "currency": cur}
            for cat, limit, cur in rows
        ]

    def delete_budget(self, user_id: str, category: str = TOTAL_BUDGET_KEY) -> bool:
        cat = category if category == TOTAL_BUDGET_KEY else normalize_category(category)
        with self._lock, sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM expense_budgets WHERE user_id = ? AND category = ?",
                (user_id, cat),
            )
            conn.commit()
            return cur.rowcount > 0

    def budget_status(self, user_id: str) -> list[dict]:
        """Estado de cada presupuesto del mes en curso.

        Devuelve dicts con spent/limit/remaining/pct/level. ``level`` es
        'ok' | 'warn' (>=80%) | 'over' (>100%) — es el dato que la Fase 1
        (event bus + reglas) va a consumir para notificar proactivamente.
        """
        budgets = self.get_budgets(user_id)
        if not budgets:
            return []
        since, until = month_bounds_utc()
        totals = self.totals_by_currency(user_id, since, until)
        out: list[dict] = []
        for b in budgets:
            cur = b["currency"]
            if b["category"] == TOTAL_BUDGET_KEY:
                spent = totals.get(cur, 0.0)
            else:
                by_cat = self.totals_by_category(user_id, since, until, cur)
                spent = next((t for c, t, _ in by_cat if c == b["category"]), 0.0)
            limit = b["monthly_limit"]
            pct = (spent / limit * 100) if limit > 0 else 0.0
            out.append({
                "category": b["category"],
                "currency": cur,
                "limit": limit,
                "spent": spent,
                "remaining": limit - spent,
                "pct": pct,
                "level": "over" if pct > 100 else ("warn" if pct >= 80 else "ok"),
            })
        out.sort(key=lambda d: d["pct"], reverse=True)
        return out


_singleton: Optional[ExpenseService] = None
_singleton_lock = threading.Lock()


def get_expense_service() -> ExpenseService:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ExpenseService()
    return _singleton
