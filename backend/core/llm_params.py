# core/llm_params.py
# Parámetros de gestión y optimización del LLM

class LLMParams:
    """Configuración de parámetros para control de recursos del CPU/GPU"""
    
    # Parámetros de temperatura y creatividad
    TEMPERATURE_PRECISE = 0.3
    TEMPERATURE_BALANCED = 0.7
    TEMPERATURE_CREATIVE = 0.9
    
    # Parámetros de contexto (tokens)
    CTX_SMALL = 2048
    CTX_MEDIUM = 4096
    CTX_LARGE = 8192
    
    # Parámetros de predicción (tokens de respuesta)
    PREDICT_SHORT = 256
    PREDICT_MEDIUM = 512
    PREDICT_LONG = 1024
    
    # Configuraciones predefinidas
    CONFIGS = {
        'router': {  # ← NUEVO: Preset específico para Router (clasificación rápida)
            'temperature': 0.2,  # Muy bajo para respuestas consistentes
            'num_ctx': 1024,     # Contexto mínimo
            'num_predict': 30,   # Solo 30 tokens (suficiente para clasificar)
            'top_k': 10,
            'top_p': 0.8,
            'stop': ['\n', 'Usuario:', 'User:']  # Parar en saltos de línea
        },
        'concepto_breve': {
            'temperature': TEMPERATURE_PRECISE,
            'num_ctx': CTX_SMALL,
            'num_predict': 80,  # Aumentado de 50 a 80 para completar oraciones
            'top_k': 20,
            'top_p': 0.9,
            'stop': ['\n\n', 'Historia', 'Contexto', 'Casos de uso', 'La historia']  # Parar antes de extenderse
        },
        'concepto_normal': {
            'temperature': TEMPERATURE_PRECISE,
            'num_ctx': CTX_MEDIUM,
            'num_predict': 250,  # Aumentado de 200 a 250
            'top_k': 40,
            'top_p': 0.95,
            'stop': ['¿Quieres saber', '¿Te gustaría', '¿Deseas']  # Parar antes de follow-up
        },
        'concepto_detallado': {
            'temperature': TEMPERATURE_BALANCED,
            'num_ctx': CTX_LARGE,
            'num_predict': 500,  # Aumentado de 400 a 500
            'top_k': 50,
            'top_p': 0.95,
            'stop': []  # Sin límites
        },
        'rapido': {
            'temperature': TEMPERATURE_PRECISE,
            'num_ctx': CTX_SMALL,
            'num_predict': 100,
            'top_k': 20,
            'top_p': 0.9,
            'stop': ['\n\n']
        },
        'balanceado': {
            'temperature': TEMPERATURE_BALANCED,
            'num_ctx': CTX_MEDIUM,
            'num_predict': 300,
            'top_k': 40,
            'top_p': 0.95,
            'stop': []
        },
        'calidad': {
            'temperature': TEMPERATURE_PRECISE,
            'num_ctx': CTX_LARGE,
            'num_predict': PREDICT_LONG,
            'top_k': 50,
            'top_p': 0.95,
            'stop': []
        },
        'creativo': {
            'temperature': TEMPERATURE_CREATIVE,
            'num_ctx': CTX_MEDIUM,
            'num_predict': PREDICT_MEDIUM,
            'top_k': 60,
            'top_p': 0.98,
            'stop': []
        }
    }
    
    @staticmethod
    def get_config(preset: str = 'balanceado') -> dict:
        return LLMParams.CONFIGS.get(preset, LLMParams.CONFIGS['balanceado'])
    
    @staticmethod
    def custom_config(temperature: float = 0.7, 
                     num_ctx: int = 2048,
                     num_predict: int = 256,
                     top_k: int = 40,
                     top_p: float = 0.95) -> dict:
        return {
            'temperature': temperature,
            'num_ctx': num_ctx,
            'num_predict': num_predict,
            'top_k': top_k,
            'top_p': top_p
        }


# Ejemplo de uso con ollama
def ejemplo_uso():
    """Ejemplo de cómo usar los parámetros con ollama"""
    import ollama
    
    # Opción 1: Usar configuración predefinida
    config = LLMParams.get_config('rapido')
    response = ollama.generate(
        model='llama3.2:3b',
        prompt='Hola, ¿cómo estás?',
        options=config
    )
    
    # Opción 2: Configuración personalizada
    config = LLMParams.custom_config(temperature=0.5, num_predict=128)
    response = ollama.generate(
        model='llama3.2:3b',
        prompt='Explica qué es Python',
        options=config
    )
