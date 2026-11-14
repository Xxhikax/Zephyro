---
name: log-reader
description: "Lectura read-only de logs de Zephyro con filtro por request_id"
triggers:
  - "/logs"
  - "/log"
  - "ver logs"
  - "ultimos logs"
  - "errores recientes"
  - "verifica los logs"
  - "verifica logs"
  - "que dicen los logs"
  - "revisa los logs"
  - "revisa logs"
  - "revisar logs"
requires: {}
provides:
  slash_commands:
    - name: "/logs"
      description: "Ultimas N lineas del log de backend"
    - name: "/logs errors"
      description: "Solo errores recientes"
    - name: "/logs request <id>"
      description: "Todas las lineas con ese request_id"
enabled: true
---

# Log Reader Skill

Permite a Zephyro consultar sus propios logs sin shell. Lectura read-only sobre archivos en `logs/`.

## Comandos

- `/logs` — ultimas 50 lineas de `logs/backend.log`
- `/logs <archivo> <N>` — lineas N del archivo (ej. `/logs terminal.log 30`)
- `/logs errors` — solo lineas con level >= ERROR de los ultimos 5 minutos
- `/logs request <request_id>` — agrupa todas las lineas con ese request_id

## Archivos disponibles

- `backend.log`
- `terminal.log` (de la TerminalSkill)
- `diagnostic_agent.log`
- `living_state.log`
- `opencode.log` (si existe)

## Uso esperado

Despues de ejecutar un comando con TerminalSkill, Zephyro puede llamar
`log-reader` con el `request_id` para verificar que todo paso bien.
