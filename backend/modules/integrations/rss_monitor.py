# modules/integrations/rss_monitor.py
"""
Monitor de RSS feeds para Zephyro.
- Monitoreo de hasta 10 feeds en background
- Notificaciones via callback cuando hay nuevas entradas
- Persistencia de feeds configurados
- Cache de entradas ya vistas
"""
import asyncio
import json
import os
import time
import threading
from typing import Optional, List, Dict, Callable, Set
from pathlib import Path


class FeedEntry:
    """Representa una entrada de feed RSS/Atom."""
    def __init__(self, title: str, url: str, published: str, summary: str, feed_url: str):
        self.title = title
        self.url = url
        self.published = published
        self.summary = summary
        self.feed_url = feed_url

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "published": self.published,
            "summary": self.summary,
            "feed_url": self.feed_url,
        }


class RSSMonitor:
    """
    Monitor asíncrono de RSS/Atom feeds.

    Uso:
        monitor = RSSMonitor()
        monitor.add_feed("https://blog.python.org/feeds/posts/default")
        monitor.start(callback=lambda entries: print(f"Nuevas entradas: {len(entries)}"))

        # Obtener últimas entradas manualmente:
        entries = await monitor.get_latest("https://blog.python.org/feeds/posts/default")
    """

    MAX_FEEDS = 10
    CHECK_INTERVAL = 900  # 15 minutos
    STORAGE_FILE = "./storage/rss_feeds.json"

    def __init__(self, storage_path: str = None):
        self._storage_path = storage_path or self.STORAGE_FILE
        self._feeds: Dict[str, dict] = {}  # url → {title, last_checked, seen_ids}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._callback: Optional[Callable] = None
        self._feedparser_available: Optional[bool] = None
        self._load_state()

    # ------------------------------------------------------------------
    # Disponibilidad de feedparser
    # ------------------------------------------------------------------

    def _check_feedparser(self) -> bool:
        if self._feedparser_available is None:
            try:
                import feedparser  # noqa
                self._feedparser_available = True
            except ImportError:
                self._feedparser_available = False
                print("[RSSMonitor] feedparser no instalado. Instala con: pip install feedparser")
        return self._feedparser_available

    # ------------------------------------------------------------------
    # Persistencia de configuración
    # ------------------------------------------------------------------

    def _load_state(self):
        """Carga feeds guardados desde disco."""
        try:
            if os.path.exists(self._storage_path):
                with open(self._storage_path, "r") as f:
                    data = json.load(f)
                    self._feeds = data.get("feeds", {})
                    print(f"[RSSMonitor] {len(self._feeds)} feeds cargados.")
        except Exception as e:
            print(f"[RSSMonitor] Error cargando estado: {e}")
            self._feeds = {}

    def _save_state(self):
        """Guarda feeds configurados a disco."""
        try:
            os.makedirs(os.path.dirname(self._storage_path), exist_ok=True)
            temp = self._storage_path + ".tmp"
            with open(temp, "w") as f:
                json.dump({"feeds": self._feeds}, f, indent=2)
            os.replace(temp, self._storage_path)
        except Exception as e:
            print(f"[RSSMonitor] Error guardando estado: {e}")

    # ------------------------------------------------------------------
    # Gestión de feeds
    # ------------------------------------------------------------------

    def add_feed(self, url: str, name: str = "") -> bool:
        """
        Agrega un feed para monitorear.

        Args:
            url: URL del feed RSS/Atom
            name: Nombre personalizado

        Returns:
            True si se agregó correctamente
        """
        if len(self._feeds) >= self.MAX_FEEDS:
            print(f"[RSSMonitor] Límite de {self.MAX_FEEDS} feeds alcanzado.")
            return False

        if url in self._feeds:
            print(f"[RSSMonitor] Feed ya existe: {url}")
            return True

        self._feeds[url] = {
            "name": name or url,
            "added_at": time.time(),
            "last_checked": 0,
            "seen_ids": [],
            "entry_count": 0,
        }
        self._save_state()
        print(f"[RSSMonitor] Feed agregado: {url}")
        return True

    def remove_feed(self, url: str) -> bool:
        """Elimina un feed del monitoreo."""
        if url in self._feeds:
            del self._feeds[url]
            self._save_state()
            return True
        return False

    def list_feeds(self) -> List[dict]:
        """Retorna lista de feeds configurados."""
        result = []
        for url, info in self._feeds.items():
            result.append({
                "url": url,
                "name": info.get("name", url),
                "last_checked": info.get("last_checked", 0),
                "entry_count": info.get("entry_count", 0),
            })
        return result

    # ------------------------------------------------------------------
    # Lectura de feeds
    # ------------------------------------------------------------------

    async def fetch_feed(self, url: str, max_entries: int = 10) -> List[FeedEntry]:
        """
        Lee un feed RSS/Atom y retorna sus entradas.

        Args:
            url: URL del feed
            max_entries: Número máximo de entradas a retornar

        Returns:
            Lista de FeedEntry
        """
        if not self._check_feedparser():
            return []

        try:
            import feedparser

            def _sync_fetch():
                feed = feedparser.parse(url)
                return feed

            loop = asyncio.get_event_loop()
            feed = await loop.run_in_executor(None, _sync_fetch)

            if feed.bozo and not feed.entries:
                print(f"[RSSMonitor] Error parseando feed {url}: {feed.bozo_exception}")
                return []

            entries = []
            for entry in feed.entries[:max_entries]:
                published = ""
                if hasattr(entry, "published"):
                    published = entry.published
                elif hasattr(entry, "updated"):
                    published = entry.updated

                summary = ""
                if hasattr(entry, "summary"):
                    summary = entry.summary[:500]
                elif hasattr(entry, "description"):
                    summary = entry.description[:500]

                entries.append(FeedEntry(
                    title=getattr(entry, "title", "Sin título"),
                    url=getattr(entry, "link", url),
                    published=published,
                    summary=summary,
                    feed_url=url,
                ))

            return entries

        except Exception as e:
            print(f"[RSSMonitor] Error leyendo feed {url}: {e}")
            return []

    async def get_latest(self, url: str, max_entries: int = 5) -> str:
        """
        Obtiene las últimas entradas de un feed formateadas.

        Args:
            url: URL del feed (o nombre si se configuró)
            max_entries: Número de entradas

        Returns:
            String formateado con las entradas
        """
        # Resolver por nombre si no es URL
        if not url.startswith("http"):
            for feed_url, info in self._feeds.items():
                if info.get("name", "").lower() == url.lower():
                    url = feed_url
                    break

        entries = await self.fetch_feed(url, max_entries)

        if not entries:
            return f"No pude obtener entradas del feed: {url}"

        feed_name = self._feeds.get(url, {}).get("name", url)
        lines = [f"**{feed_name}** - Últimas {len(entries)} entradas:\n"]

        for i, entry in enumerate(entries, 1):
            lines.append(f"{i}. **{entry.title}**")
            if entry.published:
                lines.append(f"   📅 {entry.published}")
            if entry.summary:
                # Limpiar HTML básico del summary
                import re
                clean_summary = re.sub(r"<[^>]+>", "", entry.summary).strip()
                lines.append(f"   {clean_summary[:200]}...")
            lines.append(f"   🔗 {entry.url}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Monitoreo en background
    # ------------------------------------------------------------------

    async def _check_all_feeds(self):
        """Revisa todos los feeds y detecta nuevas entradas."""
        new_entries = []

        for url, info in list(self._feeds.items()):
            try:
                entries = await self.fetch_feed(url, max_entries=20)

                if not entries:
                    continue

                # Detectar nuevas entradas (no vistas antes)
                seen_ids: Set[str] = set(info.get("seen_ids", []))
                feed_new = []

                for entry in entries:
                    entry_id = entry.url or entry.title
                    if entry_id not in seen_ids:
                        feed_new.append(entry)
                        seen_ids.add(entry_id)

                if feed_new:
                    new_entries.extend(feed_new)
                    print(f"[RSSMonitor] {len(feed_new)} nuevas entradas en {url}")

                # Actualizar estado del feed
                self._feeds[url]["last_checked"] = time.time()
                self._feeds[url]["entry_count"] = len(entries)
                self._feeds[url]["seen_ids"] = list(seen_ids)[-500:]  # Limitar a últimas 500

            except Exception as e:
                print(f"[RSSMonitor] Error revisando {url}: {e}")

        if new_entries and self._callback:
            try:
                self._callback(new_entries)
            except Exception as e:
                print(f"[RSSMonitor] Error en callback: {e}")

        self._save_state()
        return new_entries

    async def _monitor_loop(self):
        """Loop principal de monitoreo."""
        print(f"[RSSMonitor] Monitor iniciado. Revisando cada {self.CHECK_INTERVAL}s")
        while self._running:
            try:
                await self._check_all_feeds()
            except Exception as e:
                print(f"[RSSMonitor] Error en monitor loop: {e}")

            # Esperar con check periódico para poder detener
            for _ in range(self.CHECK_INTERVAL):
                if not self._running:
                    break
                await asyncio.sleep(1)

    def start(self, callback: Optional[Callable] = None):
        """
        Inicia el monitoreo en background.

        Args:
            callback: Función llamada con lista de FeedEntry cuando hay nuevas entradas
        """
        if self._running:
            print("[RSSMonitor] Ya está corriendo.")
            return

        self._running = True
        self._callback = callback

        # Crear task async
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._task = asyncio.create_task(self._monitor_loop())
            else:
                threading.Thread(
                    target=lambda: asyncio.run(self._monitor_loop()),
                    daemon=True
                ).start()
        except Exception as e:
            print(f"[RSSMonitor] Error iniciando monitor: {e}")

    def stop(self):
        """Detiene el monitoreo."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        print("[RSSMonitor] Monitor detenido.")

    def get_status(self) -> dict:
        """Retorna estado del monitor."""
        return {
            "running": self._running,
            "feeds_count": len(self._feeds),
            "feeds": self.list_feeds(),
            "feedparser_available": self._check_feedparser(),
        }
