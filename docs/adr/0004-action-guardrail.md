# 0004: Classify automations by "can the user lose work or control?" and fail closed

**Status:** Accepted

## Context
Zephyro can click, type, open and close applications. The first version of the
guardrail asked "is this action reversible?". Under that question, focusing a
window, closing Word and logging off all came out as safe. Each call was
defensible on its own, and all three were wrong. Closing an app is reversible;
the unsaved document is not.

## Decision
`classify_action()` (`backend/core/action_risk_rules.py`) maps every automation
to `READ`, `REVERSIBLE` or `HIGH` using fixed rules. A `HIGH` action always asks
for a yes/no confirmation. The guiding question is **"can the user lose work or
control of their machine?"**

- `HIGH` covers:
  - clicks on buy, pay, send, delete or log in;
  - typing into password or card fields;
  - closing protected processes, or apps that hold unsaved work (Office,
    browsers, editors);
  - very short targets;
  - **any automation intent the rules do not know**.
- `close` and `update` are always evaluated. They used to sit behind a branch
  that turned out to make them unreachable in production.
- The guard runs at all three places where an action executes: the single flow,
  multi-task, and the implicit "yes".
- Pending confirmations are stored per session with a 120 s TTL.
- A future probabilistic layer may only *add* confirmations. It can never
  downgrade a `HIGH`.

## Consequences
- Unknown actions are safe by default, at the price of some extra
  confirmations.
- A lesson about tests. The old tests built the `analysis` dict by hand,
  without the `automation_intent` field that the router always sets, so they
  validated a shape that production never produces. The current tests
  (`tests/test_action_guard.py`) use the router's real output.
