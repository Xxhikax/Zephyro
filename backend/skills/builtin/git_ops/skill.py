"""Git Operations skill — wrapper sobre modules/filesystem/git_ops.py"""
import re
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill


class GitOpsSkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ops = None

    def _get_ops(self):
        if self._ops is None:
            from modules.filesystem.git_ops import GitOps
            self._ops = GitOps()
        return self._ops

    def matches_command(self, user_input: str) -> bool:
        lower = user_input.lower().strip()
        return bool(re.match(
            r'^git\s+(status|log|diff|branch|show|add|commit|push|pull)',
            lower
        ))

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        try:
            ops = self._get_ops()
        except Exception as e:
            return f"Operaciones Git no disponibles: {e}"

        words = user_input.lower().split()
        if len(words) < 2:
            return "Especifica un subcomando git: status, log, diff, branch, add, commit, push, pull"

        subcmd = words[1]

        if subcmd == "status":
            success, result = await ops.status()
        elif subcmd == "log":
            n = 10
            n_match = re.search(r'-(\d+)', user_input)
            if n_match:
                n = int(n_match.group(1))
            success, result = await ops.log(n=n)
        elif subcmd == "diff":
            staged = "--staged" in user_input or "--cached" in user_input
            success, result = await ops.diff(staged=staged)
        elif subcmd == "branch":
            success, result = await ops.branch()
        elif subcmd == "add":
            files_str = user_input.split("add", 1)[-1].strip()
            files = files_str.split() if files_str else ["."]
            success, result = await ops.add(files)
        elif subcmd == "commit":
            m_match = re.search(r'-m\s+["\']([^"\']+)["\']', user_input, re.IGNORECASE)
            if not m_match:
                m_match = re.search(r'commit\s+(.+)', user_input, re.IGNORECASE)
            if m_match:
                msg = m_match.group(1).strip()
                success, result = await ops.commit(msg)
            else:
                return "Especifica el mensaje: 'git commit -m \"mensaje del commit\"'"
        elif subcmd == "push":
            success, result = await ops.push()
        elif subcmd == "pull":
            success, result = await ops.pull()
        elif subcmd == "show":
            commit_hash = words[2] if len(words) > 2 else "HEAD"
            success, result = await ops.show(commit_hash)
        else:
            return f"Subcomando git no reconocido: '{subcmd}'"

        return result
