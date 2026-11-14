---
name: terminal
description: "Ejecuta comandos PowerShell con clasificacion de seguridad (READ/WRITE/DESTRUCTIVE)"
triggers:
  - "/run"
  - "/exec"
  - "/pwsh"
  - "ejecuta el comando"
  - "ejecutar el comando"
  - "corre el comando"
  - "corre este comando"
  - "powershell:"
  - "pwsh:"
  - "ps:"
requires:
  bins: [powershell]
provides:
  slash_commands:
    - name: "/run"
      description: "Ejecuta un comando PowerShell con guards de seguridad"
    - name: "/exec"
      description: "Alias de /run"
    - name: "/pwsh"
      description: "Ejecuta PowerShell con captura de output"
enabled: true
---

# Terminal Skill

Ejecuta comandos PowerShell de forma segura desde el chat de Zephyro.

## Modelo de seguridad (3 tiers)

| Tier | Comportamiento | Ejemplos |
|------|----------------|----------|
| READ | Ejecuta libre, log con request_id | `Get-Process`, `git status`, `python --version`, `ls` |
| WRITE | Pide aprobacion via WebSocket | `New-Item`, `git push`, `pip install`, `gh pr create` |
| DESTRUCTIVE | Bloqueado por defecto; require flag explicito + aprobacion | `Remove-Item -Recurse -Force`, `git push --force`, `Format-Volume`, `Stop-Computer` |

## Uso

- `/run Get-Process` — ejecuta un comando READ directamente
- `/run gh repo clone tiangolo/fastapi` — comando WRITE, pide aprobacion
- "ejecuta python --version" — lenguaje natural
- "corre git status en el repo" — lenguaje natural

## Restricciones

- NO ejecuta desde canales no-Tauri (Telegram/WhatsApp) si el comando es WRITE/DESTRUCTIVE
- Output cap en 100KB; comandos que exceden se truncan con marker
- Timeout default 60s; configurable hasta 600s para builds/clones
