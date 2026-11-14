"""
DeviceCapabilities — Detecta capacidades del dispositivo según canal y user-agent.

Permite que el LLM adapte respuestas: no sugerir "abre Firefox" si el usuario
está en Telegram móvil, sino ofrecer links o info textual.
"""
from dataclasses import dataclass


@dataclass
class DeviceCapabilities:
    platform: str       # "desktop" | "mobile" | "remote"
    channel: str        # "websocket" | "telegram" | "whatsapp"
    has_display: bool   # puede mostrar GUI?
    has_audio: bool     # puede reproducir audio?
    has_browser: bool   # puede abrir browser?

    @classmethod
    def from_channel(cls, channel_type: str, user_agent: str = "") -> "DeviceCapabilities":
        """Infiere capacidades a partir del canal y user-agent."""
        if channel_type == "websocket":
            # Tauri desktop o browser remoto
            is_mobile = _is_mobile_ua(user_agent)
            if is_mobile:
                return cls(
                    platform="mobile",
                    channel=channel_type,
                    has_display=True,
                    has_audio=True,
                    has_browser=True,
                )
            return cls(
                platform="desktop",
                channel=channel_type,
                has_display=True,
                has_audio=True,
                has_browser=True,
            )

        if channel_type == "telegram":
            return cls(
                platform="mobile",
                channel=channel_type,
                has_display=False,
                has_audio=True,
                has_browser=False,
            )

        if channel_type == "whatsapp":
            return cls(
                platform="mobile",
                channel=channel_type,
                has_display=False,
                has_audio=True,
                has_browser=False,
            )

        # Fallback
        return cls(
            platform="desktop",
            channel=channel_type,
            has_display=True,
            has_audio=True,
            has_browser=True,
        )

    def get_prompt_hint(self) -> str:
        """Genera hint para inyectar en el prompt del LLM."""
        parts = [f"Usuario en {self.platform} via {self.channel}"]

        limitations = []
        if not self.has_display:
            limitations.append("sin GUI")
        if not self.has_browser:
            limitations.append("sin browser directo")
        if not self.has_audio:
            limitations.append("sin audio")

        if limitations:
            parts.append(" — " + ", ".join(limitations))
            parts.append(". Responde con texto plano, links directos y info concisa.")
        else:
            parts.append(". Acceso completo a GUI, browser y audio.")

        return "".join(parts)


def _is_mobile_ua(user_agent: str) -> bool:
    """Heurística simple para detectar mobile por user-agent."""
    if not user_agent:
        return False
    ua_lower = user_agent.lower()
    mobile_keywords = ("mobile", "android", "iphone", "ipad", "ipod", "opera mini", "opera mobi")
    return any(kw in ua_lower for kw in mobile_keywords)
