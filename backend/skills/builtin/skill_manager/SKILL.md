---
name: skill-manager
description: "Gestiona habilidades: listar, habilitar, deshabilitar, ver info"
triggers: ["/skills", "/habilidades"]
requires:
  python: []
  env: []
provides:
  slash_commands:
    - name: "/skills"
      description: "Listar, habilitar o deshabilitar habilidades"
---

# Skill Manager

Comandos disponibles:
- `/skills` o `/habilidades` - Lista todas las skills cargadas con estado
- `/skills info <nombre>` - Muestra SKILL.md completo de una skill
- `/skills enable <nombre>` - Habilita una skill deshabilitada
- `/skills disable <nombre>` - Deshabilita una skill
- `/skills search <texto>` - Busca por descripcion o capability
