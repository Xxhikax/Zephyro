#!/usr/bin/env python3
"""
WebAutomationPlanner: Traduce intenciones de automatización web en secuencias de acciones atómicas.
"""
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class WebAutomationPlanner:
    """Planificador que convierte análisis de intención en planes ejecutables"""
    
    def __init__(self, selectors_path: str = "backend/modules/automation/selectors.json"):
        self.selectors_path = selectors_path
    
    def build_plan(self, analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Convierte análisis de intención en secuencia de acciones atómicas.
        
        Campos esperados en analysis:
        - automation_intent: NAVIGATE, SEARCH, INTERACT, PLAY_MEDIA
        - provider: youtube, youtube_music, generic
        - target: URL o aplicación
        - search_query: consulta de búsqueda
        - action: acción específica (click, type, etc.)
        
        Retorna lista de acciones con estructura:
        {
            "type": "navigate|search|click|type|wait|play_media|control_media",
            "params": dict,
            "provider": str (opcional)
        }
        """
        automation_intent = analysis.get("automation_intent")
        provider = analysis.get("provider", "generic")
        target = analysis.get("target", "")
        search_query = analysis.get("search_query", "")
        action = analysis.get("action", "")
        navigate_target = analysis.get("navigate_target")
        
        plan = []
        
        # Caso: PLAY_MEDIA (reproducir contenido en plataforma específica)
        if automation_intent == "PLAY_MEDIA":
            if provider == "youtube":
                plan = self._build_youtube_playback_plan(search_query or target)
            elif provider == "youtube_music":
                plan = self._build_youtube_music_playback_plan(search_query or target)
            else:
                # Fallback a búsqueda genérica
                plan = self._build_generic_search_plan(search_query or target)
        
        # Caso: SEARCH (búsqueda web)
        elif automation_intent == "SEARCH":
            plan = self._build_generic_search_plan(search_query or target)
        
        # Caso: NAVIGATE (navegar a URL)
        elif automation_intent == "NAVIGATE":
            # Determinar destino real: prioritize navigate_target, luego search_query si target es navegador
            destination = target
            browsers = ["firefox", "browser", "navegador", "chrome"]
            
            if navigate_target:
                destination = navigate_target
            elif target in browsers and search_query:
                # Target es un navegador y hay search_query (posiblemente no convertido por router)
                destination = search_query
            
            url = self._resolve_url(destination)
            # Determinar provider basado en destino (opcional, mejora selectores)
            dest_provider = provider  # Por defecto el provider del análisis
            if "youtube" in destination.lower():
                dest_provider = "youtube"
            elif "instagram" in destination.lower():
                dest_provider = "generic"  # No hay selectores específicos aún
            # Podríamos expandir esto más adelante
            
            plan = [{
                "type": "navigate",
                "params": {"url": url},
                "provider": dest_provider
            }]
            logger.debug(f"NAVIGATE: destino={destination}, url={url}, provider={dest_provider}")
        
        # Caso: INTERACT (interacción específica)
        elif automation_intent == "INTERACT":
            if action == "click":
                plan = [{
                    "type": "click",
                    "params": {"selector": target},
                    "provider": provider
                }]
            elif action == "type":
                # Separar texto y selector si viene en formato "texto en selector"
                if " en " in target:
                    parts = target.split(" en ")
                    text = parts[0]
                    selector = parts[1]
                else:
                    text = target
                    selector = "active"
                plan = [{
                    "type": "type",
                    "params": {"selector": selector, "text": text},
                    "provider": provider
                }]
            elif action in ["close", "close_tab"]:
                plan = [{
                    "type": "close_tab",
                    "params": {},
                    "provider": "generic"
                }]
            elif action in ["pause", "play", "volume_up", "volume_down", "fullscreen"]:
                plan = [{
                    "type": "control_media",
                    "params": {"action": action},
                    "provider": provider
                }]
            else:
                # Interacción genérica por texto
                plan = [{
                    "type": "find_by_text",
                    "params": {"text": target, "partial": True},
                    "provider": provider
                }]
        
        # Caso por defecto: tratar como navegación o búsqueda según contenido
        else:
            if "." in target and " " not in target:
                # Parece una URL o dominio
                url = self._resolve_url(target)
                plan = [{
                    "type": "navigate",
                    "params": {"url": url},
                    "provider": "generic"
                }]
            elif search_query:
                plan = self._build_generic_search_plan(search_query)
            else:
                # Búsqueda genérica del texto completo
                plan = self._build_generic_search_plan(target)
        
        logger.debug(f"Plan generado para {automation_intent}: {len(plan)} acciones")
        return plan
    
    def _build_youtube_playback_plan(self, query: str) -> List[Dict[str, Any]]:
        """Plan para reproducir video en YouTube"""
        return [
            {"type": "navigate", "params": {"url": "https://www.youtube.com"}, "provider": "youtube"},
            {"type": "search", "params": {"query": query, "engine": "youtube"}, "provider": "youtube"},
            {"type": "wait", "params": {"element_type": "video_item", "timeout": 10}, "provider": "youtube"},
            {"type": "click", "params": {"selector_type": "video_item"}, "provider": "youtube"},
            {"type": "wait", "params": {"element_type": "player_container", "timeout": 15}, "provider": "youtube"},
            {"type": "play_media", "params": {}, "provider": "youtube"}
        ]
    
    def _build_youtube_music_playback_plan(self, query: str) -> List[Dict[str, Any]]:
        """Plan para reproducir música en YouTube Music"""
        return [
            {"type": "navigate", "params": {"url": "https://music.youtube.com"}, "provider": "youtube_music"},
            {"type": "search", "params": {"query": query, "engine": "youtube_music"}, "provider": "youtube_music"},
            {"type": "wait", "params": {"element_type": "song_item", "timeout": 10}, "provider": "youtube_music"},
            {"type": "click", "params": {"selector_type": "song_item"}, "provider": "youtube_music"},
            {"type": "wait", "params": {"element_type": "player_container", "timeout": 15}, "provider": "youtube_music"},
            {"type": "play_media", "params": {}, "provider": "youtube_music"}
        ]
    
    def _build_generic_search_plan(self, query: str) -> List[Dict[str, Any]]:
        """Plan para búsqueda web genérica"""
        return [
            {"type": "navigate", "params": {"url": "https://duckduckgo.com"}, "provider": "generic"},
            {"type": "search", "params": {"query": query, "engine": "duckduckgo"}, "provider": "generic"}
        ]
    
    def _resolve_url(self, target: str) -> str:
        """Convierte nombre de sitio en URL completa.
        
        Valida la URL resultante para evitar URLs malformadas como 'https:/about:blank'.
        """
        if not target or not target.strip():
            return "https://duckduckgo.com"
        
        target_lower = target.strip().lower()
        
        # Rechazar targets invalidos
        if target_lower in ("about:blank", "blank", "none", "null"):
            return "https://duckduckgo.com"
        
        # Mapeo de sitios comunes
        site_map = {
            "youtube": "https://www.youtube.com",
            "youtube music": "https://music.youtube.com",
            "instagram": "https://www.instagram.com",
            "facebook": "https://www.facebook.com",
            "twitter": "https://twitter.com",
            "x": "https://twitter.com",
            "github": "https://github.com",
            "gmail": "https://mail.google.com",
            "google": "https://www.google.com",
            "reddit": "https://www.reddit.com",
            "twitch": "https://www.twitch.tv",
            "spotify": "https://open.spotify.com",
            "netflix": "https://www.netflix.com",
            "whatsapp": "https://web.whatsapp.com",
            "discord": "https://discord.com/app",
            "wikipedia": "https://www.wikipedia.org",
            "prime video": "https://www.primevideo.com",
            "hbo": "https://www.hbo.com",
            "stackoverflow": "https://stackoverflow.com",
            "stack overflow": "https://stackoverflow.com",
            "telegram": "https://web.telegram.org",
            "linkedin": "https://www.linkedin.com",
            "pinterest": "https://www.pinterest.com",
            "tiktok": "https://www.tiktok.com",
            "amazon": "https://www.amazon.com",
            "ebay": "https://www.ebay.com",
            "paypal": "https://www.paypal.com",
            "dropbox": "https://www.dropbox.com",
            "drive": "https://drive.google.com",
            "maps": "https://maps.google.com",
            "translate": "https://translate.google.com",
            "weather": "https://www.weather.com",
            "chatgpt": "https://chat.openai.com",
            "claude": "https://claude.ai",
        }
        
        for site, url in site_map.items():
            if site in target_lower:
                return url
        
        # Si ya es una URL completa
        if target_lower.startswith(("http://", "https://")):
            from urllib.parse import urlparse
            parsed = urlparse(target_lower)
            if parsed.netloc and parsed.netloc != "about:blank":
                return target_lower
            # URL malformada, buscar en su lugar
            return f"https://duckduckgo.com/?q={target.replace(' ', '+')}"
        
        # Si parece un dominio (contiene punto, sin espacios)
        if "." in target_lower and " " not in target_lower:
            return f"https://{target_lower}"
        
        # Por defecto, busqueda en DuckDuckGo
        return f"https://duckduckgo.com/?q={target.replace(' ', '+')}"
    
    def get_possible_providers(self) -> List[str]:
        """Retorna lista de proveedores soportados"""
        return ["youtube", "youtube_music", "generic"]