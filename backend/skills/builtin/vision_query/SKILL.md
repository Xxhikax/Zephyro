---
name: vision-query
description: "Responde preguntas sobre lo que el usuario tiene en pantalla capturando una screenshot y analizándola con Claude Haiku Vision."
triggers: []
requires:
  python: []
provides:
  slash_commands:
    - name: "mira mi pantalla"
      description: "Capturar la pantalla y describir lo que se ve"
    - name: "qué ves en mi pantalla"
      description: "Pregunta abierta sobre el contenido de la pantalla"
---

# Vision Query Skill

Cuando el usuario pregunta por su pantalla ("mira mi pantalla", "ayúdame con
esto que tengo en pantalla", "qué opinas de esto", etc.), esta skill captura
una screenshot y la envía a Claude Haiku Vision con la pregunta exacta del
usuario. Devuelve una respuesta concisa en español.

## Activación

Match por regex flexible que cubre tanto formas explícitas como implícitas:

- **Explícitas**: "mira mi pantalla", "ve mi pantalla", "qué ves", "describe
  la pantalla", "analiza mi pantalla", "captura mi pantalla".
- **Implícitas**: "mira esto", "ayúdame con esto que tengo", "qué opinas de
  esto que ves", "puedes ver mi pantalla", "lee lo que aparece".

La skill se ejecuta antes del routing al LLM principal, evitando que el
modelo conversacional responda "no tengo función de captura" cuando el
sistema sí puede capturar.

## Limitaciones

- Requiere que `orchestrator.vision_capture` y `orchestrator.vision_analyze`
  estén inicializados (configurados en el orchestrator).
- Si la captura falla (permisos, display server), devuelve un mensaje de
  error accionable en vez de propagar la excepción.
- Cache de 60s por input idéntico para evitar duplicar capturas si el
  usuario repite la pregunta rápidamente.
