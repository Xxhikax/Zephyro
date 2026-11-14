# modules/filesystem/git_ops.py
"""
Operaciones Git seguras para Zephyro.
- Read-only: status, log, diff, branch, show
- Safe writes: add, commit (con confirmación)
- Dangerous: push, pull (con advertencia)
- Blacklist: main/master push sin doble confirmación
"""
import os
import asyncio
from typing import Optional, Tuple, List
from pathlib import Path


class GitOps:
    """
    Interfaz segura para operaciones Git.

    Uso:
        git = GitOps("/home/brunoq/mi_proyecto")
        success, msg = git.status()
        success, msg = git.diff()
        success, msg = git.add(["archivo.py"])
        success, msg = git.commit("Fix bug #123")
        success, msg = git.push()  # Requiere confirmación
    """

    def __init__(self, repo_path: str = None):
        self.repo_path = repo_path or os.getcwd()
        self._gitpython_available: Optional[bool] = None

    # ------------------------------------------------------------------
    # Verificaciones
    # ------------------------------------------------------------------

    def _check_gitpython(self) -> bool:
        """Verifica si GitPython está instalado."""
        if self._gitpython_available is None:
            try:
                import git  # noqa
                self._gitpython_available = True
            except ImportError:
                self._gitpython_available = False
                print("[GitOps] GitPython no instalado. Usando subprocess fallback.")
        return self._gitpython_available

    def _find_repo(self, path: str = None) -> Optional[str]:
        """Busca el repo git más cercano en la jerarquía de directorios."""
        target = path or self.repo_path
        current = Path(target).resolve()
        while current != current.parent:
            if (current / ".git").exists():
                return str(current)
            current = current.parent
        return None

    def is_git_repo(self, path: str = None) -> bool:
        """Verifica si el path está dentro de un repo git."""
        return self._find_repo(path) is not None

    async def _run_git(self, *args, cwd: str = None) -> Tuple[bool, str]:
        """Ejecuta un comando git y retorna (success, output)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                cwd=cwd or self.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode("utf-8", errors="replace").strip()
            err_output = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode == 0:
                return True, output
            else:
                return False, err_output or output
        except asyncio.TimeoutError:
            return False, "Comando git tardó más de 30 segundos."
        except FileNotFoundError:
            return False, "Git no está instalado en el sistema."
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------
    # READ-ONLY operations
    # ------------------------------------------------------------------

    async def status(self, path: str = None) -> Tuple[bool, str]:
        """Muestra el estado del repositorio."""
        repo = self._find_repo(path)
        if not repo:
            return False, "No se encontró ningún repositorio Git en el path actual o superiores."

        success, output = await self._run_git("status", cwd=repo)
        if success:
            return True, f"**Git Status** ({repo})\n\n```\n{output}\n```"
        return False, f"Error: {output}"

    async def log(self, path: str = None, n: int = 10) -> Tuple[bool, str]:
        """Muestra el historial de commits."""
        repo = self._find_repo(path)
        if not repo:
            return False, "No se encontró ningún repositorio Git."

        success, output = await self._run_git(
            "log", "--oneline", f"-{n}",
            "--pretty=format:%h %ad %s (%an)",
            "--date=short",
            cwd=repo
        )
        if success:
            return True, f"**Git Log** (últimos {n} commits)\n\n```\n{output}\n```"
        return False, f"Error: {output}"

    async def diff(self, path: str = None, staged: bool = False) -> Tuple[bool, str]:
        """Muestra los cambios del working tree o staged."""
        repo = self._find_repo(path)
        if not repo:
            return False, "No se encontró ningún repositorio Git."

        args = ["diff"]
        if staged:
            args.append("--staged")
        args.extend(["--stat", "--no-color"])

        success, output = await self._run_git(*args, cwd=repo)

        if success:
            if not output:
                return True, "No hay cambios" + (" staged" if staged else " sin stagear") + "."
            return True, f"**Git Diff** ({'staged' if staged else 'unstaged'})\n\n```\n{output}\n```"
        return False, f"Error: {output}"

    async def branch(self, path: str = None) -> Tuple[bool, str]:
        """Lista las ramas disponibles."""
        repo = self._find_repo(path)
        if not repo:
            return False, "No se encontró ningún repositorio Git."

        success, output = await self._run_git("branch", "-a", "--color=never", cwd=repo)
        if success:
            return True, f"**Git Branches**\n\n```\n{output}\n```"
        return False, f"Error: {output}"

    async def show(self, commit_hash: str = "HEAD", path: str = None) -> Tuple[bool, str]:
        """Muestra los detalles de un commit."""
        repo = self._find_repo(path)
        if not repo:
            return False, "No se encontró ningún repositorio Git."

        success, output = await self._run_git(
            "show", "--stat", "--no-color", commit_hash, cwd=repo
        )
        if success:
            return True, f"**Git Show {commit_hash}**\n\n```\n{output[:3000]}\n```"
        return False, f"Error: {output}"

    async def get_current_branch(self, path: str = None) -> str:
        """Obtiene el nombre de la rama actual."""
        repo = self._find_repo(path)
        if not repo:
            return "unknown"
        success, output = await self._run_git("branch", "--show-current", cwd=repo)
        return output.strip() if success else "unknown"

    # ------------------------------------------------------------------
    # SAFE WRITE operations (requieren repo existente)
    # ------------------------------------------------------------------

    async def add(self, files: List[str], path: str = None) -> Tuple[bool, str]:
        """
        Agrega archivos al staging area.

        Args:
            files: Lista de archivos o ['.'] para todos
            path: Ruta del repo

        Returns:
            (success, message)
        """
        repo = self._find_repo(path)
        if not repo:
            return False, "No se encontró ningún repositorio Git."

        if not files:
            return False, "No se especificaron archivos para agregar."

        args = ["add"] + files
        success, output = await self._run_git(*args, cwd=repo)

        if success:
            # Mostrar qué se agregó
            _, status_out = await self._run_git("status", "--short", cwd=repo)
            return True, f"✅ Archivos agregados al staging.\n\n```\n{status_out}\n```"
        return False, f"Error en git add: {output}"

    async def commit(self, message: str, path: str = None) -> Tuple[bool, str]:
        """
        Crea un commit con el mensaje dado.

        Args:
            message: Mensaje del commit
            path: Ruta del repo

        Returns:
            (success, message)
        """
        repo = self._find_repo(path)
        if not repo:
            return False, "No se encontró ningún repositorio Git."

        if not message or len(message.strip()) < 3:
            return False, "El mensaje del commit debe tener al menos 3 caracteres."

        success, output = await self._run_git(
            "commit", "-m", message.strip(), cwd=repo
        )

        if success:
            return True, f"✅ Commit creado:\n\n```\n{output}\n```"
        else:
            if "nothing to commit" in output:
                return False, "No hay cambios para commitear. Usa `git add` primero."
            return False, f"Error en git commit: {output}"

    # ------------------------------------------------------------------
    # DANGEROUS operations (warnings prominentes)
    # ------------------------------------------------------------------

    async def push(
        self, remote: str = "origin", branch: str = None, path: str = None,
        force: bool = False
    ) -> Tuple[bool, str]:
        """
        Push al remote. REQUIERE confirmación externa antes de llamar.

        Args:
            remote: Remote name (default: origin)
            branch: Branch a pushear (default: current)
            path: Ruta del repo
            force: Force push (muy peligroso)

        Returns:
            (success, message)
        """
        repo = self._find_repo(path)
        if not repo:
            return False, "No se encontró ningún repositorio Git."

        current_branch = branch or await self.get_current_branch(repo)

        # Advertencia extra para main/master
        if current_branch in ("main", "master", "production", "prod") and not force:
            return False, (
                f"⚠️ Estás intentando hacer push a '{current_branch}'.\n"
                f"Esta operación requiere doble confirmación explícita.\n"
                f"Para confirmar, di exactamente: 'confirmo push a {current_branch}'"
            )

        args = ["push", remote]
        if branch:
            args.append(branch)
        if force:
            args.append("--force")

        success, output = await self._run_git(*args, cwd=repo)

        if success:
            return True, f"✅ Push exitoso a {remote}/{current_branch}\n\n```\n{output}\n```"
        return False, f"Error en git push: {output}"

    async def pull(self, remote: str = "origin", branch: str = None, path: str = None) -> Tuple[bool, str]:
        """Pull del remote."""
        repo = self._find_repo(path)
        if not repo:
            return False, "No se encontró ningún repositorio Git."

        args = ["pull", remote]
        if branch:
            args.append(branch)

        success, output = await self._run_git(*args, cwd=repo)

        if success:
            return True, f"✅ Pull exitoso\n\n```\n{output}\n```"
        return False, f"Error en git pull: {output}"

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    async def get_repo_info(self, path: str = None) -> dict:
        """Obtiene información completa del repositorio."""
        repo = self._find_repo(path)
        if not repo:
            return {"error": "No es un repositorio Git"}

        _, branch = await self._run_git("branch", "--show-current", cwd=repo)
        _, remote_url = await self._run_git("remote", "get-url", "origin", cwd=repo)
        _, commit_count = await self._run_git("rev-list", "--count", "HEAD", cwd=repo)
        _, last_commit = await self._run_git(
            "log", "-1", "--pretty=format:%h %s (%ar)", cwd=repo
        )

        return {
            "path": repo,
            "branch": branch.strip(),
            "remote": remote_url.strip(),
            "commit_count": commit_count.strip(),
            "last_commit": last_commit.strip(),
        }
