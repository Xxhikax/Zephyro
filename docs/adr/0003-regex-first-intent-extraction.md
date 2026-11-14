# 0003: Regex fast path first, LLM extraction only as a fallback

**Status:** Accepted

## Context
Commands such as "abrí spotify" or "agendá dentista el lunes a las 10" must run
instantly and deterministically. Natural phrasings ("¿me podrías abrir la página
de...?") break pure regex. But sending every message to an LLM puts a network
call, and non-determinism, on the hottest path of the system.

## Decision
Each domain gets two layers:

1. **Regex fast path.** The router's keyword layers, plus the schedule skill's
   semantic patterns.
2. **LLM extractor fallback.** It extracts into a fixed JSON schema
   (`browser_intent_schema.py`, `_extract_schedule_intent`). It is tried only
   when the regex missed but a loose hint, such as an action verb or a domain
   keyword, suggests the message was meant as a command. Results are cached in a
   bounded LRU.

Conversational guards run before both layers. Complaints, rhetorical questions
and past-tense reports ("no abrió") are never treated as commands.

## Consequences
- The common commands cost no model call and behave the same every time.
- Every new intent needs a schema entry and regex cases, and the guards need
  tests whenever the regexes change.
- The fallback inherits the chat model's availability. In cloud mode the router
  skips its LLM classification layer on purpose, so anything the keywords miss
  is answered as chat, not executed.
