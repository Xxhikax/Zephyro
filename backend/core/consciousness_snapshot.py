"""
ConsciousnessSnapshot — Comprime LivingState en contexto inyectable al LLM.

Responsabilidades:
- Genera un snapshot compacto (tier=1 < 200 tokens) con estado del sistema
- Siempre incluye la fecha verificada del servidor (anti-hallucination de fechas)
- Rastrea fallos de guards hoy (reseteo diario automático)
- Se regenera en cada llamada — no cachea (LivingState ya persiste el estado)
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Callable, Optional


class ConsciousnessSnapshot:
    """
    Comprime el estado interno de Zephyro en un string inyectable al LLM.

    El getter de LivingState es lazy (lambda) para evitar imports circulares.
    El snapshot se regenera en cada llamada a generate() — sin caché.
    """

    def __init__(self, living_state_getter: Callable[[], Optional[Any]]) -> None:
        self._get_ls = living_state_getter
        self._failed_today: dict[str, int] = {}   # guard_name → count
        self._failed_date: Optional[date] = None  # para reseteo diario
        self._last_verified_date: Optional[str] = None

    # ── Estado de guards ──────────────────────────────────────────────────────

    def _reset_daily_counters_if_needed(self) -> None:
        today = datetime.now().date()
        if self._failed_date != today:
            self._failed_today.clear()
            self._failed_date = today

    def record_guard_failure(self, guard_name: str) -> None:
        """Registra un fallo de guard. Se resetea automáticamente cada día."""
        self._reset_daily_counters_if_needed()
        self._failed_today[guard_name] = self._failed_today.get(guard_name, 0) + 1

    def set_verified_date(self, date_str: str) -> None:
        """Actualiza la fecha verificada para incluir en el snapshot."""
        self._last_verified_date = date_str

    # ── Generación ────────────────────────────────────────────────────────────

    def generate(self, tier: int = 1) -> str:
        """
        Genera un snapshot del estado del sistema para inyectar al LLM.

        tier=1 (siempre presente, < 200 tokens):
            [SYSTEM STATE - verified HH:MM:SS]
            date: YYYY-MM-DD (server-verified)
            skills_degraded: skill(N failures), ...    ← solo si hay
            active_issues: N open                      ← solo si hay
            preconditions_failed_today: guard (Nx)     ← solo si hay

        tier=2 agrega: todas las skills + restart_count + recent_changes[-3:]
        tier=3 agrega: JSON completo del estado interno
        """
        self._reset_daily_counters_if_needed()
        ls = self._get_ls()

        now = datetime.now()
        now_str = now.strftime("%H:%M:%S")
        verified_date = self._last_verified_date or now.strftime("%Y-%m-%d")

        lines: list[str] = [
            f"[SYSTEM STATE - verified {now_str}]",
            f"date: {verified_date} (server-verified)",
        ]

        if ls is not None:
            state = ls._state  # acceso directo — mismo módulo de confianza

            # Skills degraded/broken
            skills = state.get("skills", {})
            degraded = [
                f"{name}({v.get('consecutive_failures', 0)} failures)"
                for name, v in skills.items()
                if v.get("status") in ("degraded", "broken")
            ]
            if degraded:
                lines.append(f"skills_degraded: {', '.join(degraded)}")

            # Issues abiertos
            open_issues = [
                i for i in state.get("active_issues", [])
                if i.get("status") != "resolved"
            ]
            if open_issues:
                lines.append(f"active_issues: {len(open_issues)} open")

            if tier >= 2:
                # Todas las skills con su estado
                if skills:
                    skill_parts = [
                        f"{name}({v.get('status', '?')})"
                        for name, v in skills.items()
                    ]
                    lines.append(f"skills: {', '.join(skill_parts)}")

                # Restart count
                system = state.get("system", {})
                restart_count = system.get("restart_count", 0)
                lines.append(f"restart_count: {restart_count}")

                # Cambios recientes
                changes = state.get("recent_changes", [])[:3]
                if changes:
                    change_parts = [
                        f"{c.get('commit_hash', '?')[:7]}: {c.get('message', '?')[:40]}"
                        for c in changes
                    ]
                    lines.append(f"recent_changes: {'; '.join(change_parts)}")

            if tier >= 3:
                lines.append(json.dumps(state, ensure_ascii=False, indent=2))

        # Guard failures today
        if self._failed_today:
            parts = [f"{k} ({v}x)" for k, v in self._failed_today.items()]
            lines.append(f"preconditions_failed_today: {', '.join(parts)}")

        return "\n".join(lines)
