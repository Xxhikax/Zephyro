# modules/voice/piper_engine.py

import logging
import subprocess
import tempfile
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Bundled piper binary (Windows): backend/bin/piper/piper.exe
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_BUNDLED_PIPER = _BACKEND_DIR / "bin" / "piper" / ("piper.exe" if os.name == "nt" else "piper")


class PiperTTS:
    def __init__(
        self,
        model_path: str,
        length_scale: float = 1.05,
        noise_scale: float = 0.7,
        noise_w: float = 0.8,
        volume: float = 1.0,
    ):
        self.model_path = model_path
        if _BUNDLED_PIPER.exists():
            self.piper_bin = str(_BUNDLED_PIPER)
        else:
            self.piper_bin = shutil.which("piper") or "piper"
        self.length_scale = length_scale
        self.noise_scale = noise_scale
        self.noise_w = noise_w
        self.volume = volume

    def speak(self, text: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name

        cmd = [
            self.piper_bin,
            "--model", self.model_path,
            "--output-file", wav_path,
            "--length-scale", str(self.length_scale),
            "--noise-scale", str(self.noise_scale),
            "--noise-w-scale", str(self.noise_w),
            "--volume", str(self.volume),
        ]

        # Use binary stdin with explicit UTF-8 encoding so Windows' cp1252
        # default doesn't crash on non-Latin1 characters (e.g. ✅ U+2705, ⚠ U+26A0)
        # that slip past the sanitizer.
        # On Windows, run from the bundled piper dir so it finds neighbour DLLs.
        cwd = str(_BUNDLED_PIPER.parent) if _BUNDLED_PIPER.exists() else None
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )

        proc.stdin.write(text.encode("utf-8", errors="ignore"))
        proc.stdin.close()
        exit_code = proc.wait()

        if exit_code != 0:
            stderr_out = (proc.stderr.read() or b"").decode("utf-8", errors="replace")[:300]
            logger.error(f"Piper TTS falló (code {exit_code}): {stderr_out}")
            # Limpiar archivo parcial si existe
            try:
                os.remove(wav_path)
            except OSError:
                pass
            raise RuntimeError(f"Piper TTS falló (code {exit_code}): {stderr_out}")

        import sys
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(wav_path, winsound.SND_FILENAME)
        else:
            subprocess.run(
                ["aplay", wav_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        os.remove(wav_path)
