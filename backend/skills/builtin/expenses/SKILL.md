---
name: expenses
description: "Tracking de gastos personales: registra cuánto gastaste y en qué, resume por día/semana/mes con desglose por categoría y avisa cómo vas contra tu presupuesto mensual"
triggers:
  - "/gasto"
  - "/gastos"
  - "/presupuesto"
  - "gasté"
  - "gaste"
  - "pagué"
  - "pague"
  - "cuánto gasté"
  - "cuanto gaste"
  - "mis gastos"
  - "gastos de hoy"
  - "gastos del mes"
  - "resumen de gastos"
  - "en qué gasté"
  - "en que gaste"
requires:
  python: []
  # Sin requires.env a propósito: el loader descarta la skill entera si falta un
  # requisito (bug histórico de canvas). Esta skill no necesita config externa —
  # SQLite en storage/expenses.db se crea solo.
uses_tools: []
risk_level: low
provides:
  slash_commands:
    - name: "/gasto"
      description: "Registrar un gasto (ej: /gasto 25 almuerzo)"
    - name: "/gastos"
      description: "Resumen del mes con desglose por categoría"
    - name: "/gastos hoy"
      description: "Gastos de hoy"
    - name: "/gastos semana"
      description: "Gastos de esta semana"
    - name: "/presupuesto"
      description: "Ver estado del presupuesto mensual"
    - name: "/presupuesto N"
      description: "Fijar presupuesto mensual total (ej: /presupuesto 1500)"
enabled: true
---

# Expenses Skill

Tracking manual de gastos con presupuestos mensuales. Persistencia SQLite en
`storage/expenses.db` (`modules/expenses/service.py`).

## Comandos

- `gasté 25 en el almuerzo` / `pagué 40 de taxi` → registra e infiere categoría
- `/gasto 25 almuerzo` → registro explícito
- `/gastos` → resumen del mes: total, desglose por categoría, comparativa con el mes anterior
- `/gastos hoy` · `/gastos semana` → mismo resumen en otro período
- `en qué gasté` → desglose por categoría del mes
- `/presupuesto 1500` → presupuesto mensual total
- `/presupuesto comida 400` → presupuesto por categoría
- `/presupuesto` → estado: gastado, restante, % y nivel (ok / warn ≥80% / over >100%)
- `borrar último gasto` · `borrar gasto 12` → deshace un registro

## Categorías

`food` `transport` `university` `subscriptions` `health` `entertainment`
`shopping` `services` `savings` `other` — se infieren por palabra clave de la
descripción (`infer_category`), o se pasan explícitas con el alias en español.

## Desambiguación (importante)

El dispatch de skills es **primer match gana** en orden alfabético de directorio
(`skills/registry.py::dispatch`), y `expenses` corre ANTES de `fitness`,
`nutrition` y `schedule`. Por eso el matcher exige **verbo de gasto + monto**:

- `tomé un café` → NO es esta skill (va a nutrition)
- `pagué 12 por el café` → sí es esta skill
- `gasté mucho tiempo en eso` → NO (no hay monto)

Al tocar los regex, corré `tests/test_expenses_skill.py`: incluye los casos de
colisión con nutrition, fitness y schedule.

## Monedas

El monto se guarda con su moneda (default `PEN`). Los totales se agrupan **por
moneda**, nunca se suman entre monedas. `$`/`dólares`/`usd` → `USD`.

## Gancho para la Fase 1 (event bus)

`ExpenseService.budget_status()` ya devuelve `level` (`ok`/`warn`/`over`) por
presupuesto. Cuando exista la capa de reglas del event bus, ese campo es el
disparador de la notificación proactiva — no hace falta tocar esta skill.
