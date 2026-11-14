# 0006: Pick the reasoning effort per turn, rolled out in shadow mode

**Status:** Accepted, running in shadow mode

## Context
Reasoning ("thinking") makes models better at arithmetic, dates, logic and
code, and slower to start answering. Claude always ran at its default (`high`)
effort, and DeepSeek always ran with thinking off. That is one global setting
for a voice assistant whose turns range from "gracias" to scheduling puzzles.

## Decision
- **Router.** `pick_reasoning()` (`backend/core/reasoning_policy.py`) maps each
  message to `low`, `medium` or `high` using regex signals: code, calendar
  arithmetic, quantities, logic, false premises and constrained plans. It runs in
  about 0.05 ms and never calls an LLM. It keeps the **highest** level among all
  the signals that match, so a weak rule that fires first cannot hide a harder
  one.
- **Provider translation.** `LLMManager` turns the decision into each provider's
  control: Claude's `output_config.effort`, or DeepSeek's thinking flag (on only
  at `high`). With no decision, the request is byte-identical to before
  (tested).
- **Rollout.** `ZEPHYRO_REASONING_POLICY` takes `off`, `shadow` or `on`. The
  default is **shadow**: the decision is logged but not applied, so real traffic
  can be observed before anything changes.

## Consequences
- The evaluation ([`research/reasoning_effort`](../../research/reasoning_effort))
  measured the tradeoff on a sealed holdout:
  - the router misses 2 of 10 hard prompts;
  - always-on thinking doubles the median time to first token;
  - on this set, the accuracy gain is not distinguishable from zero.
- The policy stays in shadow until there is evidence that the latency is worth
  it. Rolling it back is a single environment variable.
