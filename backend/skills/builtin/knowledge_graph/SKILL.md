---
name: knowledge-graph
description: "Grafo de conocimiento local — extrae entidades y relaciones de conversaciones"
triggers:
  - "/kg"
  - "/grafo"
requires:
  python:
    - networkx
provides:
  slash_commands:
    - name: "/kg"
      description: "Consultar el grafo de conocimiento (stats, export, o query)"
    - name: "/grafo"
      description: "Alias de /kg"
---

# Knowledge Graph Skill

Maneja el grafo de conocimiento de Zephyro. Extrae entidades (personas, tecnologías, proyectos, lugares) de las conversaciones y construye un grafo de relaciones.

## Subcomandos

- `/kg` o `/kg stats` — Estadísticas del grafo (nodos, aristas, entidades)
- `/kg export` — Exportar el grafo como JSON
- `/kg <pregunta>` — Query en lenguaje natural sobre el grafo
