"""Módulo de tracking de gastos personales (SQLite)."""
from .service import Expense, ExpenseService, get_expense_service

__all__ = ["Expense", "ExpenseService", "get_expense_service"]
