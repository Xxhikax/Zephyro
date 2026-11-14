---
name: system-monitor
description: "Monitoreo del sistema local — RAM, CPU, disco, red, procesos"
triggers:
  - "/system"
  - "/ram"
  - "/cpu"
  - "/disk"
  - "/red"
  - "memoria ram"
  - "uso de cpu"
  - "disco duro"
  - "espacio en disco"
  - "uso de memoria"
  - "estado del sistema"
  - "procesos"
  - "temperatura"
requires:
  python: [psutil]
provides:
  slash_commands:
    - name: "/system"
      description: "Reporte completo del sistema (RAM, CPU, disco, red, procesos)"
    - name: "/ram"
      description: "Uso de memoria RAM"
    - name: "/cpu"
      description: "Uso de CPU por nucleo"
    - name: "/disk"
      description: "Espacio en disco por particion"
    - name: "/red"
      description: "Estadisticas de red (bytes enviados/recibidos)"
enabled: true
---

# System Monitor Skill

Monitorea recursos del sistema local en tiempo real: RAM, CPU, disco, red y procesos.

## Comandos

- `/system` — Reporte completo del sistema
- `/ram` — Uso de memoria RAM (total, usado, disponible, porcentaje)
- `/cpu` — Uso de CPU global y por nucleo, load average
- `/disk` — Espacio en disco por particion (ignora squashfs/snap)
- `/red` — Bytes enviados y recibidos por red

## Lenguaje natural

Tambien responde a frases en espanol como:
- "cuanta RAM estoy usando"
- "uso de CPU"
- "espacio en disco"
- "estado del sistema"
- "procesos del sistema"
