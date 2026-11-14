---
name: schedule
description: "Gestión de agenda y horarios (clases, exámenes, tareas, proyectos, ejercicio) con parsing temporal en español y clasificador universitario"
triggers:
  - "agenda "
  - "agéndame "
  - "agendame "
  - "agendar "
  - "qué tengo "
  - "que tengo "
  - "pon en mi agenda "
  - "ponme en mi agenda "
  - "agrega a mi agenda "
  - "añade a mi agenda "
  - "registra que tengo "
  - "apunta que tengo "
  - "anota que tengo "
  - "/agenda"
  - "/hoy"
  - "/semana"
  - "reprográmame"
  - "reprograma"
  - "no pude hacer"
  - "no puedo ir"
  - "cancela y mueve"
  - "mueve el evento"
  - "pásalo para"
requires:
  python: []
provides:
  slash_commands:
    - name: "/agenda"
      description: "Listar eventos próximos (próximos 7 días)"
    - name: "/hoy"
      description: "Listar eventos de hoy"
    - name: "agenda"
      description: "Crear evento (ej: agenda gym hoy 18:00 categoría exercise)"
---

# Schedule Skill

Gestiona la agenda personal con categorías: university, course_study,
personal_project, client_project, exercise, other.

## Uso

- `agenda <título> hoy <HH:MM>` — crea evento hoy
- `agenda <título> mañana <HH:MM>` — crea evento mañana
- `agenda <título> hoy <HH:MM> categoría <cat>` — especifica categoría
- `/hoy` — listar eventos de hoy
- `/agenda` — listar próximos 7 días
- `qué tengo hoy` / `qué tengo mañana` — query natural

## Categorías

| Slug | Uso |
|---|---|
| `university` | Clases y materias de la universidad |
| `uni_exam` | Exámenes, parciales, finales, quizes (Haiku classifier si ambiguo) |
| `uni_assignment` | Tareas, entregas, reportes, proyectos universitarios (Haiku classifier si ambiguo) |
| `course_study` | Estudio de cursos |
| `personal_project` | Proyectos personales |
| `client_project` | Proyectos para clientes/empresas |
| `exercise` | Rutina de ejercicio |
| `other` | Otros |

## Notas

Walking skeleton — sin recurrencia ni propose/confirm en esta versión.
