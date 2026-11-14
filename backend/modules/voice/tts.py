import asyncio
import os
import io
import unicodedata
import requests
import tempfile
from pathlib import Path
import sounddevice as sd
import numpy as np  # Necesitaremos numpy para convertir los bytes
import soundfile as sf
from google import genai  # pip install google-genai
from modules.voice.piper_engine import PiperTTS
from rich.console import Console

console = Console()


def _normalize_for_tts(text: str) -> str:
    """Normaliza texto a NFC para que Piper/Google TTS pronuncien tildes correctamente.
    
    Algunos LLMs (Claude Haiku) pueden emitir decomposed Unicode (NFD)
    donde 'á' es 'a' + combining acute accent (U+0301). Piper no reconoce
    los combining marks separados y los lee literalmente (ej: 'revisio\u0301n'
    suena como 'revisión acento' en vez de 'revisión').
    
    NFC los convierte de vuelta a precomposed: 'revisión' (1 code point).
    """
    return unicodedata.normalize('NFC', text)


class TTS:
    def __init__(self):
        # 1. Respaldo Local
        self.local_engine = PiperTTS(model_path="models/piper/es_ES/es_ES-davefx-medium.onnx")
        
        self.gemini_available = False
        api_key = os.getenv("GOOGLE_API_KEY")
        
        if api_key:
            try:
                # Usamos v1alpha que es el endpoint para los modelos nativos de audio ilimitados
                self.client = genai.Client(
                    api_key=api_key, 
                    http_options={'api_version': 'v1alpha'}
                )
                # Actualizado al modelo nativo de audio solicitado
                self.model_id = "models/gemini-2.5-flash-native-audio-preview-12-2025"
                self.gemini_available = True
                console.print(f"[green]OK Modo Ilimitado configurado: {self.model_id}[/green]")
            except Exception as e:
                console.print(f"[red]Error configurando Live API: {e}[/red]")

        self.fishaudio_available = False
        self.fishaudio_key = os.getenv("FISHAUDIO_API_KEY", "")
        self.fishaudio_ref = os.getenv("FISHAUDIO_REFERENCE_ID", "")
        if self.fishaudio_key and self.fishaudio_ref:
            self.fishaudio_available = True
            console.print(f"[green]OK Fish Audio TTS configurado[/green]")

    async def speak(self, text: str, force_local: bool = False):
        """Usa el canal ilimitado si hay internet, sino Piper"""
        text = _normalize_for_tts(text)
        if force_local:
            await asyncio.to_thread(self.local_engine.speak, text)
            return

        # Fish Audio si está configurado y es el engine activo
        from routers.voice import get_tts_engine
        if get_tts_engine() == "fishaudio" and self.fishaudio_available:
            try:
                self._speak_fishaudio(text)
                return
            except Exception as e:
                console.print(f"[yellow]Fish Audio TTS falló: {e}. Usando local...[/yellow]")

        if self.gemini_available:
            try:
                # Usar sesión async directamente
                await self._speak_live_unlimited(text)
                return
            except Exception as e:
                # Si da error 404, prueba quitando o poniendo el prefijo models/ manualmente
                console.print(f"[yellow]Error en canal ilimitado: {e}. Usando local...[/yellow]")

        await asyncio.to_thread(self.local_engine.speak, text)
    
    async def speak_streaming(self, text_chunk: str):
        """Lee un fragmento de texto inmediatamente con Piper (para streaming, no bloquea event loop)"""
        if text_chunk and len(text_chunk.strip()) > 0:
            text_chunk = _normalize_for_tts(text_chunk)
            try:
                await asyncio.to_thread(self.local_engine.speak, text_chunk)
            except RuntimeError as e:
                console.print(f"[yellow]TTS streaming silenciado: {e}[/yellow]")

    async def _speak_live_unlimited(self, text: str):
        """Reproduce audio fluido acumulando los fragmentos de la Live API"""
        # Instrucción CRÍTICA para que actúe solo como lector
        system_instruction = (
            "Eres un motor de lectura de alta fidelidad. "
            "Tu ÚNICA tarea es leer el texto que te enviaré palabra por palabra, sin añadir, "
            "quitar ni comentar nada. Mantén la entonación natural de las frases."
        )
        
        config = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "generation_config": {
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {"voice_name": "Aoede"} # O la voz que prefieras
                    }
                }
            }
        }
        
        audio_chunks = []
        
        async with self.client.aio.live.connect(model=self.model_id, config=config) as session:
            # Enviamos solo el texto tal cual para que lo lea
            await session.send(input=text, end_of_turn=True)
            
            async for message in session.receive():
                # 1. Capturar fragmentos de audio
                if message.server_content and message.server_content.model_turn:
                    for part in message.server_content.model_turn.parts:
                        if part.inline_data:
                            raw_audio = np.frombuffer(part.inline_data.data, dtype=np.int16)
                            audio_chunks.append(raw_audio)
                
                # 2. Detectar cuando la IA terminó de hablar
                if message.server_content and message.server_content.turn_complete:
                    break
        
        # 3. Si acumulamos fragmentos, unirlos y reproducirlos
        if audio_chunks:
            # Concatenamos todos los fragmentos en un solo array
            full_audio = np.concatenate(audio_chunks)
            # Reproducción fluida a 24k
            sd.play(full_audio, samplerate=24000)
            sd.wait()
        else:
            raise ValueError("No se recibió audio del modelo ilimitado.")

    def _speak_fishaudio(self, text: str):
        """Llama a Fish Audio REST API y reproduce el audio con sounddevice."""
        import io
        import requests
        import tempfile
        from core.response_sanitizer import normalize_for_fishaudio

        text = normalize_for_fishaudio(text)

        resp = requests.post(
            "https://api.fish.audio/v1/tts",
            headers={
                "Authorization": f"Bearer {self.fishaudio_key}",
                "Content-Type": "application/json",
                "model": "s2-pro",
            },
            json={
                "text": text,
                "reference_id": self.fishaudio_ref,
                "format": "wav",
                "latency": "normal",
                "normalize": True,
            },
            timeout=30,
        )
        resp.raise_for_status()

        audio_data = resp.content
        if not audio_data:
            raise ValueError("Fish Audio devolvió audio vacío")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = tmp.name

        try:
            data, samplerate = sf.read(tmp_path)
            sd.play(data, samplerate=samplerate)
            sd.wait()
        finally:
            Path(tmp_path).unlink(missing_ok=True)
