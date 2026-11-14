import os
import base64
import requests
import mimetypes
import certifi
from pathlib import Path
from google import genai
from google.genai import types
from typing import Optional

from core.model_catalog import ANTHROPIC_CLASSIFIER_THINKING_OFF, ANTHROPIC_MODELS

# Haiku: barato y rápido para visión (fuente única: core/model_catalog.py)
_ANTHROPIC_VISION_MODEL = ANTHROPIC_MODELS["fast"]


def _anthropic_text(result: dict) -> str:
    """Texto de una respuesta de /v1/messages. El bloque 0 puede ser ``thinking``."""
    for block in result.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "")
    return ""

class ImageAnalyzer:
    def __init__(self, api_key: str, openai_key: Optional[str] = None, anthropic_key: Optional[str] = None):
        # Gemini (por defecto)
        self.gemini_client = genai.Client(api_key=api_key) if api_key else None
        self.gemini_model = "gemini-2.0-flash-exp"
        
        # OpenAI GPT-4 Vision
        self.openai_key = openai_key or os.getenv("OPENAI_API_KEY")
        
        # Anthropic Claude 3.5 Sonnet Vision
        self.anthropic_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY")
        
        # Local Ollama
        self.local_model = "llava:7b"
        
        # EasyOCR (local, gratis, rápido para texto) — lazy: se carga al primer uso
        self.easy_ocr = None
        self._easy_ocr_initialized = False

    def _get_easy_ocr(self):
        """Inicializa EasyOCR la primera vez que se necesita (~250 MB, 5-10s)."""
        if not self._easy_ocr_initialized:
            self._easy_ocr_initialized = True
            try:
                import easyocr
                self.easy_ocr = easyocr.Reader(['es', 'en'], gpu=False, verbose=False)
                print("[Vision] EasyOCR inicializado (OCR local español/inglés)")
            except Exception as e:
                print(f"[Vision] EasyOCR no disponible: {e}")
        return self.easy_ocr

    def analyze(self, image_path: str, prompt: str = "Analiza esta imagen en detalle.", use_cloud: bool = True, provider: str = "auto") -> str:
        """
        Analiza imagen usando diferentes proveedores.
        
        Args:
            image_path: Ruta a la imagen
            prompt: Instrucción para el análisis
            use_cloud: Si usar cloud o local
            provider: 'auto', 'gemini', 'openai', 'anthropic', 'local'
        """
        if not use_cloud:
            return self._analyze_local(image_path, prompt)
        
        # Si no hay ninguna API key, usar local directamente
        if not self.openai_key and not self.anthropic_key and not self.gemini_client:
            print("[Vision] No hay API keys configuradas, usando LLaVA local...")
            return self._analyze_local(image_path, prompt)
        
        # Estrategia: Haiku Vision primero (barato, rápido, excelente para UI),
        # EasyOCR solo para extracción de texto, luego Gemini/LLaVA/OpenAI como fallbacks
        if provider == "auto":
            # 1. Anthropic Haiku Vision (fast, cheap ~$0.0003/image, excellent for screen understanding)
            if self.anthropic_key:
                print("[Vision] Usando Claude Haiku Vision (rápido, barato, excelente para UI)...")
                result = self._analyze_anthropic(image_path, prompt)
                if result and not result.startswith("[") and "Error" not in result:
                    return result

            # 2. EasyOCR (local OCR fallback for text extraction)
            if self._get_easy_ocr():
                print("[Vision] Usando EasyOCR (OCR local fallback)...")
                result = self._analyze_easyocr(image_path, prompt)
                if result and len(result) > 20 and not result.startswith("["):
                    return result

            # 3. Gemini fallback
            if self.gemini_client:
                print("[Vision] Usando Gemini Vision (fallback)...")
                result = self._analyze_gemini(image_path, prompt)
                if result and not result.startswith("[") and "Error" not in result:
                    return result

            # 4. LLaVA local (slow but free)
            if os.path.exists(image_path):
                print("[Vision] Usando LLaVA (visión local, lento pero gratis)...")
                result = self._analyze_local(image_path, prompt)
                if result and len(result) > 30 and not result.startswith("[") and "Error" not in result:
                    return result

            # 5. OpenAI premium
            if self.openai_key:
                print("[Vision] Usando GPT-4 Vision (fallback premium)...")
                result = self._analyze_openai(image_path, prompt)
                if result and not result.startswith("[") and "Error" not in result:
                    return result

            return result or "[Error] No hay métodos de análisis disponibles"
        
        # Proveedor específico
        if provider == "easyocr":
            self._get_easy_ocr()
            return self._analyze_easyocr(image_path, prompt)
        elif provider == "openai":
            return self._analyze_openai(image_path, prompt)
        elif provider == "anthropic":
            return self._analyze_anthropic(image_path, prompt)
        elif provider == "gemini":
            return self._analyze_gemini(image_path, prompt)
        else:
            return self._analyze_local(image_path, prompt)
    
    def _get_mime_type(self, image_path: str) -> str:
        """Detecta el tipo MIME de la imagen."""
        # Intentar detectar por extensión
        mime_type, _ = mimetypes.guess_type(image_path)
        
        if mime_type and mime_type.startswith('image/'):
            return mime_type
        
        # Fallback a detectar por extensión manualmente
        ext = Path(image_path).suffix.lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.bmp': 'image/bmp'
        }
        
        return mime_types.get(ext, 'image/png')  # Default a PNG

    def _analyze_gemini(self, image_path: str, prompt: str) -> str:
        """Análisis con Gemini (Google)"""
        try:
            if not self.gemini_client:
                return "[Error] Gemini no está configurado"
            
            if not os.path.exists(image_path):
                return f"[Error] Archivo no encontrado: {image_path}"
            
            mime_type = self._get_mime_type(image_path)
            
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            
            response = self.gemini_client.models.generate_content(
                model=self.gemini_model,
                contents=[prompt, types.Part.from_bytes(data=image_bytes, mime_type=mime_type)]
            )
            return response.text
        except Exception as e:
            return f"[Gemini Error] {e}"
    
    def _analyze_openai(self, image_path: str, prompt: str) -> str:
        """Análisis con GPT-4 Vision (OpenAI) - Más potente para imágenes complejas"""
        try:
            if not self.openai_key:
                return "[Error] OpenAI API key no configurada"
            
            if not os.path.exists(image_path):
                return f"[Error] Archivo no encontrado: {image_path}"
            
            # Leer y codificar imagen en base64
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            
            # Detectar tipo MIME
            mime_type = self._get_mime_type(image_path)
            
            # Llamar a GPT-4 Vision
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.openai_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o",  # GPT-4 Omni (con visión)
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": prompt
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime_type};base64,{image_data}",
                                        "detail": "high"  # Alta resolución para mejor análisis
                                    }
                                }
                            ]
                        }
                    ],
                    "max_tokens": 2000,
                    "temperature": 0.7
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                return result['choices'][0]['message']['content']
            else:
                error_msg = response.json().get('error', {}).get('message', 'Error desconocido')
                return f"[OpenAI Vision Error] {error_msg}"
                
        except Exception as e:
            return f"[OpenAI Vision Error] {e}"
    
    def _analyze_anthropic(self, image_path: str, prompt: str) -> str:
        """Análisis con Claude Haiku Vision (Anthropic) - Rápido, barato, excelente para UI"""
        try:
            if not self.anthropic_key:
                return "[Error] Anthropic API key no configurada"

            if not os.path.exists(image_path):
                return f"[Error] Archivo no encontrado: {image_path}"

            # Leer y codificar imagen en base64
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')

            # Detectar formato de imagen
            mime_type = self._get_mime_type(image_path)

            # Llamar a Claude Haiku Vision (hardcoded — cheap & fast for vision)
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": _ANTHROPIC_VISION_MODEL,
                    "max_tokens": 2000,
                    # Haiku 5.5 piensa por default; para describir una imagen no
                    # hace falta y le suma latencia y tokens.
                    "thinking": ANTHROPIC_CLASSIFIER_THINKING_OFF,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": mime_type,
                                        "data": image_data
                                    }
                                },
                                {
                                    "type": "text",
                                    "text": prompt
                                }
                            ]
                        }
                    ]
                },
                timeout=30,
                verify=certifi.where()
            )
            
            if response.status_code == 200:
                return _anthropic_text(response.json())
            else:
                error_msg = response.json().get('error', {}).get('message', 'Error desconocido')
                return f"[Anthropic Vision Error] {error_msg}"
                
        except Exception as e:
            return f"[Anthropic Vision Error] {e}"

    def _analyze_cloud(self, image_path: str, prompt: str) -> str:
        """Alias para compatibilidad - usa estrategia auto"""
        return self.analyze(image_path, prompt, use_cloud=True, provider="auto")
    
    def _analyze_easyocr(self, image_path: str, prompt: str) -> str:
        """
        Extrae texto de imagen usando EasyOCR (local, rápido, gratis, estable).
        Ideal para: screenshots, gráficas, documentos, cualquier cosa con texto.
        """
        try:
            if not self.easy_ocr:
                return "[Error] EasyOCR no está disponible"
            
            if not os.path.exists(image_path):
                return f"[Error] Archivo no encontrado: {image_path}"
            
            # Ejecutar OCR
            results = self.easy_ocr.readtext(image_path)
            
            if not results:
                return "[EasyOCR] No se detectó texto en la imagen"
            
            # Extraer texto y confianzas
            extracted_texts = []
            confidences = []
            
            for (bbox, text, confidence) in results:
                extracted_texts.append(text)
                confidences.append(confidence)
            
            if not extracted_texts:
                return "[EasyOCR] No se pudo extraer texto"
            
            # Formatear resultado
            full_text = " ".join(extracted_texts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            
            # Construir respuesta contextual
            response = f"📝 Texto detectado en la imagen:\n\n{full_text}\n\n"
            response += f"✓ Confianza promedio: {avg_confidence:.1%}\n"
            response += f"✓ Fragmentos detectados: {len(extracted_texts)}"
            
            # Si el usuario hizo una pregunta específica, agregar contexto
            if "?" in prompt or any(word in prompt.lower() for word in ["qué", "cuál", "cuánto", "cómo", "dónde", "quién", "what", "how", "which"]):
                response += f"\n\n💡 Interpretación:\n"
                
                # Análisis simple basado en palabras clave
                text_lower = full_text.lower()
                if "deepseek" in text_lower or "api" in text_lower:
                    response += "La imagen muestra información sobre el uso de una API (probablemente DeepSeek)."
                if "token" in text_lower:
                    response += f"\n• Se mencionan tokens de uso."
                if "$" in full_text or "usd" in text_lower:
                    response += f"\n• Hay información de costos o balance."
                if any(num.isdigit() for num in extracted_texts):
                    numbers = [text for text in extracted_texts if any(c.isdigit() for c in text)]
                    response += f"\n• Valores numéricos detectados: {', '.join(numbers[:5])}"
            
            return response
            
        except Exception as e:
            return f"[EasyOCR Error] {e}"

    def _analyze_paddleocr(self, image_path: str, prompt: str) -> str:
        """
        Extrae texto de imagen usando PaddleOCR (local, rápido, gratis).
        Ideal para: screenshots, gráficas, documentos, cualquier cosa con texto.
        """
        try:
            if not self.paddle_ocr:
                return "[Error] PaddleOCR no está disponible"
            
            if not os.path.exists(image_path):
                return f"[Error] Archivo no encontrado: {image_path}"
            
            # Ejecutar OCR
            result = self.paddle_ocr.ocr(image_path, cls=True)
            
            if not result or not result[0]:
                return "[PaddleOCR] No se detectó texto en la imagen"
            
            # Extraer todo el texto
            extracted_texts = []
            confidences = []
            
            for line in result[0]:
                if line and len(line) >= 2:
                    text = line[1][0]  # Texto detectado
                    confidence = line[1][1]  # Confianza (0-1)
                    extracted_texts.append(text)
                    confidences.append(confidence)
            
            if not extracted_texts:
                return "[PaddleOCR] No se pudo extraer texto"
            
            # Formatear resultado
            full_text = " ".join(extracted_texts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            
            # Construir respuesta contextual
            response = f"Texto detectado en la imagen:\n\n{full_text}\n\n"
            response += f"(Confianza promedio: {avg_confidence:.1%})"
            
            # Si el usuario hizo una pregunta específica, intentar responderla
            if "?" in prompt or any(word in prompt.lower() for word in ["qué", "cuál", "cuánto", "cómo", "dónde", "quién"]):
                response += f"\n\nContexto de la pregunta '{prompt}':\n"
                response += f"El texto extraído muestra información sobre: {full_text[:200]}"
            
            return response
            
        except Exception as e:
            return f"[PaddleOCR Error] {e}"

    def _analyze_local(self, image_path: str, prompt: str) -> str:
        """Usa la API de Ollama para visión local."""
        try:
            # Verificar que el archivo exista
            if not os.path.exists(image_path):
                return f"[Error] Archivo no encontrado: {image_path}"
            
            with open(image_path, "rb") as f:
                img_base64 = base64.b64encode(f.read()).decode('utf-8')

            response = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": self.local_model,
                    "prompt": prompt,
                    "stream": False,
                    "images": [img_base64]
                },
                timeout=120  # Timeout aumentado a 120 segundos para imágenes complejas
            )
            
            if response.status_code != 200:
                return f"[Local Vision Error] Ollama respondió con código {response.status_code}"
            
            return response.json().get("response", "No pude analizar la imagen localmente.")
        except requests.exceptions.ConnectionError:
            return f"[Local Vision Error] No se puede conectar con Ollama. ¿Está corriendo? (puerto 11434)"
        except requests.exceptions.Timeout:
            return f"[Local Vision Error] Timeout al analizar imagen con {self.local_model}"
        except Exception as e:
            return f"[Local Vision Error] {e}"