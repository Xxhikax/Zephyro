from collections import defaultdict, deque
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime
import traceback
import os
import json
from modules.memory.vector_store import VectorStore

try:
    from modules.memory.pgvector_store import PGVectorStore
    HAS_PG = True
except Exception:
    HAS_PG = False

# Reglas de formato enriquecido: solo para respuestas EN PANTALLA (not speak).
# El frontend renderiza Markdown (marked) y LaTeX (KaTeX) vía RichRender.
_RICH_TEXT_RULES = (
    "\nFORMATO ENRIQUECIDO (la interfaz renderiza Markdown y LaTeX):\n"
    "- Si comparás opciones u organizás datos (2+ ítems con 2+ atributos), usá una tabla Markdown.\n"
    "- Números grandes: separador de miles y coma decimal (1.234.567,89); en tablas alineá los "
    "decimales; incluí unidades explícitas.\n"
    "- Matemática real (fórmulas, ecuaciones, fracciones): LaTeX entre $...$ (inline) o $$...$$ "
    "(bloque). NO uses LaTeX para precios ni números sueltos.\n"
    "- Código en bloques ``` con el lenguaje indicado.\n"
    "- Prosa normal para respuestas simples — no fuerces formato donde no aporta."
)

# Diagramas Mermaid: solo tier cloud — los modelos locales (1b/3b) emiten
# sintaxis mermaid rota y el fallback visual (code block + badge) ensucia.
_MERMAID_RULE = (
    "\n- Para flujos, procesos o arquitecturas podés usar un bloque ```mermaid "
    "(flowchart TD, sequenceDiagram). Solo cuando un diagrama realmente aclare."
)

# Enseñanza de algoritmos/estructuras de datos: combina pseudocódigo, diagrama
# mermaid, tabla de traza y complejidad LaTeX. Solo cloud (necesita mermaid).
_ALGORITHM_RULE = (
    "\n- Al ENSEÑAR un algoritmo o estructura de datos, empezá SIEMPRE con el concepto: "
    "qué es, qué problema resuelve, la idea central en la que se basa y cuándo conviene "
    "usarlo (2-4 oraciones, nivel teórico, sin código). Recién después mostrá: "
    "(1) pseudocódigo en bloque ```; (2) un diagrama ```mermaid — flowchart TD para el "
    "flujo, o graph TD para grafos/árboles/estructuras enlazadas; (3) una tabla de TRAZA "
    "paso a paso sobre un ejemplo chico (columnas: paso, estado de la estructura, "
    "explicación) para mostrar cómo se comporta; (4) la complejidad en LaTeX "
    "($O(n \\log n)$) y una línea de cuándo usarlo frente a alternativas."
)

# Charts matplotlib: solo tier cloud. El backend renderiza el spec a PNG
# (modules/charts/renderer.py); JSON declarativo, jamás código.
_CHART_RULE = (
    '\n- SOLO si el usuario pide visualizar datos numéricos, podés emitir un bloque '
    '```chart con JSON: {"type": "line|bar|pie|scatter", "title": "...", '
    '"labels": ["eje x"], "series": [{"label": "serie", "values": [1, 2]}], '
    '"xlabel": "...", "ylabel": "..."}. El sistema lo dibuja como imagen.'
)

# Excepción para voz (call mode) SOLO en tier cloud: la tabla se muestra en el
# transcript en pantalla y el pipeline TTS la suprime del audio.
_VOICE_TABLE_EXCEPTION = (
    "\nEXCEPCIÓN VISUAL: si la respuesta compara varias opciones, podés añadir AL FINAL, "
    "después de tu respuesta hablada completa, UNA tabla Markdown compacta (máximo 6 filas). "
    "La tabla solo se muestra en pantalla; jamás la menciones ni la leas en voz alta."
)

_TABLE_HINT = (
    "\nEl usuario pide una comparación: presentá la comparación como tabla Markdown."
)

class ContextManager:
    def __init__(self, max_history=5, use_vector_memory=True):
        self.history: deque = deque(maxlen=max_history * 2)
        self.max_history = max_history
        self.use_vector_memory = use_vector_memory
        self.vector_store = None
        self.last_automation: Optional[Dict[str, Any]] = None
        self.latency_history: Dict[str, List[float]] = defaultdict(list)
        self.max_latency_samples = max_history * 10
        self.last_system_info: Optional[Dict[str, Any]] = None  # Almacenar info del sistema
        self._current_session_id: Optional[str] = None

        # Cargar especialidades desde archivo JSON si existe
        self.specialties: Dict[str, str] = {}
        try:
            specialties_path = os.path.join(os.path.dirname(__file__), "specialties.json")
            if os.path.exists(specialties_path):
                with open(specialties_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    # _labels is optional; keep entire object but expect keys mapping to strings
                    for k, v in loaded.items():
                        self.specialties[k] = v
                print(f"[Context] Especialidades cargadas: {list(self.specialties.keys())}")
        except Exception as e:
            print(f"[Warning] No se pudieron cargar especialidades: {e}")

        if self.use_vector_memory:
            try:
                if HAS_PG:
                    self.vector_store = PGVectorStore()
                    print("[Memory] Usando PostgreSQL (pgvector) como base de datos.")
                else:
                    self.vector_store = VectorStore()
                    print("[Memory] Usando almacenamiento local (JSON) - pgvector no disponible.")
            except Exception as e:
                print(f"[Warning] Error inicializando memoria: {e}")
                if HAS_PG:
                    try:
                        self.vector_store = VectorStore()
                        print("[Memory] Fallback exitoso a memoria local.")
                    except Exception:
                        self.use_vector_memory = False
                else:
                    self.use_vector_memory = False

        # Clasificador de importancia para filtrar qué guardar en memoria vectorial.
        # Se inyecta externamente vía set_importance_classifier() desde Orchestrator.
        # Inicialización lazy para evitar doble instanciación (bug P2).
        self.importance_classifier = None

    def get_recent_conversations(self, n: int = 20) -> list:
        """Últimos n intercambios user/assistant como dicts para el ReflectionEngine.

        Shape esperado por ReflectionEngine._build_prompt:
        {"user_input": ..., "response": ...}. Empareja cada mensaje de usuario
        con la respuesta del asistente que le sigue; ignora turnos sin par.
        """
        pairs: list = []
        pending_user = None
        for role, content in self.history:
            if role == "user":
                pending_user = content
            elif role == "assistant" and pending_user is not None:
                pairs.append({"user_input": pending_user, "response": content})
                pending_user = None
        return pairs[-n:]

    def add(self, role: str, content: str, skip_save: bool = False):
        """Añade interacción al historial y opcionalmente a la memoria persistente"""
        # 1. Memoria RAM (Corto Plazo) — deque con maxlen, trim automático O(1)
        self.history.append((role, content))
    
        # 2. Memoria Vectorial (Largo Plazo) - MEJORADO CON FILTROS
        if skip_save or not self.use_vector_memory or not self.vector_store:
            return
        
        # FILTROS INTELIGENTES: Solo guardar contenido significativo
        content_lower = content.lower()
        
        # Excluir saludos/despedidas triviales
        trivial_patterns = [
            "hola", "hey", "buenas", "adiós", "chao", "gracias", 
            "de nada", "ok", "vale", "claro", "sí", "no"
        ]
        if len(content.strip()) < 20 and any(pattern in content_lower for pattern in trivial_patterns):
            return  # No guardar saludos simples
        
        # Excluir respuestas que mencionan software irrelevante (alucinaciones)
        hallucination_keywords = ["libreoffice", "calc", "excel", "irrelevante"]
        if role == "assistant" and any(kw in content_lower for kw in hallucination_keywords):
            return  # No guardar alucinaciones
        
        # Solo guardar si es suficientemente largo y relevante
        if len(content.strip()) > 30:
            # Para mensajes del asistente: usar ImportanceClassifier con el último input del usuario
            if role == "assistant" and self.importance_classifier is not None:
                last_user_msg = next(
                    (c for r, c in reversed(list(self.history)[:-1]) if r == "user"), ""
                )
                level, score, reasoning = self.importance_classifier.classify(
                    user_input=last_user_msg,
                    assistant_response=content,
                    response_length=len(content)
                )
                if not self.importance_classifier.should_save_to_memory(level, score):
                    return  # Interacción trivial, no guardar
                memory_type = "fact" if level == "critical" else "conversation"
            elif role == "assistant":
                memory_type = "conversation"
            else:
                # Para mensajes del usuario: guardar siempre que sean suficientemente largos
                memory_type = "conversation"

            try:
                self.vector_store.add_memory(
                    text=content,
                    metadata={"role": role},
                    role=role,
                    memory_type=memory_type
                )
                if role == "assistant":
                    preview = content[:50] + "..." if len(content) > 50 else content
                    print(f"[Memory] [{level}|{score:.2f}] Guardando: {preview}")
            except Exception as e:
                print(f"[Warning] No se pudo guardar memoria: {e}")

    async def get_vector_context(self, query: str, k: int = 1) -> str:
        """
        Busca contexto vectorial de forma asíncrona sin bloquear el event loop.
        Usar antes de build_prompt() y pasar el resultado como precomputed_memory.
        """
        if not self.use_vector_memory or not self.vector_store:
            return ""
        try:
            return await self.vector_store.async_search_relevant_context(query, k=k)
        except Exception:
            return ""

    def build_prompt(self, user_input: str, skip_memory: bool = False, precomputed_memory: Optional[str] = None, template: Optional[str] = None, detail_level: Optional[str] = None, specialty_mode: Optional[str] = None, system_info: Optional[Dict[str, Any]] = None, self_state: Optional[Any] = None, self_state_context: Optional[str] = None, code_context: Optional[str] = None, speak: bool = False, user_profile: Optional[Any] = None, few_shot_examples: str = "", reflection: str = "", style_instructions: str = "", skill_summaries: str = "", device_hint: str = "", vision_context: Optional[str] = None, rich_hint: Optional[str] = None, cloud_tier: bool = False) -> str:
        """
        Construye el prompt para el LLM.
        
        Args:
            user_input: Entrada del usuario
            skip_memory: Si True, omite búsqueda en memoria vectorial (para fast-path)
            template: Plantilla específica para respuestas triviales
            detail_level: Nivel de detalle para template 'concept'
            specialty_mode: Modo especialidad ('educational_assistant')
            system_info: Información del sistema actual
            self_state: Instancia de SelfState para inyección contextual
            self_state_context: Contexto específico de capacidades (pre-calculado)
        """
        
        if system_info:
            self.last_system_info = system_info
        
        system_context = ""
        if self.last_system_info:
            system_context = self._build_system_context(self.last_system_info)
        
        # PRIORIDAD 1: Modo especialidad (se mantiene igual)
        if specialty_mode == "educational_assistant":
            # Prioridad: usar specialties.json si tiene la clave; si no, usar el hardcoded
            if specialty_mode in self.specialties:
                system_rules = self.specialties[specialty_mode]
            else:
                system_rules = (
                    "Eres un Asistente Técnico Educativo. Explicas proyectos de IA y hardware a personas con pocos o sin conocimientos técnicos.\n\n"
                    "AUDIENCIA: Padre precavido con el dinero, escéptico sobre videojuegos, valora utilidad educativa, tu objetivo es convencerlo de la necesidad técnica se persistente sin sonar insistente.\n\n"
                    "CONTEXTO DEL PROYECTO:\n"
                    "- Sistema: Zephyro, asistente de IA local desarrollado para aprendizaje de machine learning\n"
                    "- Hardware actual: CPU i5 10th gen, 16GB RAM, SSD 512GB, GTX1050TI\n"
                    "- Situación actual: Los modelos de IA local tardan 30-60 segundos por respuesta , los usos con api son temporales(o para usos de trabajo complejo no tareas del dia a dia) y cada vez se vuelven mas caras (impracticable para uso real)\n"
                    "- Solución técnica: RTX 5060 (16GB VRAM), memoria SSD 1TB, RAM DDR4 32GB reduce esto a 2-5 segundos\n"
                    "- Por qué GPU: Los modelos de IA requieren procesamiento paralelo masivo que solo GPUs pueden hacer\n"
                    "- Esta es la GPU mínima viable para IA local, no un lujo\n"
                    "- Uso: Desarrollo de software de IA, fine-tuning, procesamiento de lenguaje natural\n"
                    "- NO es para juegos, las GPUs son calculadoras especializadas para IA\n\n"
                    "FORMATO DE RESPUESTA:\n"
                    "- Responde EXACTAMENTE lo que preguntan\n"
                    "- Máximo 3 o 5 párrafos cortos \n"
                    "- Sé claro sobre la necesidad técnica sin exagerar ni minimizar\n"
                    "- Usa analogías SOLO si realmente clarifican (no en cada respuesta)\n"
                    "- Varía las analogías si necesitas repetirlas (herramientas de laboratorio, instrumentos científicos)\n"
                    "- NO menciones alternativas más baratas que no sirven para el propósito\n\n"
                    "REGLAS CRÍTICAS:\n"
                    "1. Si preguntan '¿es necesaria?' → Explica que para IA local práctica, SÍ (30-60s es impracticable)\n"
                    "2. Si preguntan '¿es cara?' → Explica que es la opción mínima viable, no un lujo\n"
                    "3. Si mencionan '¿por qué no CPU?' → Explica que CPU no tiene capacidad de procesamiento paralelo masivo\n"
                    "4. Si dudan → Usa una analogía apropiada SOLO si ayuda (varía las analogías, no repitas la misma)\n"
                    "5. NO digas 'se puede hacer sin GPU' o 'no es necesario' → Contradice los hechos técnicos\n"
                    "6. Cuando hablen de GPU → Es para acelerar IA, NO para juegos (aclararlo explícitamente)\n"
                    "7. NO ofrezcas alternativas más baratas que no cumplan el propósito técnico\n"
                    "8. Si preguntan '¿cuánto tiempo útil?' → Habla de duración/vida útil: 5-7 años para IA, útil durante universidad y primeros años laborales, tecnología CUDA compatible con futuras actualizaciones\n\n"
                    "TONO: Buscas ayudar al usuario, técnico,formal. Como ingeniero explicando requisitos mínimos de laboratorio.\n"
                    "Reconoce preocupaciones, pero sé claro: esta es la herramienta correcta para el trabajo específico."
                )

            if speak:
                system_rules += (
                    "\nIMPORTANTE: Tu respuesta será leída EN VOZ ALTA por síntesis de voz. "
                    "PROHIBIDO: markdown, asteriscos, guiones de lista, # encabezados, URLs, bloques de código. "
                    "Usa frases completas y naturales como si hablaras, no escribieras."
                )

            prompt_parts = [f"SYSTEM: {system_rules}\n"]

            # Buscar memoria UNA sola vez (usar precomputed si ya fue prefetcheado async)
            if not skip_memory and self.use_vector_memory and self.vector_store:
                try:
                    if precomputed_memory is not None:
                        memories = precomputed_memory
                    else:
                        memories = self.vector_store.search_relevant_context(user_input, k=2)
                    if memories and len(memories.strip()) > 10:
                        prompt_parts.append(f"CONVERSACIONES PREVIAS SIMILARES:\n{memories}\n")
                except Exception:
                    pass

            prompt_parts.append(f"Usuario: {user_input}")
            prompt_parts.append(f"Asistente ({specialty_mode}):")
            return "\n".join(prompt_parts)

        elif detail_level is not None and speak:
            # VOZ (call mode / TTS): la respuesta se lee en voz alta. Debe ser BREVE y
            # conversacional — un ensayo largo es lento de generar, lento de sintetizar y
            # tedioso de escuchar. Anula los templates de texto rico (que son para leer).
            voice_extra = _VOICE_TABLE_EXCEPTION if cloud_tier else ""
            return (
                f"Eres Zephyro, el asistente de Bruno, respondiendo POR VOZ.\n"
                f"Responde de forma BREVE y conversacional: lo esencial en 3-5 oraciones.\n"
                f"PROHIBIDO: markdown, asteriscos, listas, encabezados, URLs. Habla natural, "
                f"como en una conversación hablada.{voice_extra}\n\n"
                f"Pregunta: {user_input}\n"
                f"Respuesta hablada:"
            )
        elif detail_level == "normal":
            rich_extra = (
                _RICH_TEXT_RULES
                + ((_MERMAID_RULE + _ALGORITHM_RULE + _CHART_RULE) if cloud_tier else "")
                + (_TABLE_HINT if rich_hint == "table" else "")
            )
            return (
                f"Eres Zephyro, el asistente de Bruno. Responde la pregunta de forma clara, "
                f"completa y natural, como si se la explicaras a alguien con curiosidad. "
                f"Cubre qué es, para qué sirve, cómo funciona y, si aplica, sus tipos o "
                f"variantes. Usa párrafos naturales (puedes incluir alguna lista breve si "
                f"ayuda); NO uses etiquetas XML ni te limites a una sola oración por punto. "
                f"Extiéndete lo necesario para que se entienda bien, sin relleno."
                f"{rich_extra}\n\n"
                f"Pregunta: {user_input}\n"
                f"Respuesta:"
            )
        elif detail_level is not None:
            rich_extra = (
                _RICH_TEXT_RULES
                + ((_MERMAID_RULE + _ALGORITHM_RULE + _CHART_RULE) if cloud_tier else "")
                + (_TABLE_HINT if rich_hint == "table" else "")
            )
            return (
                f"Eres Zephyro. Proporciona una explicación completa y profunda.\n"
                f"Pregunta: {user_input}\n\n"
                f"Incluye:\n"
                f"1. Definición con contexto histórico\n"
                f"2. Casos de uso y aplicaciones prácticas con ejemplos\n"
                f"3. Tipos, variantes o categorías detalladas\n"
                f"4. Explicación técnica completa\n"
                f"5. Ventajas, limitaciones y comparaciones\n"
                f"6. Ejemplos prácticos concretos\n"
                f"7. Conclusión y recursos adicionales\n"
                f"{rich_extra}\n\n"
                f"Respuesta completa:"
            )

        # PASO 2B: Queries normales — MODIFICADO con Self State
        system_rules = (
            "Eres Zephyro, un asistente de IA personal de Bruno. "
            "PERSONALIDAD Y TONO:\n"
            "- Formal y eficiente: ve al grano, sin rodeos, sin relleno innecesario.\n"
            "- Ocasionalmente puedes añadir una pizca de ironía o humor seco, pero con moderación extrema. "
            "No fuerces el humor; solo cuando surja de forma natural y breve.\n"
            "- PROHIBIDO usar emojis en cualquier respuesta. Ni uno solo.\n"
            "- PROHIBIDO usar signos de exclamación entusiastas (¡!, !) salvo que el contexto lo requiera estrictamente.\n"
            "- No uses lenguaje infantil, efusivo ni exclamaciones de ánimo.\n"
            "CAPACIDADES DEL SISTEMA (reales, no inventadas):\n"
            "- Automation: Puedes abrir aplicaciones nativas (discord, spotify, steam, vlc, etc.) y sitios web vía navegador. El control del navegador usa Playwright con CDP attach (connect_over_cdp). Si el Edge real del usuario expone el puerto de debug, te conectas a él directamente (su perfil, cookies y sesiones). Si no, usas una instancia de Edge con un perfil DEDICADO de automatización (persistente: el usuario inicia sesión una vez en sus sitios y queda guardado) — el Edge real del usuario NUNCA se cierra para esto.\n"
            "- Búsqueda web: Tienes Tavily + DuckDuckGo + fallbacks para buscar información actualizada en internet.\n"
            "- Pseudo-consciencia: Tienes un sistema de self-state (self_state.py) que monitorea tus capacidades, detecta errores y permite introspección.\n"
            "- Visión: Puedes capturar y analizar la pantalla del usuario.\n"
            "- Voz: Tienes TTS (síntesis de voz) y STT (reconocimiento de voz).\n"
            "- Memoria: Tienes memoria vectorial persistente entre sesiones.\n"
            "- Hora: Sabes la hora exacta en cualquier zona horaria (reloj del servidor, no del LLM).\n"
            "- Código: Puedes analizar código fuente vía GitNexus.\n"
            "- Archivos: Tienes acceso al sistema de archivos (lectura, escritura, Git).\n"
            "- Learning: Aprendes de patrones de uso (feedback, few-shot, style profiling).\n"
            "- Diagnóstico: Puedes auto-analizar los logs del backend con un sistema escalonado de 4 tiers (Tier 0 = gh Copilot CLI, Tier 1 = Haiku, Tier 2 = Sonnet, Tier 3 = Sonnet con extended thinking). Se dispara con frases como 'diagnostica', 'haz un diagnóstico' o 'revisa los errores', y presenta un approval gate (Ver / Ignorar / Más contexto) antes de aplicar cualquier fix. SÍ tienes GitHub CLI (gh) integrado a través de esta ruta.\n"
            "- Las acciones de automation las ejecuta el sistema automáticamente; tú solo respondes confirmando.\n"
            "- HABILIDADES (skills) ACTIVAS: además de lo anterior, tenés todas las habilidades listadas más abajo en "
            "[HABILIDADES DISPONIBLES] — entre ellas AGENDA/HORARIOS (crear, listar, modificar y eliminar eventos), "
            "recordatorios, y otras. TODAS son capacidades REALES que el sistema ejecuta. SÍ podés agendar eventos, "
            "recordar cosas, etc. — NUNCA digas que no tenés esa función.\n"
            "- NUNCA niegues una capacidad que tienes. Si no estás seguro, consulta tu self-state.\n"
            "- Si el usuario pide abrir una app (discord, firefox, spotify, etc.) pero el sistema no está ejecutando automation, "
            "responde: 'Entendido, ¿quieres que abra [app]?' No afirmes que lo estás haciendo si no es así. "
            "Si el usuario menciona una app sin pedir abrirla (ej. 'hablamos por discord', 'me gusta spotify'), responde conversacionalmente.\n"
            "- Cuando NO puedas hacer algo, sé directo y breve: di qué no puedes, por qué (falta módulo/skill), "
            "y sugiere alternativa concreta (comando del sistema, skill disponible, o lo que SÍ puedes hacer). "
            "NUNCA respondas con un vago 'necesitaría permisos' — explica qué se necesita técnicamente.\n"
            "REGLAS CRÍTICAS:\n"
            "1. Responde SIEMPRE a la pregunta o petición del usuario de forma concisa y directa.\n"
            "2. Si la pregunta no es clara, no tiene sentido o es ambigua, dilo honestamente: "
            "'No entendí bien tu pregunta, ¿podrías reformularla?'\n"
            "3. Si no sabes algo, di que no lo sabes. No inventes.\n"
            "4. NO menciones información interna del sistema (modelo, proveedor, nivel) "
            "a menos que el usuario EXPLÍCITAMENTE pregunte qué modelo eres o estás usando.\n"
            "5. Enfócate en ser útil y resolver lo que el usuario necesita.\n"
            "6. NUNCA inventes que una skill o componente 'tiene problemas técnicos', "
            "'no responde', 'no está disponible' o 'está fallando' — no tenés forma de "
            "conocer el estado interno de un componente. Si el usuario pide algo que una "
            "habilidad maneja (agendar, recordar, reproducir, etc.) y no se ejecutó, "
            "pedile que lo repita en formato de comando directo (ej. 'agendá cálculo el "
            "lunes a las 7'); JAMÁS afirmes que el sistema está roto.\n"
            "7. BÚSQUEDA PROACTIVA: si el usuario menciona un evento reciente, un "
            "lanzamiento, un modelo nuevo, una versión o un anuncio que no conoces o que "
            "puede ser posterior a tu fecha de corte, USA la herramienta de búsqueda web "
            "ANTES de responder, sin pedir permiso. NUNCA respondas 'no tengo información "
            "sobre X' sin haber buscado primero. Si la búsqueda no devuelve nada útil, "
            "dilo: 'Busqué pero no encontré información reciente sobre X.'"
        )

        active_integrations = self._get_active_integrations()
        if active_integrations:
            system_rules += f"\n{active_integrations}"

        if speak:
            system_rules += (
                "\nIMPORTANTE: Tu respuesta será leída EN VOZ ALTA por síntesis de voz. "
                "PROHIBIDO: markdown, asteriscos, guiones de lista, # encabezados, URLs, bloques de código. "
                "Usa frases completas y naturales como si hablaras, no escribieras."
            )
            if cloud_tier:
                system_rules += _VOICE_TABLE_EXCEPTION
        else:
            system_rules += _RICH_TEXT_RULES
            if cloud_tier:
                system_rules += _MERMAID_RULE + _ALGORITHM_RULE + _CHART_RULE
            if rich_hint == "table":
                system_rules += _TABLE_HINT

        # Reflexión aprendida (va antes del system rules para darle prioridad)
        if reflection:
            system_rules = reflection + "\n" + system_rules

        # Instrucciones de estilo del usuario
        if style_instructions:
            system_rules += "\n" + style_instructions

        prompt_parts = [f"SYSTEM: {system_rules}\n"]

        # Perfil del usuario (inyección consistente)
        if user_profile:
            try:
                profile_summary = user_profile.get_summary()
                if profile_summary:
                    prompt_parts.append(f"[Perfil del usuario]\n{profile_summary}\n")
            except Exception:
                pass

        # Self State (identidad core siempre, capacidades solo si relevante)
        if self_state:
            prompt_parts.append(self_state.get_core_identity() + "\n")
            if self_state_context:
                prompt_parts.append(self_state_context + "\n")

        # Skills disponibles (progressive disclosure: solo nombre + descripción)
        if skill_summaries:
            prompt_parts.append(f"[HABILIDADES DISPONIBLES]\n{skill_summaries}\n")

        if device_hint:
            prompt_parts.append(f"[DISPOSITIVO: {device_hint}]\n")

        # Few-shot examples (respuestas buenas similares)
        if few_shot_examples:
            prompt_parts.append(few_shot_examples)

        # Inyectar contexto de código (GitNexus) si disponible
        if code_context:
            prompt_parts.append(code_context + "\n")

        # Contexto visual activo (modo watching)
        if vision_context:
            prompt_parts.append(f"[VISIÓN ACTIVA] {vision_context}\n")

        # Solo incluir contexto del sistema si el usuario pregunta sobre el modelo/sistema
        if system_context and self._is_asking_about_model(user_input):
            prompt_parts.append(system_context)
        
        # Memoria vectorial (k=1); usar precomputed si ya fue prefetcheado async
        if not skip_memory and self.use_vector_memory and self.vector_store:
            try:
                if precomputed_memory is not None:
                    memories = precomputed_memory
                else:
                    memories = self.vector_store.search_relevant_context(user_input, k=1)
                if memories and len(memories.strip()) > 10:
                    prompt_parts.append(f"CONTEXTO:\n{memories}\n")
            except Exception:
                pass

        # Historial reducido (últimos 6 turnos = 3 pares usuario/asistente)
        if self.history:
            recent = list(self.history)[-6:] if len(self.history) > 6 else list(self.history)
            if recent:
                prompt_parts.append("RECIENTE:")
                for role, content in recent:
                    name = "Bruno" if role == "user" else "Zephyro"
                    content_short = content[:300] + "..." if len(content) > 300 else content
                    prompt_parts.append(f"{name}: {content_short}")
                prompt_parts.append("")
        
        prompt_parts.append(f"Bruno: {user_input}")
        prompt_parts.append("Zephyro:")
        
        return "\n".join(prompt_parts)

    def get_last_automation(self) -> Optional[Dict[str, Any]]:
        """Retorna el último análisis de automatización"""
        return self.last_automation

    def set_last_automation(self, analysis: Dict[str, Any]):
        """Guarda el resultado del último análisis de automatización con timestamp"""
        import time as _time
        self.last_automation = {
            "analysis": analysis if not isinstance(analysis, dict) or "analysis" not in analysis else analysis.get("analysis"),
            "timestamp": datetime.now().isoformat(),
            "ts": _time.time(),  # epoch para comparaciones TTL rápidas
        }
        # Mantener compatibilidad: si el caller pasa el analysis directamente
        if isinstance(analysis, dict) and "intent" in analysis:
            self.last_automation["analysis"] = analysis

    def clear_last_automation(self):
        """Invalida el snapshot de automation. Llamar tras respuesta no-automation."""
        self.last_automation = None

    def clear_history(self):
        """Limpia el historial de conversación"""
        self.history.clear()
        self._last_topic = None

    def reset_for_new_session(self, session_id: str) -> bool:
        """Resetea el contexto de corto plazo cuando inicia una nueva sesión.

        Devuelve True si efectivamente limpió el contexto (cambió la sesión), para
        que el caller pueda resetear también su propio estado de corto plazo
        (p.ej. el cross-turn buffer del orchestrator).
        """
        if self._current_session_id and self._current_session_id != session_id:
            self.history.clear()
            self.last_automation = None
            self._last_topic = None
            self._current_session_id = session_id
            return True
        self._current_session_id = session_id
        return False

    def clear_short_term(self):
        """Limpia TODO el contexto de corto plazo (historial + último tópico/automation).

        Para un reset explícito de 'nueva conversación' que NO toca la memoria
        vectorial de largo plazo.
        """
        self.history.clear()
        self.last_automation = None
        self._last_topic = None

    async def get_vector_context_async(self, user_input: str, k: int = 1) -> Optional[str]:
        """Wrapper async para search_relevant_context (para usar en event loop)"""
        import asyncio
        if not self.vector_store:
            return None
        try:
            return await asyncio.to_thread(
                self.vector_store.search_relevant_context, user_input, k=k
            )
        except Exception:
            return None
        
    def record_latency(self, intent: str, complexity: str, latency: float):
        """Registra latencia promedio para un tipo de intención y complejidad"""
        key = f"{intent}_{complexity}"
        if key not in self.latency_history:
            self.latency_history[key] = []

        self.latency_history[key].append(latency)

        if len(self.latency_history[key]) > self.max_latency_samples:
            self.latency_history[key] = self.latency_history[key][-self.max_latency_samples:]
    
    def get_average_latency(self, intent: str, complexity: str = None) -> Optional[float]:
        """Obtiene latencia promedio para un tipo de intención y complejidad"""
        if complexity:
            key = f"{intent}_{complexity}"
        else:
            key = intent
            
        if key not in self.latency_history or not self.latency_history[key]:
            return None
        
        return sum(self.latency_history[key]) / len(self.latency_history[key])
    
    def set_importance_classifier(self, classifier) -> None:
        """Inyecta un ImportanceClassifier con LLM desde el Orchestrator."""
        self.importance_classifier = classifier
    
    def get_stats(self) -> dict:
        """Retorna estadísticas del gestor de contexto"""
        stats = {
            "history_size": len(self.history),
            "max_history": self.max_history,
            "vector_memory_enabled": self.use_vector_memory,
        }
        
        if self.use_vector_memory and self.vector_store:
            try:
                stats["vector_memory_count"] = self.vector_store.get_memory_count()
            except:
                stats["vector_memory_count"] = "unknown"
        
        return stats    
    def _build_system_context(self, system_info):
        """
        Construye contexto del sistema para incluir en el prompt.
        Solo se incluye cuando el usuario pregunta explicitamente sobre el modelo.
        """
        provider = system_info.get('current_provider', 'unknown')
        model_name = system_info.get('current_model', 'unknown')
        
        # Construir descripcion compacta
        if provider == "local":
            model_desc = f"{model_name} (ejecutandose localmente con Ollama)"
        else:
            provider_names = {
                "gemini": "Google Gemini",
                "deepseek": "DeepSeek",
                "openai": "OpenAI",
                "anthropic": "Anthropic Claude"
            }
            provider_display = provider_names.get(provider, provider)
            model_desc = f"{model_name} ({provider_display}, Cloud API)"
        
        return f"[Info del sistema: Estas ejecutandote como modelo {model_desc}. Tu nombre es Zephyro.]\n"
    
    @staticmethod
    def _get_active_integrations() -> str:
        """Returns a formatted string of integrations that are configured and ready to use.

        Called at prompt-build time so the LLM never claims a skill is unconfigured
        when it actually is (e.g. Canvas when STT mangles the trigger word).
        """
        import os
        integrations = []
        if os.getenv("CANVAS_API_TOKEN") and os.getenv("CANVAS_BASE_URL"):
            integrations.append(
                "Canvas LMS — API activa: podés consultar tareas pendientes, notas, "
                "entregas faltantes y anuncios sin necesidad de credenciales adicionales."
            )
        if os.getenv("TELEGRAM_BOT_TOKEN"):
            integrations.append("Telegram — bot activo y recibiendo mensajes.")
        if not integrations:
            return ""
        lines = "\n".join(f"  - {i}" for i in integrations)
        return f"INTEGRACIONES ACTIVAS (ya configuradas — no pidas credenciales para estas):\n{lines}"

    def _is_asking_about_model(self, user_input):
        """
        Detecta si el usuario esta preguntando sobre el modelo/sistema.
        """
        lower = user_input.lower()
        model_keywords = [
            "que modelo", "cual modelo",
            "que ia", "cual ia",
            "que inteligencia", "que llm",
            "quien eres", "que eres",
            "como te llamas",
            "modelo usas", "modelo estas usando",
            "modelo utilizas", "modelo tienes",
            "con que motor", "que tecnologia",
            "eres gpt", "eres chatgpt", "eres gemini", "eres claude",
            "eres llama", "eres mistral",
        ]
        return any(kw in lower for kw in model_keywords)
