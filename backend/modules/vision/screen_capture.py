import os
import subprocess
from datetime import datetime
import mss
import mss.tools

class ScreenCapturer:
    def __init__(self, storage_path="storage/vision"):
        self.storage_path = storage_path
        os.makedirs(self.storage_path, exist_ok=True)

    def capture(self) -> str:
        """Captura la pantalla completa detectando dinámicamente X11 o Wayland."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"capture_{timestamp}.png"
        filepath = os.path.join(self.storage_path, filename)
        
        # Detectar si estamos en Wayland
        is_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY")
        
        if is_wayland:
            # Intento con 'grim' (Estándar en Wayland/Arch)
            try:
                subprocess.run(["grim", filepath], check=True, capture_output=True)
                return filepath
            except Exception:
                # Fallback para KDE Plasma: 'spectacle'
                try:
                    subprocess.run(["spectacle", "-b", "-n", "-o", filepath], check=True, capture_output=True)
                    return filepath
                except Exception:
                    pass

        # Intento con MSS (Solo funciona en X11)
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                mss.tools.to_png(sct_img.rgb, sct_img.size, output=filepath)
            return filepath
        except Exception as e:
            raise RuntimeError(
                f"Error al capturar pantalla. "
                f"Si usas Wayland, instala 'grim': sudo pacman -S grim. "
                f"Detalles: {e}"
            )