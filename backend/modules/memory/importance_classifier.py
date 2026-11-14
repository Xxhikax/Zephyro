"""
Clasificador de Importancia de Conversaciones
Determina si una conversación debe guardarse en memoria vectorial o solo en historial
"""

from typing import Dict, Tuple
import re

class ImportanceClassifier:
    def __init__(self, llm=None):
        """
        Inicializar clasificador
        
        Args:
            llm: Modelo LLM para clasificación (opcional, usa reglas si no se provee)
        """
        self.llm = llm
        
        # Palabras clave que indican importancia
        self.important_keywords = {
            'trabajo': 1.0,
            'proyecto': 1.0,
            'tarea': 0.9,
            'desarrollo': 0.9,
            'implementa': 0.9,
            'crea': 0.8,
            'diseña': 0.8,
            'planifica': 0.8,
            'explica': 0.7,
            'enseña': 0.7,
            'aprende': 0.7,
            'estudia': 0.7,
            'documenta': 0.8,
            'investiga': 0.8,
            'analiza': 0.7,
            'resuelve': 0.8,
            'problema': 0.7,
            'error': 0.6,
            'bug': 0.6,
            'función': 0.6,
            'clase': 0.6,
            'algoritmo': 0.8,
            'base de datos': 0.7,
            'api': 0.7,
            'código': 0.6,
            'programa': 0.7,
            'script': 0.6,
            'importante': 0.9,
            'recuerda': 0.9,
            'memoriza': 1.0,
            'guarda': 0.7,
            'nota': 0.6,
        }
        
        # Palabras que indican trivialidad
        self.trivial_keywords = {
            'hola': 0.1,
            'adiós': 0.1,
            'chao': 0.1,
            'gracias': 0.2,
            'buenas': 0.1,
            'qué tal': 0.1,
            'cómo estás': 0.1,
            'bien': 0.1,
            'mal': 0.1,
            'ok': 0.1,
            'vale': 0.1,
        }
    
    def classify(
        self,
        user_input: str,
        assistant_response: str,
        intent: str = None,
        complexity: str = None,
        response_length: int = None
    ) -> Tuple[str, float, str]:
        """
        Clasificar conversación
        
        Returns:
            Tuple[importance_level, importance_score, reasoning]
            - importance_level: 'trivial', 'moderate', 'important', 'critical'
            - importance_score: 0.0 a 1.0
            - reasoning: Explicación de la clasificación
        """
        
        # Usar LLM local sólo si (a) el clasificador local está habilitado (opt-in, GPU
        # holgada) y (b) Ollama responde. En hardware débil un modelo chico falla el formato
        # de este prompt (devuelve 'SCORE') y agrega latencia → mejor reglas.
        import os as _os
        from core.ollama_health import is_ollama_up
        _local_ok = _os.getenv("ZEPHYRO_LOCAL_CLASSIFIERS", "0").lower() in ("1", "true", "yes", "on")
        if self.llm and response_length and response_length > 200 and _local_ok and is_ollama_up():
            return self._classify_with_llm(user_input, assistant_response)

        # Clasificación basada en reglas
        return self._classify_with_rules(user_input, assistant_response, intent, complexity, response_length)
    
    def _classify_with_rules(
        self,
        user_input: str,
        assistant_response: str,
        intent: str = None,
        complexity: str = None,
        response_length: int = None
    ) -> Tuple[str, float, str]:
        """Clasificación basada en reglas heurísticas"""
        
        user_lower = user_input.lower()
        response_lower = assistant_response.lower()
        combined_text = user_lower + " " + response_lower
        
        # Calcular score base
        importance_score = 0.0
        reasoning_parts = []
        
        # 1. Palabras clave importantes
        important_matches = []
        for keyword, weight in self.important_keywords.items():
            if keyword in user_lower:
                importance_score += weight
                important_matches.append(keyword)
        
        if important_matches:
            reasoning_parts.append(f"Palabras clave importantes: {', '.join(important_matches[:3])}")
        
        # 2. Palabras triviales (restan)
        trivial_matches = []
        for keyword, weight in self.trivial_keywords.items():
            if keyword == user_lower or user_lower.startswith(keyword):
                importance_score -= (1.0 - weight)
                trivial_matches.append(keyword)
        
        if trivial_matches:
            reasoning_parts.append(f"Saludos/triviales detectados: {', '.join(trivial_matches[:2])}")
        
        # 3. Longitud de la respuesta (indicador de complejidad)
        if response_length:
            if response_length > 500:
                importance_score += 0.4
                reasoning_parts.append("Respuesta extensa (>500 chars)")
            elif response_length > 200:
                importance_score += 0.2
                reasoning_parts.append("Respuesta moderada (>200 chars)")
        
        # 4. Intent del router
        if intent:
            if intent in ['automation', 'vision', 'educational']:
                importance_score += 0.5
                reasoning_parts.append(f"Intent complejo: {intent}")
            elif intent == 'chat':
                importance_score += 0.1
        
        # 5. Complejidad
        if complexity:
            if complexity == 'complex':
                importance_score += 0.6
                reasoning_parts.append("Tarea compleja detectada")
            elif complexity == 'simple':
                importance_score -= 0.2
        
        # 6. Presencia de código
        if '```' in assistant_response or 'def ' in assistant_response or 'class ' in assistant_response:
            importance_score += 0.5
            reasoning_parts.append("Contiene código")
        
        # 7. Preguntas complejas (múltiples oraciones)
        if user_input.count('?') > 1 or user_input.count('.') > 2:
            importance_score += 0.3
            reasoning_parts.append("Pregunta compleja o múltiple")
        
        # 8. Comandos especiales que NO deben guardarse
        if user_input.startswith('!') and user_input not in ['!recordar', '!importante']:
            importance_score -= 0.5
            reasoning_parts.append("Comando del sistema")
        
        # 9. Respuestas de error no son importantes
        if 'error' in response_lower and len(assistant_response) < 100:
            importance_score -= 0.3
            reasoning_parts.append("Respuesta de error corta")
        
        # Normalizar score
        importance_score = max(0.0, min(1.0, importance_score))
        
        # Determinar nivel (ajustado para ser más conservador)
        if importance_score >= 0.9:
            level = 'critical'
        elif importance_score >= 0.65:
            level = 'important'
        elif importance_score >= 0.35:
            level = 'moderate'
        else:
            level = 'trivial'
        
        # Generar razonamiento
        if not reasoning_parts:
            reasoning = f"Conversación {level} (score: {importance_score:.2f})"
        else:
            reasoning = f"{level.capitalize()} - {'; '.join(reasoning_parts[:3])} (score: {importance_score:.2f})"
        
        return level, importance_score, reasoning
    
    def _classify_with_llm(
        self,
        user_input: str,
        assistant_response: str
    ) -> Tuple[str, float, str]:
        """Clasificación usando LLM (para casos complejos)"""
        
        prompt = f"""Analiza esta conversación y determina su importancia.

Usuario: {user_input[:200]}
Asistente: {assistant_response[:300]}

¿Esta conversación contiene información importante que debería recordarse a largo plazo?
Considera importante: desarrollo de proyectos, tareas complejas, aprendizaje, trabajo profesional, información técnica.
Considera trivial: saludos, chat casual, consultas simples de información general.

Responde en formato: NIVEL|SCORE|RAZÓN
Donde NIVEL es: trivial, moderate, important, critical
SCORE es un número de 0.0 a 1.0
RAZÓN es una breve explicación

Ejemplo: important|0.75|Discusión sobre implementación de feature"""
        
        try:
            # Usar generate() si es LLMManager, invoke() si es modelo raw de LangChain
            if hasattr(self.llm, 'generate'):
                response = self.llm.generate(prompt, force_local=True, preset="router")
            else:
                response = self.llm.invoke(prompt, temperature=0.1, max_tokens=50)
            
            # Parsear respuesta
            parts = response.split('|')
            if len(parts) >= 3:
                level = parts[0].strip().lower()
                score = float(parts[1].strip())
                reasoning = parts[2].strip()
                
                # Validar nivel
                if level not in ['trivial', 'moderate', 'important', 'critical']:
                    level = 'moderate'
                
                # Validar score
                score = max(0.0, min(1.0, score))
                
                return level, score, f"LLM: {reasoning}"
        except Exception as e:
            print(f"[Warning] Error en clasificación LLM: {e}")
        
        # Fallback a reglas
        return self._classify_with_rules(user_input, assistant_response, None, None, len(assistant_response))
    
    def should_save_to_memory(self, importance_level: str, importance_score: float) -> bool:
        """
        Determinar si debe guardarse en memoria vectorial
        
        Criterio: important o critical (score >= 0.6)
        """
        return importance_level in ['important', 'critical'] or importance_score >= 0.6
