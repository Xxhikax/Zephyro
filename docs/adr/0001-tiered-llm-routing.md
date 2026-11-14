# 0001: Route each request across local and cloud model tiers

**Status:** Accepted

## Context
Zephyro gets very uneven traffic: greetings, one-line commands, long conceptual
questions, browser automation and code questions. A cloud model for everything
adds latency and cost to a "hola". A small local model for everything makes the
hard answers noticeably worse.

## Decision
Three tiers, chosen per request in `LLMManager` (`backend/core/llm_manager.py`):

| Tier | Model | Used for |
|---|---|---|
| 1 | `llama3.2:1b` (Ollama) | greetings, quick commands |
| 2 | `llama3.2:3b` (Ollama) | balanced chat |
| 3 | Cloud: DeepSeek, Gemini, OpenAI or Anthropic | complex requests, tool use, extraction |

Escalation uses a complexity score (`complexity_classifier.py`) plus a length
threshold. Cloud model IDs live in a single catalog (`model_catalog.py`) that
remaps retired IDs, and a test forbids hard-coded model IDs anywhere else.

## Consequences
- Trivial requests never pay a network round trip.
- The router has to commit to a tier before generating, and the quality is
  visibly uneven across tiers.
- Every provider has its own quirks (thinking defaults, rejected parameters).
  The catalog and the per-provider translation in `LLMManager` keep them out of
  the callers.
