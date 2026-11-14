---
name: voice-notes
description: "Captura rapida de ideas por voz. Clasifica por tema con Haiku y guarda en formato Obsidian (.md con frontmatter)."
version: "1.0"
triggers:
  - "nota:"
  - "apunta:"
  - "anota:"
  - "recuerda que"
  - "guarda esto"
  - "guarda que"
  - "/nota"
  - "nota de voz"
uses_tools:
  - filesystem:write
  - web:read
risk_level: low
enabled: true
---
Skill que captura notas de voz, las clasifica por tema usando Haiku 4.5, y las guarda en storage/voice_notes/ como archivos Markdown con frontmatter YAML (compatible con Obsidian).
Tambien permite buscar notas: "que notas tengo sobre X".
