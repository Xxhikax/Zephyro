---
name: daily-briefing
description: "Resumen matutino: agenda del dia + Canvas + clima. Configurable por voz."
version: "1.0"
triggers:
  - "que tengo hoy"
  - "resumen del dia"
  - "dame el briefing"
  - "/briefing"
  - "/resumen"
  - "que hay hoy"
  - "que tengo para hoy"
  - "briefing"
  - "resumen"
uses_tools:
  - schedule:read
  - canvas:read
  - web:read
risk_level: low
enabled: true
---
Skill que genera un resumen del dia combinando agenda local, tareas de Canvas LMS y clima.
Tambien permite cambiar la hora del briefing automatico por voz ("cambia el briefing a las 9am").
