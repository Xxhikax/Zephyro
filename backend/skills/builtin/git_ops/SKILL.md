---
name: git-ops
description: "Operaciones Git: status, log, diff, branch, add, commit, push, pull"
triggers:
  - "git "
requires:
  python:
    - git
provides:
  slash_commands:
    - name: "git status"
      description: "Estado del repositorio"
    - name: "git log"
      description: "Historial de commits"
    - name: "git diff"
      description: "Cambios sin commitear"
    - name: "git commit"
      description: "Crear commit"
---

# Git Operations Skill

Ejecuta operaciones Git directamente desde el chat.

## Uso

- `git status` — estado del repo
- `git log` / `git log -5` — historial
- `git diff` / `git diff --staged` — cambios
- `git branch` — listar ramas
- `git add .` — agregar archivos
- `git commit -m "mensaje"` — commitear
- `git push` / `git pull` — sincronizar
- `git show HEAD` — ver commit específico
