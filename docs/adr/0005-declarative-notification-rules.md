# 0005: One notification path driven by declarative rules

**Status:** Accepted

## Context
Every proactive checker (upcoming events, budgets, CI failures and so on) used to
do everything itself:
- built its own payload;
- pushed it to WebSocket and Telegram by hand;
- kept its thresholds in code;
- deduplicated in an in-memory dict that was lost on every restart.

As a result, the same alert could fire again after a reboot.

## Decision
Producers only **declare facts**, as domain events: `schedule.event_upcoming`,
`expenses.budget_threshold`, `ci.run_failed`. A `NotificationCenter`
(`backend/core/notification_center.py`) evaluates the rules and delivers the
notifications.

- **Typed rules.** A rule has a minimum severity, a payload match, channels, a
  cooldown and quiet hours. **Conditions are never evaluated expressions**, so
  nothing like `eval()` ever runs on data read from the database.
- **Persistent deduplication.** The rules and the delivery log live in SQLite, so
  deduplication survives restarts.
- **Testable decisions.** `evaluate()` is pure and returns a decision together
  with its reason. `emit()` does the delivery.
- **Retries.** If no channel accepted a notification, it is not marked as sent,
  and the next tick retries it.

## Consequences
- Thresholds, channels and quiet hours are data, not code.
- Migrated alerts keep the exact payload contract the frontend already consumed.
- Not everything moved. Canvas reminders keep their own per-item state machine,
  because putting them under the rules would deduplicate them twice. Migrating
  them is a separate slice.
