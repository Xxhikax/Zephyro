# core/llm_manager.py
try:
    from langchain_ollama import OllamaLLM as Ollama
except ImportError:
    from langchain_community.llms import Ollama  # deprecated, remove after migration
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from core.llm_params import LLMParams
from core.model_catalog import (
    ANTHROPIC_DEFAULT_MODEL,
    ANTHROPIC_VALID_MODELS,
    DEEPSEEK_DEFAULT_MODEL,
    DEEPSEEK_THINKING_OFF,
    DEEPSEEK_VALID_MODELS,
    GEMINI_CHAT_MODEL,
    OPENAI_CHAT_MODEL,
    cloud_chat_model,
    resolve_anthropic_model,
    resolve_deepseek_model,
)
from typing import Optional, Literal
from dotenv import load_dotenv
import os
import threading
import time
import ollama

# Cargar variables de entorno
load_dotenv()

# Fix SSL: el HTTPTransport del SDK anthropic no carga certifi por defecto en Linux
# Esto afecta a ChatAnthropic (y AsyncAnthropic) que fallan con CERTIFICATE_VERIFY_FAILED
# Solo necesario en Linux (CachyOS/Arch tiene trust store incompleto); Windows funciona nativo.
import sys as _sys
if _sys.platform != "win32":
    try:
        import certifi as _certifi
        import anthropic._base_client as _a_bc
        _orig_sync_init = _a_bc._DefaultHttpxClient.__init__
        _orig_async_init = _a_bc._DefaultAsyncHttpxClient.__init__
        def _sync_init_certifi(self, **kwargs):
            kwargs.setdefault("verify", _certifi.where())
            _orig_sync_init(self, **kwargs)
        def _async_init_certifi(self, **kwargs):
            kwargs.setdefault("verify", _certifi.where())
            _orig_async_init(self, **kwargs)
        _a_bc._DefaultHttpxClient.__init__ = _sync_init_certifi
        _a_bc._DefaultAsyncHttpxClient.__init__ = _async_init_certifi
    except Exception:
        pass  # Si falla el patch, el SDK intentará conectar de todas formas

# DeepSeek N3 chat request timeout. 30s cut long non-streamed answers mid-generation
# and fell back to Ollama (often missing the model → user got an error).
DEEPSEEK_TIMEOUT_S = 60

class LLMManager:
    """Gestor híbrido de LLMs: local (Ollama) + cloud (OpenAI/Anthropic/Gemini/DeepSeek)"""
    
    def __init__(
        self, 
        local_model: str = "mistral:7b-instruct",
        cloud_provider: Literal["openai", "anthropic", "gemini", "deepseek", "auto"] = "auto",
        preset: str = "rapido"
    ):
        # IMPORTANTE: Guardar el nombre del modelo como string
        self.local_model_name = local_model

        # Configurar modelo local (graceful si Ollama no está instalado)
        # Modelos recomendados para futuro con GPU adecuada:
        #   - qwen2.5-coder:7b  (código, 8GB VRAM)
        #   - gemma3:4b         (general, 6GB VRAM)
        #   - llama3.2:8b       (general, 8GB VRAM)
        params = LLMParams.get_config(preset)
        self.ollama_available = False
        try:
            self.local_model = Ollama(
                model=local_model,
                temperature=params['temperature'],
                num_ctx=params['num_ctx'],
                num_predict=params['num_predict'],
                top_k=params['top_k'],
                top_p=params['top_p'],
                keep_alive="10m"
            )
            self.ollama_available = True
        except Exception:
            self.local_model = None
            print("[Info] Ollama no disponible — solo se usarán modelos cloud")

        # Configurar modelos cloud
        self.cloud_provider = cloud_provider
        self.cloud_model = None

        # Modelo Anthropic seleccionado (por defecto el equilibrado). Un ID legacy
        # en .env (p.ej. claude-sonnet-4-6) se remapea al tier vigente.
        self.anthropic_model = (
            resolve_anthropic_model(os.getenv("ANTHROPIC_MODEL")) or ANTHROPIC_DEFAULT_MODEL
        )
        # Modelo DeepSeek del chat N3 (flash por defecto). Los helpers de
        # llm_helpers (router/clasificadores) no lo leen: tienen su modelo fijo.
        self.deepseek_model = (
            resolve_deepseek_model(os.getenv("DEEPSEEK_MODEL")) or DEEPSEEK_DEFAULT_MODEL
        )

        # Rastrear último modelo usado
        self.last_model_used = None
        self.last_provider_used = None

        # Intentar inicializar cloud (si hay API keys)
        self._init_cloud_models()

        # Estado de conexión — verificar en background para no bloquear startup
        self.is_online = True  # optimistic default
        threading.Thread(target=self._async_internet_check, daemon=True).start()
    
    def _init_cloud_models(self):
        """Inicializar modelos cloud si hay API keys disponibles"""
        try:
            # Prioridad: Gemini > DeepSeek > OpenAI > Anthropic
            if self.cloud_provider in ["gemini", "auto"]:
                api_key = os.getenv("GOOGLE_API_KEY")
                if api_key and len(api_key) > 20:  # Validar que sea una key real
                    # Fix SSL CachyOS: pasar certifi directamente al cliente httpx de google-genai
                    _gemini_client_args = {}
                    try:
                        import certifi as _cert
                        _gemini_client_args = {"verify": _cert.where()}
                    except ImportError:
                        pass
                    self.cloud_model = ChatGoogleGenerativeAI(
                        model=GEMINI_CHAT_MODEL,
                        temperature=0.5,  # ← OPTIMIZADO: Menos creatividad = más velocidad
                        max_output_tokens=1500,  # Permite respuestas largas sin truncar (era 500, cortaba explicaciones)
                        timeout=10,  # ← NUEVO: Timeout de 10s para fallar rápido a local
                        max_retries=2,  # ← NUEVO: Solo 2 reintentos para no hacer esperar
                        google_api_key=api_key,
                        convert_system_message_to_human=True,
                        client_args=_gemini_client_args or None,
                    )
                    if self.cloud_provider == "auto":
                        self.cloud_provider = "gemini"
                    print(f"[Info] Gemini configurado (Gemini 2.5 Flash Lite, optimizado para velocidad)")
                    return
            
            if self.cloud_provider in ["deepseek", "auto"] and not self.cloud_model:
                api_key = os.getenv("DEEPSEEK_API_KEY")
                if api_key and api_key.startswith("sk-"):
                    try:
                        # deepseek-flash (V4.1) y v4-pro traen thinking ON por default:
                        # con el timeout de 30 s y temperature el request fallaba/expiraba.
                        # Para chat lo apagamos en ambos (mismo comportamiento que tenía
                        # el retirado deepseek-chat = V4-Flash non-thinking).
                        self.cloud_model = ChatOpenAI(
                            model=self.deepseek_model,
                            temperature=0.7,
                            api_key=api_key,
                            base_url="https://api.deepseek.com",
                            max_retries=0,  # ← CAMBIADO: Sin reintentos (fallback rápido a local)
                            timeout=DEEPSEEK_TIMEOUT_S,
                            extra_body=DEEPSEEK_THINKING_OFF,
                        )
                        if self.cloud_provider == "auto":
                            self.cloud_provider = "deepseek"
                        print(f"[Info] DeepSeek configurado correctamente (modelo: {self.deepseek_model}, thinking off)")
                        print(f"[Info] Timeout: {DEEPSEEK_TIMEOUT_S}s, sin reintentos (fallback a local si falla)")
                        return
                    except Exception as e:
                        print(f"[Warning] Error configurando DeepSeek: {e}")
                        self.cloud_model = None
            
            if self.cloud_provider in ["openai", "auto"] and not self.cloud_model:
                api_key = os.getenv("OPENAI_API_KEY")
                if api_key and api_key.startswith("sk-"):
                    self.cloud_model = ChatOpenAI(
                        model=OPENAI_CHAT_MODEL,
                        temperature=0.7,
                        api_key=api_key
                    )
                    if self.cloud_provider == "auto":
                        self.cloud_provider = "openai"
                    print("[Info] OpenAI configurado correctamente")
                    return
                        
            if self.cloud_provider in ["anthropic", "auto"] and not self.cloud_model:
                api_key = os.getenv("ANTHROPIC_API_KEY")
                if api_key and api_key.startswith("sk-ant-"):
                    # Sin temperature: en Claude 4.7+ (Sonnet 5 / Opus 5) un valor
                    # no-default devuelve 400. El tono se controla por prompt.
                    self.cloud_model = ChatAnthropic(
                        model=self.anthropic_model,
                        api_key=api_key,
                        max_retries=1,
                        # 120s (antes 30s): respuestas largas de conocimiento ("explicame el
                        # calentamiento global") tardaban >30s → APITimeoutError → fallback a
                        # Ollama → si Ollama está caído, respuesta VACÍA (call mode mudo).
                        # El "fail-fast a local" sólo sirve si hay local; con Anthropic como
                        # proveedor primario hay que darle tiempo a terminar.
                        timeout=120,
                        max_tokens=8192,
                    )
                    if self.cloud_provider == "auto":
                        self.cloud_provider = "anthropic"
                    print(f"[Info] Anthropic configurado correctamente (modelo: {self.anthropic_model})")
                    return
                    
        except Exception as e:
            print(f"[Info] Cloud models not available: {e}")
            self.cloud_model = None
    
    def get_status(self) -> dict:
        """
        Obtener información del estado actual del LLM Manager.
        Útil para debug y para que Zephyro sepa qué modelo está usando.

        Returns:
            dict con:
                - current_model: Nombre del modelo usado en la última generación
                - provider: Proveedor usado (local, gemini, deepseek, openai, anthropic)
                - is_online: Estado de conexión
                - cloud_available: Si cloud está disponible
                - local_available: Si Ollama está corriendo y responde
        """
        # Usar cache de 30s para evitar llamar ollama.list() en cada request
        now = time.time()
        if not hasattr(self, '_ollama_alive_cache') or (now - self._ollama_alive_cache_time) > 30:
            try:
                import ollama as _ol
                _ol.list()
                self._ollama_alive_cache = True
            except Exception:
                self._ollama_alive_cache = False
            self._ollama_alive_cache_time = now
        local_alive = self._ollama_alive_cache

        status = {
            "is_online": self.is_online,
            "cloud_available": self.cloud_model is not None,
            "local_available": local_alive,
            "cloud_provider": self.cloud_provider,
        }

        # Usar el último modelo que realmente se ejecutó
        if self.last_provider_used:
            status["provider"] = self.last_provider_used
            status["current_model"] = self.last_model_used or "unknown"
        elif self.cloud_model:
            # Fallback: si no hay historial, usar configuración actual
            status["provider"] = self.cloud_provider

            # Nombres de modelo desde core/model_catalog.py (fuente única)
            status["current_model"] = self._active_cloud_model_name() or "unknown-cloud"
        else:
            status["provider"] = "local"
            status["current_model"] = self.local_model_name

        return status
    
    def _async_internet_check(self):
        """Verificar conexión a internet en background (no bloquea startup)"""
        self.is_online = self._check_internet()

    def _check_internet(self) -> bool:
        """Verificar conexión a internet"""
        try:
            import requests
            requests.get("https://www.google.com", timeout=2)
            return True
        except:
            return False
    
    def _requires_cloud(self, prompt: str) -> bool:
        """
        Determinar si una tarea requiere cloud o puede hacerse local
        
        Criterios para usar cloud:
        - Tareas de razonamiento complejo
        - Generación de código largo
        - Análisis profundo
        - Traducción avanzada
        - Tareas creativas complejas
        """
        cloud_keywords = [
            "analiza en detalle", "genera código", "explica paso a paso",
            "escribe un programa", "crea una aplicación", "diseña una arquitectura",
            "compara en detalle", "ventajas y desventajas", "análisis profundo",
        ]

        prompt_lower = prompt.lower()

        # Solo prompts muy largos van a cloud automáticamente.
        # El prompt construido incluye system rules + historial, por eso el umbral
        # debe ser alto (≥1500) para no enrutar al cloud preguntas simples.
        if len(prompt) > 1500:
            return True

        # Buscar keywords que indican necesidad real de capacidad cloud
        return any(keyword in prompt_lower for keyword in cloud_keywords)

    @staticmethod
    def _auto_cloud_enabled() -> bool:
        """Verifica si el auto-enrutamiento a cloud está habilitado (AUTO_CLOUD env var)."""
        val = os.environ.get("AUTO_CLOUD", "true").lower()
        return val in ("1", "true", "yes", "on")
    
    def _clean_response(self, response) -> str:
        """Limpia la respuesta para obtener solo texto"""
        # Caso 1: Lista de objetos (response de gemini 2.x/3.x)
        if isinstance(response, list):
            texts = []
            for item in response:
                # Si es diccionario con campo 'text'
                if isinstance(item, dict) and 'text' in item:
                    texts.append(item['text'])
                # Si es objeto con atributo 'text' (generation object)
                elif hasattr(item, 'text'):
                    texts.append(item.text)
                # Si es AIMessage de LangChain dentro de lista
                elif hasattr(item, 'content'):
                    texts.append(item.content)
                # Si es string directo
                elif isinstance(item, str):
                    texts.append(item)
            return " ".join(texts)
            
        # Caso 2: Objeto AIMessage de LangChain (standard)
        if hasattr(response, 'content'):
            # A veces content es una lista también en los modelos nuevos
            if isinstance(response.content, list):
                 return self._clean_response(response.content)
            return str(response.content)
            
        # Caso 3: String directo
        return str(response)

    def _track_cloud_usage(self, response, latency_ms: int) -> None:
        """Registra una llamada cloud en el api_usage_store. Nunca lanza."""
        try:
            from core.api_usage_store import (
                get_usage_store, extract_langchain_usage, estimate_call_cost,
            )
            provider = self.last_provider_used or self.cloud_provider
            model = self.last_model_used
            tokens_in, tokens_out = extract_langchain_usage(response, provider)
            cost = estimate_call_cost(provider, model, tokens_in, tokens_out)
            get_usage_store().record(
                provider=provider,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                cost_usd=cost,
                success=True,
            )
        except Exception:
            pass  # tracking jamás debe romper la generación

    def _reasoning_kwargs(self, reasoning) -> dict:
        """Per-call kwargs for the cloud chat model from a ReasoningDecision.

        {} keeps today's request byte-identical: no decision, or a level that keeps
        DeepSeek thinking off. Claude's effort is always explicit when there is a
        decision: the default effort differs per model (Opus/Haiku vs Sonnet), and
        every catalog model accepts low..max (models.retrieve, 2026-10-26).
        """
        if reasoning is None:
            return {}
        if self.cloud_provider == "anthropic":
            return {"output_config": {"effort": reasoning.claude_effort}}
        if self.cloud_provider == "deepseek" and reasoning.deepseek_thinking:
            # Per-call extra_body REPLACES the client's (thinking off), so send it whole.
            return {"extra_body": {"thinking": {"type": "enabled"},
                                   "reasoning_effort": reasoning.deepseek_thinking}}
        return {}

    def generate(self, prompt: str, force_local: bool = False, force_cloud: bool = False, preset: str = None,
                 reasoning=None) -> str:
        """
        Generar respuesta usando estrategia híbrida

        Args:
            prompt: Texto de entrada
            force_local: Forzar uso de modelo local
            force_cloud: Forzar uso de modelo cloud
            preset: Preset de parámetros ('rapido', 'balanceado', 'calidad') - solo para local
            reasoning: core.reasoning_policy.ReasoningDecision for the cloud call (None = default)

        Returns:
            Respuesta generada
        """
        # Validación: si se fuerza cloud pero no está disponible
        if force_cloud:
            if not self.cloud_model:
                raise ValueError(
                    "❌ Modelo cloud no disponible. "
                    "Configura GOOGLE_API_KEY, OPENAI_API_KEY o ANTHROPIC_API_KEY en .env"
                )
            if not self.is_online:
                raise ValueError(
                    "❌ No hay conexión a internet. "
                    "No se puede usar el modelo cloud."
                )
        
        # Determinar qué modelo usar
        use_cloud = False
        
        if force_cloud:
            use_cloud = True
        elif force_local:
            use_cloud = False
        else:
            # Lógica automática: ¿necesita cloud?
            # Solo auto-enruta a cloud si AUTO_CLOUD=true (default yes)
            if self.cloud_model and self.is_online and self._requires_cloud(prompt):
                if LLMManager._auto_cloud_enabled():
                    use_cloud = True
        
        # Generar respuesta
        try:
            if use_cloud:
                print(f"[Info] Usando modelo cloud ({self.cloud_provider})...")
                try:
                    _t0 = time.monotonic()
                    response = self.cloud_model.invoke(prompt, **self._reasoning_kwargs(reasoning))
                    _latency_ms = int((time.monotonic() - _t0) * 1000)

                    # Registrar el modelo usado
                    self.last_provider_used = self.cloud_provider
                    self.last_model_used = self._active_cloud_model_name()

                    self._track_cloud_usage(response, _latency_ms)
                    return self._clean_response(response)
                except Exception as cloud_error:
                    error_msg = str(cloud_error)
                    cause = getattr(cloud_error, '__cause__', None)
                    if cause and "SSL" in str(cause):
                        print(f"[red]⚠ {self.cloud_provider.upper()}: Error SSL — {str(cause)[:100]}[/red]")

                    
                    # Error 402: Saldo insuficiente (DeepSeek)
                    if "402" in error_msg or "insufficient balance" in error_msg.lower():
                        print(f"[red]⚠ {self.cloud_provider.upper()}: Saldo insuficiente en tu cuenta[/red]")
                        if self.cloud_provider == "deepseek":
                            print(f"[yellow]  → Verifica tu saldo en: https://platform.deepseek.com/usage[/yellow]")
                            print(f"[yellow]  → Recarga créditos en: https://platform.deepseek.com/top_up[/yellow]")
                        print(f"[cyan]  → Usando modelo local como alternativa...[/cyan]")
                    # Error 503: Servidor sobrecargado
                    elif "503" in error_msg or "overloaded" in error_msg.lower():
                        print(f"[yellow]⚠ {self.cloud_provider.upper()} sobrecargado, usando modelo local...[/yellow]")
                    # Error 429: Rate limit
                    elif "429" in error_msg or "rate limit" in error_msg.lower():
                        print(f"[yellow]⚠ Rate limit alcanzado, usando modelo local...[/yellow]")
                    # Error 401: API key inválida
                    elif "401" in error_msg or "unauthorized" in error_msg.lower():
                        print(f"[red]⚠ {self.cloud_provider.upper()}: API key inválida o expirada[/red]")
                        print(f"[yellow]  → Verifica tu API key en el archivo .env[/yellow]")
                    # Otros errores
                    else:
                        print(f"[yellow]⚠ Error cloud: {error_msg[:120]} → Fallback a local[/yellow]")
                    
                    # Fallback silencioso a local
                    if not self.ollama_available:
                        return "Lo siento, no pude procesar eso. El modelo cloud falló y no hay modelo local disponible."
                    response = self.local_model.invoke(prompt)
                    self.last_provider_used = "local"
                    self.last_model_used = self.local_model_name
                    return self._clean_response(response)
            else:
                if not self.ollama_available:
                    # Sin Ollama, redirigir a cloud si está disponible
                    if self.cloud_model and self.is_online:
                        print("[Info] Ollama no disponible, redirigiendo a cloud...")
                        response = self.cloud_model.invoke(prompt, **self._reasoning_kwargs(reasoning))
                        self.last_provider_used = self.cloud_provider
                        return self._clean_response(response)
                    return "No hay modelo disponible. Instala Ollama o configura API keys cloud en .env"
                if preset:
                    # SOLUCIÓN DEFINITIVA: Librería oficial Ollama con streaming controlado
                    from core.llm_params import LLMParams
                    
                    params = LLMParams.get_config(preset)
                    print(f"[Info] Usando Ollama con preset '{preset}' (num_predict={params['num_predict']})...")
                    
                    try:
                        # Opciones con stop sequences
                        options = {
                            "temperature": params['temperature'],
                            "num_ctx": params['num_ctx'],
                            "num_predict": params['num_predict'],
                            "top_k": params['top_k'],
                            "top_p": params['top_p']
                        }
                        
                        # Agregar stop sequences si existen
                        if 'stop' in params and params['stop']:
                            options['stop'] = params['stop']
                        
                        # Streaming con control de tokens
                        response_text = ""
                        token_count = 0
                        max_tokens = params['num_predict']
                        
                        print(f"[Debug] Llamando ollama.generate con model={self.local_model_name}")
                        # Separar instrucciones del sistema de la conversación para que
                        # los modelos instruct reciban cada parte en el canal correcto.
                        _marker = "RECIENTE:"
                        if _marker in prompt:
                            _idx = prompt.index(_marker)
                            system_part = prompt[:_idx].strip()
                            if system_part.startswith("SYSTEM:"):
                                system_part = system_part[len("SYSTEM:"):].strip()
                            conv_part = prompt[_idx:]
                        else:
                            system_part = None
                            conv_part = prompt
                        stream = ollama.generate(
                            model=self.local_model_name,
                            prompt=conv_part,
                            system=system_part,
                            stream=True,
                            options=options
                        )
                        
                        for chunk in stream:
                            if 'response' in chunk:
                                response_text += chunk['response']
                                token_count += 1
                                
                                # Hard limit: detener si excede tokens (seguridad)
                                if token_count >= max_tokens * 1.2:  # 20% margen
                                    break
                                
                                # Stop sequence check
                                if 'stop' in params and params['stop']:
                                    for stop_seq in params['stop']:
                                        if stop_seq in response_text:
                                            response_text = response_text.split(stop_seq)[0]
                                            return response_text.strip()
                        
                        print(f"[Debug] Generación completada. Tokens: {token_count}")
                        self.last_provider_used = "local"
                        self.last_model_used = self.local_model_name
                        return response_text.strip()
                        
                    except Exception as e:
                        print(f"[Error] Error con Ollama streaming: {e}")
                        import traceback
                        traceback.print_exc()
                        print("[Info] Fallback a LangChain Ollama...")
                        response = self.local_model.invoke(prompt)
                        self.last_provider_used = "local"
                        self.last_model_used = self.local_model_name
                        return self._clean_response(response)
                else:
                    print("[Info] Usando modelo local (LangChain)...")
                    response = self.local_model.invoke(prompt)
                    self.last_provider_used = "local"
                    self.last_model_used = self.local_model_name
                    return self._clean_response(response)
        except Exception as e:
            # Si llegamos aquí es un error no manejado arriba
            print(f"[red]✗ Error inesperado: {str(e)[:100]}[/red]")
            # Último fallback: probar local SÓLO si Ollama responde. Si está caído, no
            # reintentar contra el puerto muerto (sumaba latencia por turno) — re-lanzar.
            from core.ollama_health import is_ollama_up
            if use_cloud and is_ollama_up():
                print("[yellow]→ Intentando modelo local...[/yellow]")
                self.last_provider_used = "local"
                self.last_model_used = self.local_model_name
                return self._clean_response(self.local_model.invoke(prompt))
            raise
    
    def generate_stream(
        self,
        prompt: str,
        force_cloud: bool = False,
        force_local: bool = False,
        preset: str = None,
        reasoning=None,
    ):
        """
        Streaming token-by-token. Devuelve un iterable de strings (tokens/chunks).
        `reasoning` (ReasoningDecision) sets effort/thinking for the cloud call.

        Soporta:
          - Cloud (Gemini/OpenAI/Anthropic/DeepSeek): vía LangChain .stream()
          - Local (Ollama): vía ollama.generate(stream=True)

        El llamador es responsable de acumular tokens en frases antes de enviarlas
        al TTS (ver _generate_with_streaming_tts en orchestrator.py).
        """
        use_cloud = force_cloud or (
            not force_local and self.cloud_model and self.is_online
            and self._requires_cloud(prompt)
        )

        # Fallbacks may land on the cloud again (no Ollama): they keep the decision.
        _rkw = {"reasoning": reasoning} if reasoning is not None else {}
        if use_cloud and self.cloud_model:
            self.last_provider_used = self.cloud_provider
            self.last_model_used = self._active_cloud_model_name()
            emitted = False
            try:
                for chunk in self.cloud_model.stream(prompt, **self._reasoning_kwargs(reasoning)):
                    # LangChain devuelve AIMessageChunk; .content puede ser str o list
                    content = chunk.content if hasattr(chunk, "content") else str(chunk)
                    if isinstance(content, list):
                        # Gemini/Anthropic devuelven lista de partes (thinking parts → sin "text")
                        for part in content:
                            text = part.get("text", "") if isinstance(part, dict) else str(part)
                            if text:
                                emitted = True
                                yield text
                    elif content:
                        emitted = True
                        yield content
            except Exception as e:
                if emitted:
                    # Part of the answer was already yielded (and possibly spoken): restarting
                    # on the local model would append a second, different full answer.
                    print(f"[Warning] generate_stream cloud error mid-stream ({e}) — answer truncated")
                    return
                print(f"[Warning] generate_stream cloud error ({e}), fallback a local")
                if not self.ollama_available:
                    # Without Ollama, generate() goes back to the cloud: keep the decision.
                    yield self.generate(prompt, force_local=True, **_rkw)
                    return
                yield from self._stream_ollama(prompt, preset, **_rkw)
        else:
            yield from self._stream_ollama(prompt, preset, **_rkw)

    def _stream_ollama(self, prompt: str, preset: str = None, reasoning=None):
        """Iterable de tokens desde Ollama."""
        if not self.ollama_available:
            yield self.generate(prompt, **({"reasoning": reasoning} if reasoning is not None else {}))
            return
        from core.llm_params import LLMParams
        params = LLMParams.get_config(preset or "balanceado")
        options = {
            "temperature": params["temperature"],
            "num_ctx": params["num_ctx"],
            "num_predict": params["num_predict"],
            "top_k": params["top_k"],
            "top_p": params["top_p"],
        }
        self.last_provider_used = "local"
        self.last_model_used = self.local_model_name
        for chunk in ollama.generate(
            model=self.local_model_name,
            prompt=prompt,
            stream=True,
            options=options,
        ):
            if "response" in chunk:
                yield chunk["response"]

    def generate_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: list,
        tool_executor,
        max_iterations: int = 8,
        reasoning=None,
    ) -> str:
        """
        Tool use loop usando Anthropic SDK raw.
        Diseñado para correr en asyncio.to_thread() — nunca llamar desde el event loop.

        Args:
            system_prompt: Prompt de sistema (contexto completo construido por build_prompt)
            user_message: Mensaje actual del usuario
            tools: Lista de dicts con definiciones de tools (formato Anthropic)
            tool_executor: async callable(name: str, input: dict) -> str
            max_iterations: Máximo de rondas tool_use (evita loops infinitos)
            reasoning: ReasoningDecision; its level becomes Claude's effort (None = default)

        Returns:
            Respuesta final en texto
        """
        import asyncio
        import anthropic as _anthropic
        from core.prompt_cache import supports_caching, StructuredSystemPrompt

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY no configurado — no se puede usar tool use")

        client = _anthropic.Anthropic(api_key=api_key)
        messages = [{"role": "user", "content": user_message}]

        # Build system as structured blocks when Anthropic caching is available.
        # The full system prompt (identity + memory context) is marked ephemeral so
        # consecutive calls within the 5-min TTL window hit the cache (~90% cost reduction).
        if supports_caching(self.cloud_provider):
            _sp = StructuredSystemPrompt()
            _sp.add_cacheable(system_prompt, "system")
            _system_arg = _sp.to_anthropic_content()
        else:
            _system_arg = system_prompt

        # This loop always talks to Claude, whatever the chat provider is.
        _effort = (
            {"output_config": {"effort": reasoning.claude_effort}} if reasoning is not None else {}
        )

        for iteration in range(max_iterations):
            response = client.messages.create(
                model=self.anthropic_model,
                system=_system_arg,
                messages=messages,
                tools=tools,
                max_tokens=8192,
                **_effort,
            )

            if response.stop_reason == "end_turn":
                self.last_provider_used = "anthropic"
                self.last_model_used = self.anthropic_model
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return ""

            if response.stop_reason == "tool_use":
                # Añadir respuesta del asistente al historial
                messages.append({"role": "assistant", "content": response.content})

                # Ejecutar cada tool call y recoger resultados
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        print(f"[Tool Use] {block.name}({block.input})")
                        try:
                            # asyncio.run() es seguro desde un thread (no hay event loop activo aquí)
                            result = asyncio.run(tool_executor(block.name, block.input))
                        except Exception as e:
                            result = f"Error ejecutando {block.name}: {e}"
                            print(f"[Tool Use] Error: {e}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result
                        })

                messages.append({"role": "user", "content": tool_results})
            else:
                # stop_reason inesperado (max_tokens, etc.)
                print(f"[Tool Use] stop_reason inesperado: {response.stop_reason}")
                break

        # Si llegamos aquí: se agotaron iteraciones o stop_reason inesperado.
        # Intentar extraer texto de la última respuesta si existe.
        try:
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    self.last_provider_used = "anthropic"
                    self.last_model_used = self.anthropic_model
                    return block.text
        except Exception:
            pass

        # Última oportunidad: llamada de síntesis sin tools usando el historial acumulado.
        # Claude puede sintetizar la info que ya recuperó aunque no haya generado end_turn.
        try:
            print("[Tool Use] Intentando síntesis final sin tools...")
            _synth_suffix = (
                "\n\nINSTRUCCIÓN DE SÍNTESIS: Ya realizaste búsquedas previas. "
                "Con la información obtenida, responde ahora directamente al usuario "
                "en español de forma completa y útil, aunque la información sea parcial."
            )
            if supports_caching(self.cloud_provider):
                _synth_sp = StructuredSystemPrompt()
                _synth_sp.add_cacheable(system_prompt, "system")
                _synth_sp.add_dynamic(_synth_suffix, "synth_instr")
                _synth_system = _synth_sp.to_anthropic_content()
            else:
                _synth_system = system_prompt + _synth_suffix
            synthesis_response = client.messages.create(
                model=self.anthropic_model,
                system=_synth_system,
                messages=messages,
                max_tokens=8192,
                **_effort,
            )
            for block in synthesis_response.content:
                if hasattr(block, "text") and block.text:
                    self.last_provider_used = "anthropic"
                    self.last_model_used = self.anthropic_model
                    return block.text
        except Exception as synth_err:
            print(f"[Tool Use] Síntesis final falló: {synth_err}")

        return "No se pudo completar la respuesta con las herramientas disponibles."

    def stream(self, prompt: str, force_local: bool = False):
        """Generar respuesta en streaming (solo local por ahora)"""
        print("[Info] Streaming con modelo local...")
        return self.local_model.stream(prompt)
    
    def set_cloud_provider(self, provider: Literal["openai", "anthropic", "gemini", "deepseek", "auto"]):
        """Cambiar el proveedor cloud dinámicamente"""
        self.cloud_provider = provider
        self.cloud_model = None
        self.last_provider_used = None  # Reset so get_status() returns the new provider
        self._init_cloud_models()
        return self.get_status()

    def set_anthropic_model(self, model: str):
        """Cambiar el modelo de Anthropic dinámicamente (IDs legacy se remapean)."""
        resolved = resolve_anthropic_model(model)
        if resolved is None:
            raise ValueError(f"Modelo no válido. Opciones: {ANTHROPIC_VALID_MODELS}")
        model = resolved
        self.anthropic_model = model
        os.environ["ANTHROPIC_MODEL"] = model  # Propagar a otros módulos (ej: vision)
        # Reinicializar solo si anthropic es el proveedor activo
        if self.cloud_provider == "anthropic":
            self.cloud_model = None
            self._init_cloud_models()
        return self.get_status()

    def set_deepseek_model(self, model: str):
        """Cambiar el modelo DeepSeek del chat N3 (IDs legacy se remapean)."""
        resolved = resolve_deepseek_model(model)
        if resolved is None:
            raise ValueError(f"Modelo no válido. Opciones: {DEEPSEEK_VALID_MODELS}")
        self.deepseek_model = resolved
        if self.cloud_provider == "deepseek":
            self.cloud_model = None
            self._init_cloud_models()
        return self.get_status()

    def _active_cloud_model_name(self) -> Optional[str]:
        """ID del modelo que corre el chat N3 para el provider cloud activo."""
        if self.cloud_provider == "anthropic":
            return self.anthropic_model
        if self.cloud_provider == "deepseek":
            return self.deepseek_model
        return cloud_chat_model(self.cloud_provider)
    
