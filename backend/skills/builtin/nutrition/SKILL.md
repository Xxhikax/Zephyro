---
name: nutrition
description: "Tracking de nutrición, agua, pasos y peso con foco en resistencia a insulina. Analiza comidas por foto o texto, registra macros, sugiere próxima comida y genera reportes semanales."
triggers:
  - "hice"
  - "acabo de comer"
  - "/macros"
  - "/qué como"
  - "qué cocino"
  - "qué como"
  - "/agua"
  - "tomé un vaso"
  - "tomar agua"
  - "/pasos"
  - "/peso"
  - "resumen nutrición"
  - "nutrición semanal"
  - "mi nutrición"
requires:
  python:
    # Importable module name (the pip package is psycopg2-binary, but
    # check_requirements() does __import__, which needs the module name).
    - psycopg2
  env:
    - PGHOST
    - PGDATABASE
uses_tools:
  - llm
  - vision
risk_level: low
provides:
  slash_commands:
    - name: "/macros"
      description: "Resumen del día con macros, agua, flags de insulina y adherencia"
    - name: "/agua"
      description: "Registrar agua (ej: /agua 250 o 'tomé un vaso')"
    - name: "/pasos"
      description: "Registrar pasos del día (ej: /pasos 9500)"
    - name: "/peso"
      description: "Registrar peso (ej: /peso 96.5)"
    - name: "/qué como"
      description: "Sugerencia de próxima comida respetando perfil IR y timing"
---

# Nutrition Skill

Skill de tracking nutricional con foco en **resistencia a insulina (IR)**.

## Comandos

- `hice [comida]` / `acabo de comer X` → analiza con LLM y registra
- `/macros` → resumen del día con progreso de macros + indicador insulina
- `/qué como` / `qué cocino` → sugiere próxima comida IR-friendly
- `/agua [N]` / `tomé un vaso` → registra agua (vaso = 250ml)
- `/pasos N` → registra pasos del día
- `/peso N` → registra peso en kg
- `resumen nutrición semanal` → reporte semanal con análisis

## Filosofía IR

Cada flag de insulina viene con un nudge educativo en español tuteo:
**nunca** "no comas esto" — siempre "X eleva la insulina rápido porque... alternativa: Y".

## Integración con foto

Si el usuario envía una foto sin contexto fitness → se enruta a nutrición para análisis visual.
