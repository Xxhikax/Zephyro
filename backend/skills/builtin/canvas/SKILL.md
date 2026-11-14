---
name: canvas
description: "Integración con Canvas LMS: tareas pendientes, entregas faltantes, calificaciones, anuncios y eventos de hoy"
triggers:
  - "/canvas"
  - "canvas "
  - "mis tareas de canvas"
  - "tareas de canvas"
  - "entregas pendientes"
  - "calificaciones canvas"
  - "anuncios de canvas"
  - "que tengo en canvas"
  - "qué tengo en canvas"
  - "mis notas"
  - "mi nota"
  - "hay pendientes"
  - "tengo pendientes"
  - "nota de"
requires:
  python: [httpx]
  # CANVAS_BASE_URL / CANVAS_API_TOKEN NO van como requires.env: el loader descarta el
  # skill entero si falta un requisito, y eso hacía que "pendientes en canvas" se
  # escapara a la agenda. El skill se registra siempre; handle() reporta vía
  # is_configured() si falta la config (ver sección Configuración más abajo).
provides:
  slash_commands:
    - name: "/canvas"
      description: "Resumen de Canvas: tareas próximas (7 días)"
    - name: "canvas tareas"
      description: "Tareas pendientes próximos 7 días"
    - name: "canvas faltantes"
      description: "Entregas faltantes / atrasadas"
    - name: "canvas calificaciones"
      description: "Calificaciones actuales por curso"
    - name: "canvas anuncios"
      description: "Anuncios recientes de todos los cursos"
    - name: "canvas hoy"
      description: "Eventos del calendario de Canvas de hoy"
enabled: true
---

# Canvas LMS Skill

Conecta Zephyro con tu Canvas universitario via API REST.

## Configuración

Añadir en `backend/.env`:
```
CANVAS_BASE_URL=https://tu-universidad.instructure.com
CANVAS_API_TOKEN=tu_token_aqui
```

El token se genera en Canvas → Cuenta → Configuración → Tokens de acceso.

## Comandos

| Frase | Acción |
|---|---|
| `/canvas` o `canvas tareas` | Tareas próximas (7 días) |
| `canvas faltantes` / `sin entregar` | Entregas atrasadas o faltantes |
| `canvas calificaciones` / `mis notas` | Notas actuales por materia |
| `canvas anuncios` | Anuncios recientes de tus cursos |
| `canvas hoy` / `eventos hoy canvas` | Eventos del calendario de hoy |

## Caché

Los resultados se cachean 10 minutos en memoria para evitar llamadas repetidas.
El caché se limpia al reiniciar el backend.
