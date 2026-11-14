# 0002: Carry per-request state in an explicit `RequestContext`

**Status:** Accepted

## Context
The orchestrator is a singleton shared by every channel: the desktop WebSocket,
Telegram, WhatsApp and the CLI. It used to keep the state of the current request
(`user_role`, `session_id`, `force_mode`, channel and so on) as instance
attributes. With concurrent channels that is a race condition. A Telegram
message could change `user_role` while a desktop request was still in flight.

## Decision
A `@dataclass RequestContext` (`backend/core/request_context.py`) holds
everything about one request: identity and role, session, channel and device,
flags such as `speak` or `reasoning_mode`, and a status callback. It is passed
explicitly through the chain: orchestrator → router → skills → `LLMManager`.

State that must outlive a request is keyed by session instead. For example,
pending confirmations are stored per `session_id`, so a "yes" sent on Telegram
cannot confirm an action that was queued from the desktop.

## Consequences
- No identity or mode leaks from one request into another.
- Every function shows what it depends on, which makes it easier to debug and to
  test.
- `ctx` has to be threaded through many signatures, and a field nobody reads is
  easy to miss. The UI's THINK toggle was stored in `ctx.reasoning_mode` but
  never reached `handle_input` until [0006](0006-per-turn-reasoning-policy.md).
