---
# ── Campos OBLIGATORIOS ──────────────────────────────────────────────────
# name: identificador único de la skill (kebab/snake). Si falta, el loader NO la carga.
name: my-skill
# description: una línea. Se muestra en el índice de skills y en el prompt del LLM.
description: "Qué hace esta skill, en una frase"
# triggers: prefijos que activan la skill (matching por prefijo, case-insensitive).
#   SIN triggers la skill se carga pero el router NUNCA la rutea — salvo que
#   overridees matches_command() en skill.py (caso avanzado, ver vision_query).
triggers:
  - "mi comando "
  - "/mi-skill"

# ── Campos OPCIONALES (tienen defaults seguros) ──────────────────────────
# requires: dependencias que deben existir para que la skill cargue.
requires:
  python: []        # paquetes pip (ej. [psycopg2])
  env: []           # variables de entorno (ej. [MY_API_KEY])
  bins: []          # binarios en PATH (ej. [ffmpeg])
# provides: comandos slash que la skill expone (para ayuda/descubrimiento).
provides:
  slash_commands:
    - name: "/mi-skill"
      description: "Qué hace el comando"
enabled: true
# uses_tools: herramientas/sub-skills que esta skill invoca (para el grafo de
#   aprobación de drafts auto-generados). Ej: ["terminal:read", "llm", "vision"].
uses_tools: []
# risk_level: low | medium | high. Skills "high" requieren confirmación extra.
risk_level: low
---

# My Skill

Cuerpo Markdown: contexto completo de la skill, cargado on-demand cuando se
necesita inyectar en el prompt del LLM. Explicá qué hace, ejemplos de uso, y
cualquier detalle que el modelo necesite para usarla bien.

## Implementación

El `skill.py` que acompaña este SKILL.md debe:
- Heredar de `BaseSkill` (`from skills.base import BaseSkill`).
- Implementar `async def handle(self, user_input, ctx) -> Optional[str]`.
- (Opcional) Overridear `match_confidence(user_input) -> float` para dar al
  ClarificationGate una señal granular (1.0 trigger exacto, 0.6 semántico, etc.).
- NUNCA importar subprocess/os.system/eval/exec (el draft_linter los rechaza).
