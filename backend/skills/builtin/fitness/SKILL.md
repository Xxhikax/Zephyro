---
name: fitness
description: "Gestión de rutinas de entrenamiento (Muay Thai/boxeo/gym/natación), agenda automática de sesiones, racha y resumen semanal"
triggers:
  - "entreno"
  - "rutina"
  - "fitness"
  - "boxeo"
  - "muay thai"
  - "mi racha"
  - "/fitness"
  - "/racha"
  - "hazme un plan"
  - "cambiar a gym"
  - "prefiero entrenar"
  - "reprograma mi entreno"
  - "hice mi entreno"
  - "resumen semanal"
requires:
  python:
    - psycopg2
provides:
  slash_commands:
    - name: "/fitness"
      description: "Genera o muestra el plan de entrenamiento actual"
    - name: "/racha"
      description: "Muestra racha actual de entrenamientos"
enabled: true
uses_tools:
  - llm
  - vision
risk_level: low
---

# Fitness Skill

Maneja el plan de entrenamiento personalizado tipo coach Muay Thai/boxeo.
Genera rutinas con Claude Sonnet, las agenda automáticamente evitando conflictos con clases/trabajo,
y registra el progreso con verificación opcional por foto.

## Comandos

- `genera mi rutina` / `/fitness` — genera plan y lo agenda en las próximas 4 semanas
- `entreno los martes y jueves` — fija días preferidos
- `prefiero entrenar de mañana/noche` — actualiza horario preferido
- `cambiar a gym|muay thai|natacion|casa` — cambia el contexto del plan
- `reprograma mi entreno de hoy` / `mueve mi sesion` — busca slot alternativo
- `hice mi entreno` — loggea la sesión más cercana de hoy
- `mi racha` / `/racha` — muestra racha actual y máxima
- `resumen semanal` — resumen de sesiones de la semana

## Contextos disponibles

| Slug | Descripción |
|---|---|
| `home` | Soga, bolsa pesada, espacio libre (por defecto) |
| `gym` | Pesas, máquinas, cardio completo |
| `muay_thai` | Complemento a clases presenciales |
| `swimming` | Complemento a natación |

## Notas técnicas

- Storage: PostgreSQL (tablas workout_plans, workout_sessions, fitness_profile, streaks)
- LLM: Claude Sonnet cloud para generación de planes
- Vision: Claude Haiku Vision para verificación de fotos post-entreno
- Timezone: UTC en storage, America/Lima para display
