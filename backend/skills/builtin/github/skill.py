"""GitHub Skill — wrapper de `gh` CLI con clonado a D:\\zephyro_repos."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.request_context import RequestContext
from modules.code_analysis import binary_finder
from modules.automation.terminal_controller import get_terminal_controller
from skills.base import BaseSkill

log = logging.getLogger("zephyro.github")


# Acepta owner/repo o URL completa
_REPO_RE = re.compile(
    r"(?:https?://github\.com/)?"
    r"(?P<owner>[A-Za-z0-9][A-Za-z0-9\-_.]*?)"
    r"/"
    r"(?P<repo>[A-Za-z0-9][A-Za-z0-9\-_.]*?)"
    r"(?:\.git)?(?:/|$|\s)"
)


# ── CI/CD (read-only) ────────────────────────────────────────────────────────

# "/ci fail", "por qué falló el build" → detalle del último run fallido.
_CI_FAIL_RE = re.compile(
    r"^/(?:gh\s+)?ci\s+(?:fail|falla|fallo|failed)\b|"
    r"por\s+qu[eé]\s+fall[oó]\s+(?:el\s+)?(?:build|ci|pipeline|workflow)|"
    r"fall[oó]\s+(?:el\s+)?(?:build|ci|pipeline)",
    re.IGNORECASE,
)

# "/ci", "/runs", "cómo va el build", "pasaron los tests en github"
_CI_LIST_RE = re.compile(
    r"^/(?:gh\s+)?(?:ci|runs)\b|^/gh\s+run\s+list\b|"
    r"(?:c[oó]mo\s+va|estado\s+de[l]?)\s+(?:el\s+)?(?:ci|build|pipeline)|"
    r"pasaron\s+los\s+tests\s+en\s+github|corridas\s+de\s+ci",
    re.IGNORECASE,
)

_CI_VIEW_RE = re.compile(r"/(?:gh\s+)?run\s+view\s+(\d+)", re.IGNORECASE)

_RUN_STATE_ICON = {
    "success": "✓",
    "failure": "✗",
    "cancelled": "-",
    "skipped": "-",
    "startup_failure": "✗",
    "timed_out": "✗",
}


def _relative_age(iso_ts: str) -> str:
    """'2026-09-27T12:00:00Z' → 'hace 3h'. Devuelve '?' si no parsea."""
    if not iso_ts:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    delta = datetime.now(timezone.utc) - dt
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "ahora"
    if mins < 60:
        return f"hace {mins}m"
    if mins < 1440:
        return f"hace {mins // 60}h"
    return f"hace {mins // 1440}d"


class GitHubSkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._gh_path: Optional[str] = None

    async def on_load(self) -> None:
        self._gh_path = binary_finder.find("gh")
        if self._gh_path is None:
            log.warning("[github] gh CLI no encontrado — skill deshabilitada")
            self.enabled = False
        else:
            log.info("[github] gh = %s", self._gh_path)

    def matches_command(self, user_input: str) -> bool:
        if super().matches_command(user_input):
            return True
        lower = user_input.lower().strip()
        # CI/CD en lenguaje natural: "cómo va el build", "por qué falló el ci"
        if _CI_FAIL_RE.search(lower) or _CI_LIST_RE.search(lower) or _CI_VIEW_RE.search(lower):
            return True
        # Lenguaje natural: "instalá el repo X de Y"
        return any(p in lower for p in (
            "instala el repo", "instalar el repo", "clona el repo",
            "clonar el repo", "busca repositorios", "busca repos",
        ))

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.enabled or self._gh_path is None:
            return (
                "GitHub Skill no disponible. Verificá `gh auth status` o "
                "instalá GitHub CLI."
            )
        if not self.matches_command(user_input):
            return None

        text = user_input.strip()
        action, args = self._parse_action(text)

        if action == "clone":
            return await self._clone(args, ctx)
        if action == "view_repo":
            return await self._view_repo(args, ctx)
        if action == "view_pr":
            return await self._view_pr(args, ctx)
        if action == "list_pr":
            return await self._list_pr(ctx)
        if action == "list_issue":
            return await self._list_issue(ctx)
        if action == "search":
            return await self._search(args, ctx)
        if action == "list_runs":
            return await self._list_runs(ctx)
        if action == "view_run":
            return await self._view_run(args, ctx)
        if action == "why_failed":
            return await self._why_failed(ctx)

        return (
            "No reconocí la operación. Usá:\n"
            "- `/gh repo view OWNER/REPO`\n"
            "- `/gh repo clone OWNER/REPO`\n"
            "- `/gh pr view N`\n"
            "- `/gh pr list` / `/gh issue list`\n"
            "- `/gh search <query>`\n"
            "- `/ci` (corridas de CI) / `/ci fail` (por qué falló)"
        )

    # ── Parsing ──────────────────────────────────────────────────────

    def _parse_action(self, text: str) -> tuple[str, str]:
        lower = text.lower()

        # CI/CD primero: "/ci fail" antes que "/ci" (el segundo también matchea).
        if _CI_FAIL_RE.search(lower):
            return ("why_failed", "")
        m = _CI_VIEW_RE.search(lower)
        if m:
            return ("view_run", m.group(1))
        if _CI_LIST_RE.search(lower):
            return ("list_runs", "")

        if "/gh search " in lower or "busca repo" in lower or "busca repositorios" in lower:
            q = re.sub(r"^.*?(?:search|repos|repositorios)\s+", "", text, flags=re.I)
            return ("search", q.strip())

        if "/pr list" in lower or "/gh pr list" in lower:
            return ("list_pr", "")
        if "/issue list" in lower or "/gh issue list" in lower:
            return ("list_issue", "")

        m = re.search(r"/(?:gh\s+)?pr\s+view\s+(\d+)", lower)
        if m:
            return ("view_pr", m.group(1))
        m = re.search(r"/(?:gh\s+)?repo\s+view\s+(\S+)", lower)
        if m:
            return ("view_repo", m.group(1))

        # Clone: cualquier mención de repo en input con verbo de instalación
        if any(w in lower for w in ("clone", "clona", "clonar", "instala", "instalar")):
            m = _REPO_RE.search(text + " ")
            if m:
                return ("clone", f"{m.group('owner')}/{m.group('repo')}")

        # /gh repo clone OWNER/REPO
        m = re.search(r"/(?:gh\s+)?repo\s+clone\s+(\S+)", lower)
        if m:
            return ("clone", m.group(1))

        return ("", "")

    # ── Operations ───────────────────────────────────────────────────

    async def _view_repo(self, slug: str, ctx: RequestContext) -> str:
        ctrl = get_terminal_controller()
        if ctrl is None:
            return "TerminalController no disponible."
        cmd = (
            f'& "{self._gh_path}" repo view {slug} '
            f'--json name,description,defaultBranchRef,stargazerCount,'
            f'primaryLanguage,url,isArchived'
        )
        result = await ctrl.run(cmd, ctx, timeout=30.0)
        if not result.ok:
            return f"⛔ {result.error or result.stderr or 'gh falló.'}"
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return f"```\n{result.stdout}\n```"

        return (
            f"**{data.get('name')}** · {data.get('primaryLanguage', {}).get('name', '?')}\n"
            f"⭐ {data.get('stargazerCount', 0)} · "
            f"branch: `{(data.get('defaultBranchRef') or {}).get('name', '?')}`\n\n"
            f"{data.get('description') or '_(sin descripción)_'}\n\n"
            f"{data.get('url')}"
        )

    async def _view_pr(self, n: str, ctx: RequestContext) -> str:
        ctrl = get_terminal_controller()
        if ctrl is None:
            return "TerminalController no disponible."
        cmd = f'& "{self._gh_path}" pr view {n} --json title,body,state,author,number'
        result = await ctrl.run(cmd, ctx, timeout=30.0)
        if not result.ok:
            return f"⛔ {result.error or result.stderr or 'gh falló.'}"
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return f"```\n{result.stdout}\n```"
        body = (data.get("body") or "").strip()[:1500]
        return (
            f"**PR #{data.get('number')}** · {data.get('state')} · "
            f"@{data.get('author', {}).get('login', '?')}\n"
            f"### {data.get('title')}\n\n{body}"
        )

    async def _list_pr(self, ctx: RequestContext) -> str:
        ctrl = get_terminal_controller()
        if ctrl is None:
            return "TerminalController no disponible."
        cmd = f'& "{self._gh_path}" pr list --json number,title,state,author --limit 20'
        result = await ctrl.run(cmd, ctx, timeout=30.0)
        if not result.ok:
            return f"⛔ {result.error or result.stderr}"
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return f"```\n{result.stdout}\n```"
        if not data:
            return "Sin PRs abiertas."
        lines = [
            f"#{p['number']} · {p['state']} · @{p.get('author', {}).get('login', '?')} · {p['title']}"
            for p in data
        ]
        return "**PRs abiertas:**\n" + "\n".join(lines)

    async def _list_issue(self, ctx: RequestContext) -> str:
        ctrl = get_terminal_controller()
        if ctrl is None:
            return "TerminalController no disponible."
        cmd = f'& "{self._gh_path}" issue list --json number,title,state --limit 20'
        result = await ctrl.run(cmd, ctx, timeout=30.0)
        if not result.ok:
            return f"⛔ {result.error or result.stderr}"
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return f"```\n{result.stdout}\n```"
        if not data:
            return "Sin issues abiertos."
        return "**Issues:**\n" + "\n".join(
            f"#{i['number']} · {i['state']} · {i['title']}" for i in data
        )

    async def _search(self, query: str, ctx: RequestContext) -> str:
        if not query:
            return "Especificá una query: `/gh search fastapi`"
        ctrl = get_terminal_controller()
        if ctrl is None:
            return "TerminalController no disponible."
        # `gh search repos` requiere quoting
        safe_query = query.replace('"', '\\"')
        cmd = (
            f'& "{self._gh_path}" search repos "{safe_query}" '
            f'--json fullName,description,stargazersCount --limit 10'
        )
        result = await ctrl.run(cmd, ctx, timeout=30.0)
        if not result.ok:
            return f"⛔ {result.error or result.stderr}"
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return f"```\n{result.stdout}\n```"
        if not data:
            return f"Sin resultados para `{query}`."
        return f"**Resultados para `{query}`:**\n" + "\n".join(
            f"⭐ {r.get('stargazersCount', 0):>5} · {r['fullName']}\n  {r.get('description', '')}"
            for r in data[:10]
        )

    # ── CI/CD (read-only) ────────────────────────────────────────────

    async def _run_gh_json(self, cmd: str, ctx: RequestContext, timeout: float = 30.0):
        """Ejecuta un `gh ... --json` y devuelve (data, error_msg)."""
        ctrl = get_terminal_controller()
        if ctrl is None:
            return None, "TerminalController no disponible."
        result = await ctrl.run(cmd, ctx, timeout=timeout)
        if not result.ok:
            err = (result.error or result.stderr or "gh falló.").strip()
            # Repo sin workflows o sin remote: no es un error del sistema.
            if "no workflow" in err.lower() or "not a git repository" in err.lower():
                return None, f"Sin CI configurado acá: {err}"
            return None, f"⛔ {err}"
        try:
            return json.loads(result.stdout), None
        except json.JSONDecodeError:
            return None, f"```\n{result.stdout[:800]}\n```"

    async def _list_runs(self, ctx: RequestContext) -> str:
        cmd = (
            f'& "{self._gh_path}" run list --limit 10 '
            f'--json databaseId,workflowName,displayTitle,status,conclusion,'
            f'headBranch,createdAt,event'
        )
        data, err = await self._run_gh_json(cmd, ctx)
        if err:
            return err
        if not data:
            return "Sin corridas de CI en este repo."

        lines = ["**CI — últimas corridas:**"]
        for r in data:
            conclusion = r.get("conclusion") or r.get("status") or "?"
            icon = _RUN_STATE_ICON.get(conclusion, "…")
            lines.append(
                f"{icon} #{r.get('databaseId')} · {r.get('workflowName', '?')} · "
                f"`{r.get('headBranch', '?')}` · {conclusion} · "
                f"{_relative_age(r.get('createdAt', ''))} · {r.get('displayTitle', '')[:60]}"
            )

        failed = [r for r in data if r.get("conclusion") == "failure"]
        if failed:
            lines.append(
                f"\nLa más reciente que falló es #{failed[0].get('databaseId')} — "
                f"mirá el detalle con `/ci fail`."
            )
        return "\n".join(lines)

    async def _view_run(self, run_id: str, ctx: RequestContext) -> str:
        cmd = (
            f'& "{self._gh_path}" run view {run_id} '
            f'--json databaseId,workflowName,displayTitle,status,conclusion,'
            f'headBranch,createdAt,jobs'
        )
        data, err = await self._run_gh_json(cmd, ctx)
        if err:
            return err
        if not data:
            return f"No encontré la corrida #{run_id}."
        return self._format_run_detail(data)

    def _format_run_detail(self, data: dict) -> str:
        conclusion = data.get("conclusion") or data.get("status") or "?"
        icon = _RUN_STATE_ICON.get(conclusion, "…")
        lines = [
            f"{icon} **Run #{data.get('databaseId')}** · {data.get('workflowName', '?')} · "
            f"`{data.get('headBranch', '?')}` · {conclusion} · "
            f"{_relative_age(data.get('createdAt', ''))}",
            f"{data.get('displayTitle', '')}",
        ]
        jobs = data.get("jobs") or []
        failed_jobs = [j for j in jobs if j.get("conclusion") == "failure"]
        if failed_jobs:
            lines.append("\n**Jobs fallidos:**")
            for j in failed_jobs:
                steps = [
                    s.get("name", "?") for s in (j.get("steps") or [])
                    if s.get("conclusion") == "failure"
                ]
                step_part = f" → paso: {', '.join(steps)}" if steps else ""
                lines.append(f"  ✗ {j.get('name', '?')}{step_part}")
        elif jobs:
            lines.append(f"\n{len(jobs)} job(s), ninguno fallido.")
        return "\n".join(lines)

    async def _why_failed(self, ctx: RequestContext) -> str:
        """Detalle + tail del log de la última corrida fallida."""
        list_cmd = (
            f'& "{self._gh_path}" run list --limit 10 --status failure '
            f'--json databaseId,workflowName,displayTitle,status,conclusion,'
            f'headBranch,createdAt'
        )
        runs, err = await self._run_gh_json(list_cmd, ctx)
        if err:
            return err
        if not runs:
            return "Ninguna corrida de CI falló últimamente."

        run_id = runs[0].get("databaseId")
        detail = await self._view_run(str(run_id), ctx)

        ctrl = get_terminal_controller()
        if ctrl is None:
            return detail
        log_cmd = f'& "{self._gh_path}" run view {run_id} --log-failed'
        result = await ctrl.run(log_cmd, ctx, timeout=60.0)
        if not result.ok or not result.stdout.strip():
            return detail

        # El log completo puede ser enorme: solo el tail, que es donde está el error.
        tail = "\n".join(result.stdout.rstrip().splitlines()[-25:])
        return f"{detail}\n\n**Log (últimas líneas):**\n```\n{tail[-1500:]}\n```"

    async def _clone(self, slug: str, ctx: RequestContext) -> str:
        if "/" not in slug:
            return f"Slug `{slug}` inválido. Esperado: `owner/repo`."

        ctrl = get_terminal_controller()
        if ctrl is None:
            return "TerminalController no disponible."

        repos_root = Path(os.getenv("ZEPHYRO_REPOS_ROOT", "D:/zephyro_repos"))
        try:
            repos_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            repos_root = Path.home() / "zephyro_workspace" / "repos"
            repos_root.mkdir(parents=True, exist_ok=True)

        repo_name = slug.split("/")[-1]
        target = repos_root / repo_name
        if target.exists():
            return f"`{target}` ya existe. Usá `git pull` desde ahí o eliminálo primero."

        cmd = f'& "{self._gh_path}" repo clone {slug} "{target}"'
        result = await ctrl.run(cmd, ctx, timeout=600.0)
        if not result.ok:
            return f"⛔ Clone falló: {result.error or result.stderr}"

        # Actualizar el índice
        self._update_index(repos_root, slug, target, ctx)

        return (
            f"✓ Clonado `{slug}` en `{target}` ({result.duration_ms}ms).\n"
            f"Output:\n```\n{result.stdout.rstrip()[:500]}\n```"
        )

    def _update_index(self, root: Path, slug: str, target: Path, ctx: RequestContext) -> None:
        idx_path = root / "_index.json"
        try:
            if idx_path.exists():
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
            else:
                idx = {"repos": []}
            idx["repos"] = [r for r in idx.get("repos", []) if r.get("path") != str(target)]
            idx["repos"].append({
                "slug": slug,
                "path": str(target),
                "cloned_at": time.time(),
                "cloned_by": ctx.user_name or "unknown",
                "request_id": ctx.request_id,
            })
            idx["updated_at"] = time.time()
            idx_path.write_text(
                json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            log.warning("[github] no pude actualizar _index.json: %s", e)
