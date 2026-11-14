"""Voice router — STT and TTS endpoints."""
import base64
import os
import re
import secrets
import struct
import wave
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from core.auth_middleware import require_auth

router = APIRouter()

_BACKEND_DIR = Path(__file__).parent.parent
from core.paths import UPLOAD_DIR

WHISPER_HALLUCINATIONS = {
    "subtítulos por la comunidad",
    "thanks for watching",
    "amara.org",
    "suscríbete",
    "dale like",
    "no olvides suscribirte",
    "gracias por ver",
    "subtitulado por",
    "esto es una conversación en español",
    "hola, esto es una conversación",
    "conversación en español con un asistente",
    "asistente llamado zephyro",
}

# Palabras comunes en español — usadas para detectar si una transcripción
# parece más español que inglés (heuristic para auto-detección de idioma).
_SPANISH_FUNCTION_WORDS = frozenset([
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "en", "con", "por", "para", "a", "al",
    "que", "es", "son", "fue", "era", "está", "estaba",
    "no", "sí", "si", "pero", "o", "y", "e", "ni",
    "me", "te", "se", "nos", "os", "le", "les", "lo", "la",
    "mi", "tu", "su", "nuestro", "vuestro", "mis", "tus", "sus",
    "yo", "tú", "él", "ella", "usted", "nosotros", "vosotros", "ellos",
    "este", "esta", "esto", "ese", "esa", "eso", "aquel",
    "qué", "quién", "cómo", "cuándo", "dónde", "cuál",
])

# Palabras función comunes en inglés para el mismo propósito
_EN_FUNCTION_WORDS_STT = frozenset([
    "the", "of", "and", "to", "in", "is", "that", "for", "it", "on",
    "with", "as", "are", "be", "this", "was", "from", "or", "by",
    "but", "not", "we", "he", "she", "they", "you", "i",
])

# Estado global del engine STT seleccionado ("local" o "groq").
# Default configurable: con ZEPHYRO_STT_ENGINE=groq el arranque NO carga whisper local
# (libera ~2.6GB de VRAM para un modelo local de clasificación).
_stt_engine = os.getenv("ZEPHYRO_STT_ENGINE", "local")


def get_stt_engine() -> str:
    return _stt_engine


def set_stt_engine(engine: str):
    global _stt_engine
    _stt_engine = engine


# Estado global del engine TTS seleccionado ("local" o "fishaudio")
_tts_engine = "local"


def get_tts_engine() -> str:
    return _tts_engine


def set_tts_engine(engine: str):
    global _tts_engine
    _tts_engine = engine


def _is_whisper_hallucination(text: str) -> bool:
    if len(text.strip()) <= 2:
        return True
    lower = text.lower().strip()
    # Exact match for short texts to avoid false positives
    if len(lower) < 10:
        return lower in WHISPER_HALLUCINATIONS
    return any(h in lower for h in WHISPER_HALLUCINATIONS)


def _looks_garbled(text: str) -> bool:
    """
    Heurístico: detecta si una transcripción en español parece distorsionada
    (audio en inglés forzado a transcripción en español).

    Señales de alarma:
    - Muchas palabras con trigramas consonánticos inusuales (kr, ts, sh, clústeres
      no nativos del español como "cyl", "dopl", "ght")
    - Proporción muy baja de palabras función comunes en español
    - Presencia de palabras función inglesas rodeando la transcripción
    """
    if not text or len(text) < 10:
        return False

    words = re.findall(r'[a-zA-Záéíóúüñ]+', text.lower())
    if len(words) < 3:
        return False

    # Señal 1: trigramas consonánticos inusuales en español
    unusual_trigrams = re.findall(r'[bcdfghjklmnpqrstvwxyz]{3,}', text.lower())
    # Señal 2: baja proporción de palabras función españolas
    es_fn_count = sum(1 for w in words if w in _SPANISH_FUNCTION_WORDS)
    # Señal 3: alta proporción de palabras función inglesas
    en_fn_count = sum(1 for w in words if w in _EN_FUNCTION_WORDS_STT)

    # Si hay más palabras función inglesas que españolas Y hay clusters
    # consonánticos inusuales, probablemente es audio en inglés transcrito como español.
    if en_fn_count > es_fn_count and len(unusual_trigrams) >= 2:
        return True
    # También si no hay palabras función españolas y hay ≥1 cluster inusual
    # (pasa con frases cortas como "gold in the ceiling" → "produce a gold endocylinder")
    if es_fn_count == 0 and en_fn_count >= 1 and len(unusual_trigrams) >= 1:
        return True

    return False


def get_orchestrator(request: Request):
    return request.app.state.orchestrator


async def _transcribe_groq(audio_path: Path, language: str = "es") -> str:
    """Transcribe audio using Groq's Whisper API."""
    import certifi
    import httpx

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise Exception("GROQ_API_KEY no configurada")

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    suffix = audio_path.suffix.lstrip(".")
    mime_map = {
        "wav": "audio/wav",
        "webm": "audio/webm",
        "mp4": "audio/mp4",
        "ogg": "audio/ogg",
        "mp3": "audio/mpeg",
    }
    mime = mime_map.get(suffix, "audio/wav")

    # Prompt de contexto para Whisper: nombres propios de bandas y términos musicales
    # en inglés que el STT confunde cuando se pronuncian con acento español.
    # Whisper usa este texto para sesgar la transcripción hacia estos tokens.
    STT_MUSIC_PROMPT = (
        "Zephyro, YouTube Music, Spotify, The Black Keys, The Black Midi, "
        "Daft Punk, Led Zeppelin, AC/DC, Black Sabbath, Pink Floyd, Nirvana, "
        "Radiohead, Coldplay, The Beatles, Rolling Stones, "
        "reproduce, álbum, playlist, canción, artista, pausa, siguiente, anterior."
    )

    # CachyOS tiene cert store incompleto — usar certifi
    async with httpx.AsyncClient(timeout=30.0, verify=certifi.where()) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (audio_path.name, audio_bytes, mime)},
            data={
                "model": "whisper-large-v3-turbo",
                "language": language,
                "response_format": "json",
                "prompt": STT_MUSIC_PROMPT,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("text", "").strip()


@router.post("/voice/stt")
async def speech_to_text(
    request: Request,
    audio: UploadFile = File(...),
    session: dict = Depends(require_auth),
    engine: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
):
    """Convertir audio a texto (Speech to Text)"""
    audio_path = None
    wav_path = None

    try:
        # El frontend puede mandar engine/language por request, si no usa el global
        active_engine = engine or get_stt_engine()
        active_language = language or "es"

        content_type = audio.content_type or ''
        if 'webm' in content_type or (audio.filename and audio.filename.endswith('.webm')):
            ext = '.webm'
        elif 'mp4' in content_type or (audio.filename and audio.filename.endswith('.mp4')):
            ext = '.mp4'
        elif 'ogg' in content_type or (audio.filename and audio.filename.endswith('.ogg')):
            ext = '.ogg'
        else:
            ext = '.wav'

        audio_path = UPLOAD_DIR / f"audio_{secrets.token_hex(8)}{ext}"
        audio_content = await audio.read()

        with open(audio_path, "wb") as f:
            f.write(audio_content)

        if audio_path.stat().st_size == 0:
            raise Exception("Archivo de audio vacío")

        # Groq acepta webm/ogg/mp4 nativamente — skip ffmpeg para mejor calidad
        if active_engine == "groq":
            transcription_path = audio_path
        elif ext in ['.webm', '.mp4', '.ogg']:
            wav_path = audio_path.with_suffix('.wav')
            try:
                import asyncio
                import subprocess
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        'ffmpeg', '-i', str(audio_path),
                        '-af', 'afftdn=nf=-25,highpass=f=80,lowpass=f=8000,volume=1.3',
                        '-ar', '16000',
                        '-ac', '1',
                        '-acodec', 'pcm_s16le',
                        '-y',
                        str(wav_path)
                    ],
                    check=True, capture_output=True, text=True, timeout=30
                )
                transcription_path = wav_path
            except subprocess.CalledProcessError:
                transcription_path = audio_path
            except FileNotFoundError:
                transcription_path = audio_path
        else:
            transcription_path = audio_path

        # Detección de silencio (solo si tenemos wav)
        try:
            if transcription_path.suffix == '.wav':
                with wave.open(str(transcription_path), 'rb') as wav_file:
                    frames = wav_file.readframes(wav_file.getnframes())
                    samples = struct.unpack(f"{len(frames)//2}h", frames)
                    sum_squares = sum(s * s for s in samples)
                    rms = (sum_squares / len(samples)) ** 0.5
                    normalized_rms = rms / 32768
                    if normalized_rms < 0.003:
                        if audio_path and audio_path.exists():
                            audio_path.unlink()
                        if wav_path and wav_path.exists():
                            wav_path.unlink()
                        return {"success": False, "error": "Silencio detectado", "text": ""}
        except Exception:
            pass

        # ── Transcripción según engine ──────────────────────────────
        import asyncio
        used_language = active_language
        if active_engine == "groq":
            text = await _transcribe_groq(transcription_path, language=active_language)
            # Auto-detect: si la transcripción en español parece distorsionada
            # (audio en inglés forzado a español), reintentar con inglés.
            if active_language == "es" and _looks_garbled(text):
                text_en = await _transcribe_groq(transcription_path, language="en")
                if text_en and not _looks_garbled(text_en):
                    text = text_en
                    used_language = "en"
        else:
            orchestrator = get_orchestrator(request)
            stt = orchestrator._get_stt()
            text = await asyncio.to_thread(stt.transcribe, str(transcription_path), active_language)
            # Para local también: reintentar con inglés si parece distorsionado
            if active_language == "es" and _looks_garbled(text):
                text_en = await asyncio.to_thread(stt.transcribe, str(transcription_path), "en")
                if text_en and not _looks_garbled(text_en):
                    text = text_en
                    used_language = "en"

        try:
            if audio_path and audio_path.exists():
                audio_path.unlink()
            if wav_path and wav_path.exists():
                wav_path.unlink()
        except Exception:
            pass

        if not text or not text.strip():
            return {"success": False, "error": "No se detectó voz en el audio", "text": ""}

        text = text.strip()
        if _is_whisper_hallucination(text):
            return {"success": False, "error": "Alucinación de Whisper filtrada", "text": ""}

        return {"success": True, "text": text, "engine": active_engine, "language": used_language}

    except Exception as e:
        try:
            if audio_path and audio_path.exists():
                audio_path.unlink()
            if wav_path and wav_path.exists():
                wav_path.unlink()
        except Exception:
            pass
        return {"success": False, "error": str(e), "text": ""}


@router.post("/voice/diag")
async def voice_diagnostic(
    request: Request,
    audio: UploadFile = File(...),
    session: dict = Depends(require_auth),
    engine: Optional[str] = Form(None),
):
    """Diagnóstico de captura de mic.

    Recibe un blob corto, mide RMS antes y después de ffmpeg, intenta
    transcribir, y devuelve recomendación de threshold. Sin side-effects.
    """
    audio_path = None
    wav_path = None
    try:
        active_engine = engine or get_stt_engine()

        content_type = audio.content_type or ''
        if 'webm' in content_type or (audio.filename or '').endswith('.webm'):
            ext = '.webm'
        elif 'mp4' in content_type or (audio.filename or '').endswith('.mp4'):
            ext = '.mp4'
        elif 'ogg' in content_type or (audio.filename or '').endswith('.ogg'):
            ext = '.ogg'
        else:
            ext = '.wav'

        audio_path = UPLOAD_DIR / f"diag_{secrets.token_hex(8)}{ext}"
        audio_content = await audio.read()
        with open(audio_path, "wb") as f:
            f.write(audio_content)
        size_bytes = audio_path.stat().st_size

        result = {
            "success": True,
            "engine": active_engine,
            "size_bytes": size_bytes,
            "rms_raw": None,
            "rms_post_ffmpeg": None,
            "duration_s": None,
            "transcription": "",
            "recommended_vad_threshold": None,
            "errors": [],
        }

        # Convertir a WAV con ffmpeg para medir RMS post-procesado
        if ext in ['.webm', '.mp4', '.ogg']:
            wav_path = audio_path.with_suffix('.wav')
            try:
                import asyncio
                import subprocess
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        'ffmpeg', '-i', str(audio_path),
                        '-af', 'afftdn=nf=-25,highpass=f=80,lowpass=f=8000,volume=1.3',
                        '-ar', '16000', '-ac', '1', '-acodec', 'pcm_s16le', '-y',
                        str(wav_path)
                    ],
                    check=True, capture_output=True, text=True, timeout=30
                )
            except Exception as e:
                result["errors"].append(f"ffmpeg: {e}")
                wav_path = None

        # Si es WAV directo o tras ffmpeg, medimos RMS
        target_wav = wav_path if (wav_path and wav_path.exists()) else (audio_path if ext == '.wav' else None)
        if target_wav and target_wav.exists():
            try:
                with wave.open(str(target_wav), 'rb') as wav_file:
                    n_frames = wav_file.getnframes()
                    sample_rate = wav_file.getframerate()
                    frames = wav_file.readframes(n_frames)
                    samples = struct.unpack(f"{len(frames)//2}h", frames)
                    if samples:
                        sum_squares = sum(s * s for s in samples)
                        rms = (sum_squares / len(samples)) ** 0.5
                        result["rms_post_ffmpeg"] = round(rms / 32768, 5)
                        result["duration_s"] = round(n_frames / sample_rate, 2)
                        # Heurística: threshold recomendado ≈ rms_normalizado * 2.5,
                        # con floor 0.03 y techo 0.15
                        rec = max(0.03, min(0.15, (rms / 32768) * 2.5))
                        result["recommended_vad_threshold"] = round(rec, 4)
            except Exception as e:
                result["errors"].append(f"rms: {e}")

        # Transcribir para verificar el pipeline completo
        try:
            import asyncio
            transcription_path = audio_path if active_engine == "groq" else (wav_path or audio_path)
            if active_engine == "groq":
                text = await _transcribe_groq(transcription_path)
            else:
                orchestrator = get_orchestrator(request)
                stt = orchestrator._get_stt()
                text = await asyncio.to_thread(stt.transcribe, str(transcription_path))
            result["transcription"] = (text or "").strip()
        except Exception as e:
            result["errors"].append(f"transcribe: {e}")

        return result
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        try:
            if audio_path and audio_path.exists():
                audio_path.unlink()
            if wav_path and wav_path.exists():
                wav_path.unlink()
        except Exception:
            pass


def _run_piper_sync(text: str, audio_path: Path) -> dict:
    """
    Genera audio con Piper de forma síncrona y bloqueante.

    DEBE ejecutarse vía asyncio.to_thread() desde el handler async — `proc.wait()`
    bloquea el hilo donde corre, así que correrlo en un thread separado evita
    congelar el event loop de FastAPI durante toda la generación (que para
    respuestas largas puede tardar 20-30s).

    Devuelve {"success": bool, "audio"|"error": ...}. No lanza (captura todo).
    """
    import shutil
    import subprocess

    model_path = _BACKEND_DIR / "models" / "piper" / "es_ES" / "es_ES-davefx-medium.onnx"
    if not model_path.exists():
        return {"success": False, "error": f"Modelo TTS no encontrado: {model_path}"}

    bundled_piper = _BACKEND_DIR / "bin" / "piper" / ("piper.exe" if os.name == "nt" else "piper")
    if bundled_piper.exists():
        piper_bin = str(bundled_piper)
        piper_cwd = str(bundled_piper.parent)
    else:
        piper_bin = shutil.which("piper") or "piper"
        piper_cwd = None

    cmd = [
        piper_bin,
        "--model", str(model_path),
        "--output-file", str(audio_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=piper_cwd,
    )
    try:
        # communicate() con timeout maneja el deadlock de stdin/stderr y mata el
        # proceso si excede el límite. 30s cubre respuestas largas con margen.
        _, stderr_bytes = proc.communicate(input=text.encode("utf-8", errors="ignore"), timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return {"success": False, "error": "Piper TTS timeout (>30s)"}

    if proc.returncode != 0:
        stderr_output = (stderr_bytes or b"").decode("utf-8", errors="replace")[:200]
        return {"success": False, "error": f"Piper TTS falló (code {proc.returncode}): {stderr_output}"}

    if not audio_path.exists():
        return {"success": False, "error": "El archivo de audio no se generó"}

    with open(audio_path, "rb") as f:
        audio_data = f.read()
    try:
        audio_path.unlink()
    except OSError:
        pass

    return {"success": True, "audio": base64.b64encode(audio_data).decode("utf-8")}


async def _fishaudio_tts(text: str) -> dict:
    """Genera audio con Fish Audio (cloud) usando httpx async — no bloquea el loop."""
    import certifi
    import httpx
    from core.response_sanitizer import normalize_for_fishaudio

    fish_key = os.environ.get("FISHAUDIO_API_KEY", "")
    fish_ref = os.environ.get("FISHAUDIO_REFERENCE_ID", "")
    if not fish_key:
        return {"success": False, "error": "FISHAUDIO_API_KEY no configurada"}
    if not fish_ref:
        return {"success": False, "error": "FISHAUDIO_REFERENCE_ID no configurado"}

    text = normalize_for_fishaudio(text)

    try:
        async with httpx.AsyncClient(timeout=30.0, verify=certifi.where()) as client:
            resp = await client.post(
                "https://api.fish.audio/v1/tts",
                headers={
                    "Authorization": f"Bearer {fish_key}",
                    "Content-Type": "application/json",
                    "model": "s2-pro",
                },
                json={
                    "text": text,
                    "reference_id": fish_ref,
                    "format": "wav",
                    # "balanced" trims first-byte latency vs "normal" with negligible
                    # quality loss; combined with frontend sentence-chunking this cuts
                    # perceived TTS delay substantially on multi-sentence answers.
                    "latency": "balanced",
                    "normalize": True,
                },
            )
        if resp.status_code != 200:
            detail = resp.text[:300] if resp.text else f"HTTP {resp.status_code}"
            return {"success": False, "error": f"Fish Audio API error: {detail}"}
        audio_data = resp.content
        if not audio_data:
            return {"success": False, "error": "Fish Audio devolvió audio vacío"}
        return {"success": True, "audio": base64.b64encode(audio_data).decode("utf-8")}
    except Exception as e:
        return {"success": False, "error": f"Fish Audio falló: {str(e)}"}


async def synthesize_tts(text: str, engine: Optional[str] = None, placeholder: bool = True) -> dict:
    """Sintetiza `text` a audio y devuelve `{"success": bool, "audio": b64, "error"?}`.

    Núcleo de TTS reutilizable (sin auth) — lo usan tanto el endpoint `/voice/tts`
    (TTS manual desde el frontend) como el WS de chat (push del audio del response
    en call mode, evitando el segundo round-trip frágil del frontend).

    Per-sentence callers pass `placeholder=False`: a chunk that cleans to nothing returns
    `{"success": False, "empty": True}` instead of speaking "Listo." mid-answer.
    """
    import asyncio
    from core.response_sanitizer import prepare_for_tts

    text = prepare_for_tts(text, placeholder=placeholder)
    if not text:
        return {"success": False, "empty": True, "error": "Texto vacío tras limpieza TTS"}

    # Determinar engine: explícito > global > "local"
    resolved_engine = engine or _tts_engine
    if resolved_engine not in ("local", "fishaudio"):
        resolved_engine = "local"

    # ── Fish Audio (cloud) — async, no bloquea el loop ──
    if resolved_engine == "fishaudio":
        return await _fishaudio_tts(text)

    # ── Local Piper — corre en thread aparte para no congelar el event loop ──
    audio_path = UPLOAD_DIR / f"tts_{secrets.token_hex(8)}.wav"
    result = await asyncio.to_thread(_run_piper_sync, text, audio_path)

    # Auto-fallback: if Piper failed (model missing, binary not found, etc.)
    # and Fish Audio credentials are configured, use Fish Audio transparently.
    if not result.get("success") and os.environ.get("FISHAUDIO_API_KEY") and os.environ.get("FISHAUDIO_REFERENCE_ID"):
        return await _fishaudio_tts(text)
    return result


@router.post("/voice/tts")
async def text_to_speech(
    text: str = Form(...),
    session: dict = Depends(require_auth),
    engine: Optional[str] = Form(None)
):
    """Convertir texto a audio (Text to Speech)"""
    try:
        return await synthesize_tts(text, engine)
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}
