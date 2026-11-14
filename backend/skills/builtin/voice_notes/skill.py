"""VoiceNotesSkill — captura rapida de ideas por voz.

Guarda notas en storage/voice_notes/ como archivos .md con frontmatter YAML
(compatible con Obsidian). Clasifica el tema con Haiku 4.5 y crea subcarpetas.
"""
import asyncio
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill

_VOICE_NOTES_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "storage" / "voice_notes"

_NOTE_TRIGGERS_RE = re.compile(
    r'^(nota[:\s]|apunt[aáe][:\s]|anot[aáe][\s:]|/nota)',
    re.IGNORECASE,
)

_SEARCH_TRIGGERS_RE = re.compile(
    r'(?:qu[eé] notas|notas sobre|busca(?:r)? notas|mis notas sobre|dame (?:mis |las )?notas|'
    r'busca en (?:mis |las )?notas)\s+(.+)',
    re.IGNORECASE,
)

_TOPICS = [
    "programacion",
    "ideas",
    "recordatorios",
    "universidad",
    "trabajo",
    "personal",
    "otros",
]

_ANALYZE_PROMPT = (
    "Analiza esta nota y responde SOLO con un JSON valido con dos campos:\n"
    "  - topic: una de [" + ", ".join(_TOPICS) + "]\n"
    "  - summary: titulo corto de 3 a 6 palabras (sin comillas, sin puntuacion)\n"
    "\n"
    "Nota: \"{note}\"\n\n"
    "JSON:"
)


class VoiceNotesSkill(BaseSkill):

    def __init__(self, metadata=None, skill_dir: Optional[Path] = None):
        super().__init__(metadata=metadata, skill_dir=skill_dir)
        self._cache: dict[str, tuple[float, str]] = {}
        _VOICE_NOTES_ROOT.mkdir(parents=True, exist_ok=True)

    def matches_command(self, user_input: str) -> bool:
        lower = user_input.lower().strip()
        if _NOTE_TRIGGERS_RE.match(lower):
            return True
        if _SEARCH_TRIGGERS_RE.search(lower):
            return True
        return False

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        lower = user_input.lower().strip()

        m = _SEARCH_TRIGGERS_RE.search(lower)
        if m:
            return self._handle_search(m.group(1).strip())

        return await self._handle_create(user_input)

    async def _handle_create(self, user_input: str) -> str:
        content = _NOTE_TRIGGERS_RE.sub("", user_input).strip()
        if not content:
            return "Que queres anotar?"

        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        dedup_key = f"vnote_{content_hash}"
        now = time.time()

        cached = self._cache.get(dedup_key)
        if cached:
            cached_at, cached_summary = cached
            if now - cached_at < 300:
                return f"Ya guarde eso hace poco: \"{cached_summary}\""

        topic, summary = await self._analyze_note(content)

        filepath = self._save_note(content, topic, summary)

        self._cache[dedup_key] = (now, summary)
        if len(self._cache) > 200:
            oldest = min(self._cache.keys(), key=lambda k: self._cache[k][0])
            del self._cache[oldest]

        return f"Guardado en {filepath.relative_to(_VOICE_NOTES_ROOT.parent)}"

    async def _analyze_note(self, content: str) -> tuple[str, str]:
        fallback = ("otros", content[:40].strip() or "nota")
        if self.llm_manager is None:
            return fallback

        prompt = _ANALYZE_PROMPT.format(note=content[:500])
        try:
            raw = await asyncio.to_thread(
                lambda: self.llm_manager.generate(
                    prompt, force_cloud=True, max_tokens=80,
                )
            )
            if not raw:
                return fallback

            text = raw.strip()
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return fallback

            data = json.loads(text[start:end + 1])
            topic = str(data.get("topic", "")).strip().lower().rstrip(".")
            summary = str(data.get("summary", "")).strip().strip('"').strip()

            chosen_topic = next((t for t in _TOPICS if t in topic), "otros")
            safe_summary = re.sub(r'[<>:"/\\|?*]', '', summary)[:60] or fallback[1]
            return chosen_topic, safe_summary
        except Exception:
            return fallback

    def _save_note(self, content: str, topic: str, summary: str) -> Path:
        topic_dir = _VOICE_NOTES_ROOT / topic
        topic_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        safe_summary = re.sub(r'[<>:"/\\|?*]', '', summary)[:50]
        ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts_file}_{safe_summary}.md"
        filepath = topic_dir / filename

        frontmatter = (
            f"---\n"
            f"topic: {topic}\n"
            f"date: {timestamp}\n"
            f"summary: \"{summary}\"\n"
            f"source: voice_note\n"
            f"---\n\n"
            f"{content}\n"
        )

        filepath.write_text(frontmatter, encoding="utf-8")
        return filepath

    def _handle_search(self, query: str) -> str:
        query_lower = query.lower()
        results = []

        if not _VOICE_NOTES_ROOT.exists():
            return f"No tengo notas todavia. Crea una con: 'nota: tu idea'."

        for md_file in _VOICE_NOTES_ROOT.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8")
                if query_lower in text.lower():
                    results.append((md_file, text))
            except Exception:
                continue

        if not results:
            return f"No encontre notas sobre \"{query}\"."

        lines = [f"Notas sobre \"{query}\" ({len(results)}):"]
        for filepath, text in results[:8]:
            rel = filepath.relative_to(_VOICE_NOTES_ROOT)
            frontmatter = ""
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1].strip()
            body = text.split("---", 2)[-1].strip() if text.startswith("---") else text
            preview = body[:100].replace("\n", " ")
            topic = rel.parts[0] if len(rel.parts) > 1 else "?"
            date_str = ""
            for line in frontmatter.split("\n"):
                if line.startswith("date:"):
                    date_str = line.replace("date:", "").strip()
                    break
            lines.append(f"  [{topic}] {rel.name} — {preview}...")

        return "\n".join(lines)
