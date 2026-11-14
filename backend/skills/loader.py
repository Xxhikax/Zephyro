"""
SkillLoader — Escanea directorios, parsea SKILL.md, importa skills, y opcionalmente
observa cambios para hot-reload sin reiniciar el servidor.
"""
import importlib
import importlib.util
import logging
import sys
import threading
import yaml
from pathlib import Path
from typing import Optional

from skills.base import BaseSkill, SkillMetadata
from skills.registry import SkillRegistry

log = logging.getLogger("zephyro.skill_loader")


class SkillLoader:
    """Carga skills desde uno o más directorios."""

    def __init__(self, skill_dirs: list[Path]):
        self.skill_dirs = skill_dirs
        self._lock = threading.RLock()  # Protege hot-reload
        self._watcher = None

    # ── Public API ────────────────────────────────────────────────────

    def load_into(self, registry: SkillRegistry) -> int:
        """
        Escanea todos los directorios, carga skills válidas, las registra.
        Retorna cantidad de skills cargadas.
        """
        count = 0
        for skill_dir in self.skill_dirs:
            if not skill_dir.exists():
                continue
            for entry in sorted(skill_dir.iterdir()):
                if not entry.is_dir() or entry.name.startswith("_"):
                    continue
                skill_md = entry / "SKILL.md"
                if not skill_md.exists():
                    # Antes era skip silencioso: un dir con skill.py pero sin
                    # SKILL.md no se cargaba y nadie se enteraba. Lo avisamos.
                    if (entry / "skill.py").exists():
                        log.warning(
                            "Skill '%s': tiene skill.py pero falta SKILL.md — "
                            "no se cargará (ver skills/SKILL_TEMPLATE.md)", entry.name)
                    continue
                skill = self._load_single(entry)
                if skill:
                    registry.register(skill)
                    count += 1
        return count

    def watch(self, registry: SkillRegistry) -> None:
        """
        Inicia un watcher en background que recarga skills cuando cambian.
        Requiere 'watchdog' instalado.
        """
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
        except ImportError:
            print("[SkillLoader] watchdog no instalado — hot-reload deshabilitado")
            return

        loader = self

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.is_directory:
                    return
                path = Path(event.src_path)
                if path.suffix in (".py", ".md"):
                    skill_dir = path.parent
                    if skill_dir.name in ("__pycache__",):
                        return
                    loader._reload_skill(skill_dir, registry)

            def on_created(self, event):
                self.on_modified(event)

        observer = Observer()
        handler = _Handler()
        for d in self.skill_dirs:
            if d.exists():
                observer.schedule(handler, str(d), recursive=True)
        observer.daemon = True
        observer.start()
        self._watcher = observer
        log.info("Hot-reload activo en %d directorios", len(self.skill_dirs))

    # ── Internal ──────────────────────────────────────────────────────

    def _load_single(self, entry: Path) -> Optional[BaseSkill]:
        """Carga una skill desde su directorio."""
        try:
            metadata = self._parse_skill_md(entry / "SKILL.md")
            if metadata is None:
                return None

            # Verificar requirements
            skill_stub = _RequirementChecker(metadata, entry)
            ok, missing = skill_stub.check_requirements()
            if not ok:
                log.warning("Skill %s: falta %s", metadata.name, ", ".join(missing))
                return None

            # Importar el módulo skill.py
            skill_py = entry / "skill.py"
            if not skill_py.exists():
                log.warning("Skill %s: sin skill.py", entry.name)
                return None

            module = self._import_module(entry.name, skill_py)
            if module is None:
                return None

            # Buscar la clase que hereda de BaseSkill
            skill_class = self._find_skill_class(module)
            if skill_class is None:
                log.warning("Skill %s: sin clase BaseSkill en skill.py", entry.name)
                return None

            instance = skill_class(metadata=metadata, skill_dir=entry)
            log.info("OK %s", metadata.name)
            return instance

        except Exception as e:
            log.error("FAIL %s: %s", entry.name, e)
            return None

    def _parse_skill_md(self, path: Path) -> Optional[SkillMetadata]:
        """Parsea frontmatter YAML del SKILL.md."""
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.startswith("---"):
                return None

            parts = raw.split("---", 2)
            if len(parts) < 3:
                return None

            data = yaml.safe_load(parts[1])
            if not data or "name" not in data:
                return None

            triggers = [str(t) for t in data.get("triggers", [])]
            if not triggers:
                # Sin triggers la skill se carga pero el registry no la rutea,
                # salvo que overridee matches_command (caso vision_query).
                log.warning(
                    "Skill '%s': triggers vacío — no será ruteada salvo que "
                    "overridee matches_command()", data["name"])

            requires = data.get("requires", {})
            if not isinstance(requires, dict):
                requires = {}

            provides = data.get("provides", {})
            if not isinstance(provides, dict):
                provides = {}

            return SkillMetadata(
                name=data["name"],
                description=data.get("description", ""),
                triggers=triggers,
                requires=requires,
                provides=provides,
                enabled=data.get("enabled", True),
                uses_tools=[str(t) for t in data.get("uses_tools", [])],
                risk_level=str(data.get("risk_level", "low")),
                auto_generated=bool(data.get("auto_generated", False)),
                source_envelope_ids=[
                    str(s) for s in data.get("source_envelope_ids", [])
                ],
            )
        except Exception as e:
            log.error("Error parseando %s: %s", path, e)
            return None

    def _import_module(self, name: str, path: Path):
        """Importa un módulo Python desde path."""
        module_name = f"skills._loaded_.{name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            return module
        except Exception as e:
            log.error("Import error %s: %s", name, e)
            return None

    def _find_skill_class(self, module) -> Optional[type]:
        """Encuentra la primera clase que hereda de BaseSkill en el módulo."""
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseSkill)
                and obj is not BaseSkill
            ):
                return obj
        return None

    def _reload_skill(self, skill_dir: Path, registry: SkillRegistry) -> None:
        """Recarga una skill específica (hot-reload)."""
        with self._lock:
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                return

            # Limpiar módulo cacheado
            module_name = f"skills._loaded_.{skill_dir.name}"
            if module_name in sys.modules:
                del sys.modules[module_name]

            skill = self._load_single(skill_dir)
            if skill:
                registry.register(skill)  # Reemplaza la existente
                log.info("Skill %s recargada", skill.name)


class _RequirementChecker(BaseSkill):
    """Stub para verificar requirements sin implementar handle()."""

    async def handle(self, user_input, ctx):
        return None
