---
name: github
description: "Operaciones de GitHub via gh CLI: clonar repos, ver PRs, listar issues, buscar y estado de CI/CD (workflow runs, builds fallidos)"
triggers:
  - "/gh"
  - "/github"
  - "/repo"
  - "/pr"
  - "/issue"
  - "/ci"
  - "/runs"
  - "estado del ci"
  - "cómo va el ci"
  - "como va el ci"
  - "cómo va el build"
  - "como va el build"
  - "estado del build"
  - "falló el build"
  - "fallo el build"
  - "por qué falló el build"
  - "por que fallo el build"
  - "estado del pipeline"
  - "pasaron los tests en github"
  - "instala el repo"
  - "instalar el repo"
  - "clona el repo"
  - "clonar el repo"
  - "busca repo"
  - "busca repositorios"
  - "ver pr"
  - "ver issue"
requires:
  bins: []
provides:
  slash_commands:
    - name: "/gh repo view OWNER/REPO"
      description: "Info de un repositorio"
    - name: "/gh repo clone OWNER/REPO"
      description: "Clona un repo a D:\\zephyro_repos\\"
    - name: "/gh pr view N"
      description: "Detalles de un PR (titulo, body, comments)"
    - name: "/gh pr list"
      description: "Lista PRs del repo actual"
    - name: "/gh issue list"
      description: "Lista issues del repo actual"
    - name: "/gh search QUERY"
      description: "Busca repositorios"
    - name: "/ci"
      description: "Últimas corridas de CI del repo actual (status + conclusión)"
    - name: "/ci fail"
      description: "Por qué falló la última corrida: jobs fallidos + tail del log"
    - name: "/gh run view N"
      description: "Detalle de una corrida de workflow"
enabled: true
---

# GitHub Skill

Wrapper sobre el binario `gh` (GitHub CLI). Hereda la sesion autenticada
del usuario (no maneja API keys propias).

## Modo de seguridad

- **READ ops** (`view`, `list`, `search`) ejecutan libres
- **WRITE ops** (`clone`, `create`, `merge`) pasan por el TerminalController
  → classifier las marca como WRITE → pide aprobacion

## CI/CD (read-only)

`/ci` → `gh run list`, `/ci fail` → `gh run view --log-failed`, `/gh run view N`
→ detalle con jobs. Son READ para `core/command_classifier.py` (su patron de
WRITE para `gh` cubre `workflow run`, no `run list`/`run view`), asi que corren
sin aprobacion. **A proposito NO se expone `gh run rerun` ni `workflow run`**:
disparar CI es una accion de escritura y esta skill mantiene CI/CD en solo
lectura.

## Workspace de repos

Cuando cloneas un repo, va a `D:\zephyro_repos\<repo>` por defecto. Si D:
no esta disponible, fallback a `~/zephyro_workspace/repos/`. Cada clon
se registra en `_index.json` con metadata.

## Auto-deteccion del binario

Si `gh` no esta en PATH, busca en:
- `C:\Program Files\GitHub CLI\gh.exe`
- `~\scoop\shims\gh.exe`
- `C:\ProgramData\chocolatey\bin\gh.exe`
