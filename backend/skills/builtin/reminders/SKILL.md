---
name: reminders
description: "Sistema de recordatorios con parsing de tiempo en lenguaje natural"
triggers:
  - "recuérdame "
  - "recuerdame "
  - "/recordatorios"
  - "/reminders"
  - "lista recordatorios"
  - "mis recordatorios"
requires:
  python: []
provides:
  slash_commands:
    - name: "recuérdame"
      description: "Crear recordatorio (ej: recuérdame llamar en 2 horas)"
    - name: "/recordatorios"
      description: "Listar recordatorios activos"
---

# Reminders Skill

Crea y gestiona recordatorios basados en tiempo, con parsing de lenguaje natural en español.

## Uso

- `recuérdame [cosa] en 2 horas`
- `recuérdame [cosa] mañana a las 10am`
- `recuérdame [cosa] a las 15:00`
- `/recordatorios` — listar activos
