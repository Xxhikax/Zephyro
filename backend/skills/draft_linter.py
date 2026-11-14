"""
draft_linter — Linter estatico que valida un skill.py PROPUESTO antes de
guardarlo en `storage/skill_drafts/PENDING/`.

Bloquea drafts que contengan patrones peligrosos: subprocess directo,
os.system, eval/exec, abrir archivos fuera del workspace, etc.

La idea es que las skills auto-generadas se compongan SOLO llamando a
otras skills (TerminalSkill, GitHubSkill, etc.) cuyas barreras ya estan
en runtime — no que escriban su propia logica de subprocess.
"""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("zephyro.draft_linter")


# Imports/symbols prohibidos en drafts
_BANNED_IMPORTS = frozenset({
    "subprocess", "os", "shutil", "ctypes", "pty", "winreg",
    "_winapi", "fcntl", "resource", "signal",
})

_BANNED_FROM_OS = frozenset({
    "system", "popen", "spawnl", "spawnv", "execv", "execvp",
    "remove", "unlink", "rmdir", "removedirs", "rename", "replace",
    "chmod", "chown", "kill", "killpg",
})

# Funciones built-in peligrosas por nombre (heuristica)
_BANNED_BUILTINS = frozenset({
    "eval", "exec", "compile", "__import__", "open",
})

# Imports requeridos: el draft DEBE usar la API de skills, no inventar la propia
_REQUIRED_BASES = frozenset({"BaseSkill", "skills.base"})


@dataclass
class LintIssue:
    severity: str  # "error" | "warning"
    line: int
    col: int
    message: str

    def __str__(self) -> str:
        return f"L{self.line}:{self.col} [{self.severity}] {self.message}"


@dataclass
class LintResult:
    ok: bool
    errors: list[LintIssue] = field(default_factory=list)
    warnings: list[LintIssue] = field(default_factory=list)

    def all_issues(self) -> list[LintIssue]:
        return self.errors + self.warnings

    def summary(self) -> str:
        if self.ok:
            return f"OK ({len(self.warnings)} warnings)"
        return f"BLOCKED ({len(self.errors)} errores, {len(self.warnings)} warnings)"


def lint_draft(source: str, *, filename: str = "<draft>") -> LintResult:
    """
    Analiza el código fuente Python de un skill propuesto.
    Devuelve LintResult con `ok=False` si hay cualquier error.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return LintResult(
            ok=False,
            errors=[LintIssue(
                severity="error",
                line=exc.lineno or 0,
                col=exc.offset or 0,
                message=f"SyntaxError: {exc.msg}",
            )],
        )

    visitor = _LinterVisitor()
    visitor.visit(tree)

    # Verificación: debe heredar de BaseSkill
    if not visitor.has_base_skill_class:
        visitor.errors.append(LintIssue(
            severity="error", line=0, col=0,
            message="Draft debe contener una clase que herede de BaseSkill",
        ))

    return LintResult(
        ok=len(visitor.errors) == 0,
        errors=visitor.errors,
        warnings=visitor.warnings,
    )


class _LinterVisitor(ast.NodeVisitor):

    def __init__(self) -> None:
        self.errors: list[LintIssue] = []
        self.warnings: list[LintIssue] = []
        self.has_base_skill_class = False

    # ── imports ──────────────────────────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in _BANNED_IMPORTS:
                self.errors.append(LintIssue(
                    "error", node.lineno, node.col_offset,
                    f"import '{alias.name}' prohibido en drafts auto-generados",
                ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = (node.module or "").split(".")[0]
        if module in _BANNED_IMPORTS:
            # Caso especial: from os import getenv, path está OK.
            allowed_from_os = {"getenv", "environ", "path", "sep", "linesep"}
            for alias in node.names:
                if module == "os" and alias.name in allowed_from_os:
                    continue
                if module == "os" and alias.name in _BANNED_FROM_OS:
                    self.errors.append(LintIssue(
                        "error", node.lineno, node.col_offset,
                        f"from os import '{alias.name}' prohibido (use TerminalSkill)",
                    ))
                else:
                    self.errors.append(LintIssue(
                        "error", node.lineno, node.col_offset,
                        f"from {module} import '{alias.name}' prohibido en drafts",
                    ))
        self.generic_visit(node)

    # ── llamadas peligrosas ──────────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        name = self._call_name(node)
        if name in _BANNED_BUILTINS:
            self.errors.append(LintIssue(
                "error", node.lineno, node.col_offset,
                f"llamada a '{name}' prohibida en drafts",
            ))
        # Detectar `os.system(...)`, `subprocess.run(...)`, etc.
        if isinstance(node.func, ast.Attribute):
            attr_chain = self._attr_chain(node.func)
            if attr_chain and attr_chain[0] in _BANNED_IMPORTS:
                self.errors.append(LintIssue(
                    "error", node.lineno, node.col_offset,
                    f"llamada a {'.'.join(attr_chain)}() prohibida — "
                    f"usá TerminalSkill o llamá a otra skill",
                ))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for base in node.bases:
            base_name = self._call_name(base) if isinstance(base, ast.Call) else (
                base.id if isinstance(base, ast.Name)
                else base.attr if isinstance(base, ast.Attribute)
                else None
            )
            if base_name and base_name in _REQUIRED_BASES:
                self.has_base_skill_class = True
        self.generic_visit(node)

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _call_name(node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Call):
            return _LinterVisitor._call_name(node.func)
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    @staticmethod
    def _attr_chain(node: ast.Attribute) -> list[str]:
        chain: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            chain.append(cur.id)
        chain.reverse()
        return chain
