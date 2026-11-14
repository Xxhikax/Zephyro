from faster_whisper import WhisperModel
import sounddevice as sd
import numpy as np
import tempfile
import os
from rich.console import Console

console = Console()

class STT:
    def __init__(self, model_size=None):
        """
        Inicializa el motor de Speech-to-Text usando Whisper

        Args:
            model_size: Tamaño del modelo ('tiny', 'base', 'small', 'medium', 'large-v3').
                        Default vía env ZEPHYRO_WHISPER_MODEL (medium). Usar 'base'/'small'
                        para STT local liviano cuando hay poca VRAM.
        """
        model_size = model_size or os.getenv("ZEPHYRO_WHISPER_MODEL", "medium")
        console.print(f"[dim]Cargando modelo faster-whisper ({model_size})...[/dim]")

        # Umbral de silencio dinámico (ajustable con calibrate)
        self.energy_threshold = 0.02

        try:
            self.model = WhisperModel(model_size, device="cuda", compute_type="int8")
            self.sample_rate = 16000
            console.print(f"[green]faster-whisper ({model_size}) cargado en CUDA/int8[/green]")
        except Exception as e:
             console.print(f"[yellow]CUDA no disponible ({e}), usando CPU...[/yellow]")
             self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
             self.sample_rate = 16000
             console.print(f"[green]faster-whisper ({model_size}) cargado en CPU/int8[/green]")

    def calibrate(self):
        """Calibra el umbral de ruido del entorno"""
        console.print("\n[bold yellow]Iniciando calibración de micrófono...[/bold yellow]")
        console.print("Por favor, guarda silencio durante 2 segundos...")
        
        try:
            duration = 2
            recording = sd.rec(
                int(duration * self.sample_rate), 
                samplerate=self.sample_rate, 
                channels=1, 
                dtype='float32'
            )
            sd.wait()
            
            # Calcular RMS por chunks de 100ms (más estable que el pico ante transitorios)
            chunk_size = int(self.sample_rate * 0.1)
            flat = recording.flatten()
            rms_values = [
                float(np.sqrt(np.mean(flat[i:i + chunk_size] ** 2)))
                for i in range(0, len(flat) - chunk_size, chunk_size)
            ]

            # Percentil 95: robusto frente a picos transitorios aislados
            noise_level = float(np.percentile(rms_values, 95))
            avg_noise = float(np.mean(rms_values))
            
            # Margen ×2.5 para rechazar voces de fondo y ruido ambiental continuo
            new_threshold = noise_level * 2.5
            
            # Mínimo razonable y techo para entornos muy ruidosos
            new_threshold = max(new_threshold, 0.015)
            new_threshold = min(new_threshold, 0.15)
                
            self.energy_threshold = new_threshold
            
            console.print(f"[green]Calibración completada.[/green]")
            console.print(f"  • RMS de ruido (p95): {noise_level:.4f}")
            console.print(f"  • RMS medio: {avg_noise:.4f}")
            console.print(f"  • Nuevo umbral de activación: {self.energy_threshold:.4f}")
            
        except Exception as e:
            console.print(f"[red]Error durante calibración: {e}[/red]")

    def record_audio(self, duration=5):
        """
        Graba audio del micrófono
        
        Args:
            duration: Duración máxima de grabación en segundos
            
        Returns:
            numpy array con el audio
        """
        console.print(f"[bold red]● Grabando... ({duration}s)[/bold red]")
        try:
            recording = sd.rec(
                int(duration * self.sample_rate), 
                samplerate=self.sample_rate, 
                channels=1,
                dtype='float32'
            )
            sd.wait()  # Esperar a que termine la grabación
            console.print("[dim]Procesando audio...[/dim]")
            return recording.flatten()
        except Exception as e:
            console.print(f"[red]Error grabando audio: {e}[/red]")
            return None

    def record_until_silence(self, threshold=None, silence_duration=1.2, max_duration=20):
        """
        Graba audio hasta detectar silencio.
        ARGS:
            silence_duration: Segundos de silencio para considerar fin de frase
            max_duration: Tiempo máximo de escucha
        """
        if threshold is None:
            threshold = self.energy_threshold
            
        console.print("[bold red]● Escuchando... (habla ahora)[/bold red]")
        
        frames = []
        silent_chunks = 0
        speech_chunks = 0      # chunks consecutivos con voz por encima del umbral
        speech_detected = False
        chunk_size = int(self.sample_rate * 0.1)  # 100ms chunks

        # Piso de ruido adaptativo (EMA): se actualiza durante el silencio
        # para seguir los cambios lentos del ruido ambiente.
        noise_floor = threshold * 0.5
        noise_alpha = 0.05  # coeficiente EMA (suavizado lento)

        try:
            with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='float32') as stream:
                for _ in range(int(max_duration * 10)):
                    data, _ = stream.read(chunk_size)
                    audio_chunk = data.flatten()
                    frames.append(audio_chunk)

                    # RMS del chunk: más estable que el pico ante transitorios breves
                    rms = float(np.sqrt(np.mean(audio_chunk ** 2)))

                    # Umbral efectivo: nunca baja del umbral estático y siempre
                    # mantiene distancia del piso de ruido adaptativo.
                    effective_threshold = max(threshold, noise_floor * 2.5)

                    if rms > effective_threshold:
                        silent_chunks = 0
                        speech_chunks += 1
                        # Confirmar voz real solo tras ≥3 chunks consecutivos (~300ms)
                        # para ignorar picos transitorios (golpes, toses aisladas, etc.)
                        if speech_chunks >= 3:
                            speech_detected = True
                    else:
                        # Actualizar piso de ruido solo durante silencio real
                        if not speech_detected or silent_chunks > 5:
                            noise_floor = noise_alpha * rms + (1 - noise_alpha) * noise_floor
                        speech_chunks = 0
                        silent_chunks += 1

                    # Cortar solo si se detectó voz real y luego sobrevino silencio
                    if speech_detected and silent_chunks > int(silence_duration * 10) and len(frames) > 15:
                        break

            console.print("[dim]Fin de la escucha.[/dim]")
            return np.concatenate(frames)
        except Exception as e:
            console.print(f"[red]Error escuchando: {e}[/red]")
            return None

    def transcribe(self, audio_data, language: str = "es"):
        """
        Transcribe el audio a texto

        Args:
            audio_data: Numpy array con el audio o ruta a archivo de audio
            language: Código de idioma para Whisper ("es", "en", "auto")

        Returns:
            str: Texto transcrito
        """
        if audio_data is None:
            return ""

        try:
            lang = None if language == "auto" else language
            segments, info = self.model.transcribe(
                audio_data,
                language=lang,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(
                    threshold=0.45,
                    min_speech_duration_ms=200,
                    speech_pad_ms=200,
                ),
                condition_on_previous_text=False,
                no_speech_threshold=0.5,
                log_prob_threshold=-1.0,
                compression_ratio_threshold=2.0,
                initial_prompt="Zephyro, JARVIS, Bruno.",
            )
            text = " ".join(
                seg.text for seg in segments
                if seg.avg_logprob >= -1.0
            ).strip()
            return text
        except Exception as e:
            console.print(f"[red]Error en transcripción: {e}[/red]")
            return ""

    def listen(self, mode="auto"):
        """Método principal para escuchar y transcribir"""
        if mode == "auto":
            audio = self.record_until_silence()
        else:
            audio = self.record_audio(duration=5)
        
        if audio is None:
            return ""

        text = self.transcribe(audio)
        
        if text:
            console.print(f"[bold cyan]Tú (voz):[/bold cyan] {text}")
        else:
            console.print("[yellow]No te entendí bien (o hubo silencio).[/yellow]")
            
        return text

# Ejemplo de uso
if __name__ == "__main__":
    stt = STT()
    text = stt.listen()
    print(f"Texto reconocido: {text}")
