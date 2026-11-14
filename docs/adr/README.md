# Architecture Decision Records

Short records of the decisions that shaped Zephyro: the context, the decision,
and what it cost. They were written after the fact from the project's working
notes, so each one also says what turned out to be wrong or is still open.

| # | Decision | Status |
|---|---|---|
| [0001](0001-tiered-llm-routing.md) | Route each request across local and cloud model tiers | Accepted |
| [0002](0002-request-context.md) | Carry per-request state in an explicit `RequestContext` | Accepted |
| [0003](0003-regex-first-intent-extraction.md) | Regex fast path first, LLM extraction only as a fallback | Accepted |
| [0004](0004-action-guardrail.md) | Classify automations by "can the user lose work or control?" and fail closed | Accepted |
| [0005](0005-declarative-notification-rules.md) | One notification path driven by declarative rules | Accepted |
| [0006](0006-per-turn-reasoning-policy.md) | Pick the reasoning effort per turn, rolled out in shadow mode | Accepted (shadow) |
