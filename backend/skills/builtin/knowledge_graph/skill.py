"""Knowledge Graph skill — wrapper sobre modules/memory_extended/knowledge_graph.py"""
import json
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill


class KnowledgeGraphSkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._kg = None

    def _get_kg(self):
        if self._kg is None:
            from modules.memory_extended.knowledge_graph import KnowledgeGraph
            self._kg = KnowledgeGraph()
        return self._kg

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        try:
            kg = self._get_kg()
        except Exception as e:
            return f"Knowledge Graph no disponible: {e}"

        parts = user_input.strip().split(None, 1)
        subcmd = parts[1].strip() if len(parts) > 1 else ""

        if not subcmd or subcmd in ("stats", "estadísticas", "info"):
            return kg.get_stats()

        if subcmd.startswith("export"):
            data = kg.export_json()
            return f"Knowledge Graph exportado:\n```json\n{json.dumps(data, indent=2, ensure_ascii=False)[:2000]}\n```"

        # Query en lenguaje natural
        return kg.query(subcmd)
