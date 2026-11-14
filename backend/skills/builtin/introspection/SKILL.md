---
name: introspection
description: "Analisis profundo de codigo en 4 tiers: GitNexus, OpenCode (DeepSeek), Anthropic SDK, Claude CLI"
triggers:
  - "/analiza"
  - "/introspect"
  - "/analyze"
  - "analiza el repo"
  - "analiza el codigo"
  - "analiza la arquitectura"
  - "explica el codigo"
  - "como funciona"
  - "analisis profundo"
requires: {}
provides:
  slash_commands:
    - name: "/analiza <pregunta>"
      description: "Analisis tier 2 (DeepSeek) sobre el repo actual"
    - name: "/analiza con claude <pregunta>"
      description: "Analisis tier 4 con Claude Code CLI"
enabled: true
---

# Introspection Skill

Analisis profundo de codigo escalando por tiers segun costo/calidad:

| Tier | Backend | Cuando | Costo |
|------|---------|--------|-------|
| 1 | GitNexus (estructural) | Default — preguntas sobre simbolos, impacto, estructura | $0 |
| 2 | OpenCode + DeepSeek | Razonamiento sobre codigo, "como funciona X" | bajo |
| 3 | Anthropic SDK directo | Reformulacion sin necesitar contexto repo completo | medio |
| 4 | Claude Code CLI | Analisis profundo con todo el contexto del repo | alto |

Por defecto, las preguntas analiticas usan **Tier 1 + Tier 2** (DeepSeek).
Tier 4 (Claude) solo se activa con "**con Claude**" o "**deep**" en el input.

## Repo objetivo

Si no se especifica, usa el repo actual (cwd). Para apuntar a un repo
clonado: `analiza fastapi <pregunta>` busca en `D:\zephyro_repos\fastapi`.
