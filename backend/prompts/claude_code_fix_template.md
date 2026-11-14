# Zephyro Diagnostic Fix Request

**Tier:** {tier_usado} | **Confianza:** {confianza}% | **Archivos:** {archivos_afectados}

## Causa raíz identificada

{causa}

## Solución propuesta

{solucion_propuesta}

---

## Error log (últimas ocurrencias)

```
{error_log_text}
```

## Code context

{code_context_blocks}

---

## Instrucciones para Claude Code

Analiza el error descrito arriba y genera el fix mínimo necesario.

### Restricciones:
- Solo modifica los archivos listados en **Archivos:** `{archivos_afectados}`
- No introduzcas nuevas dependencias externas sin justificación explícita
- El fix debe ser compatible con Python 3.11+ y asyncio
- Mantén el estilo existente: snake_case, type hints, docstrings en español
- No refactorices código no relacionado con el error

### Formato de respuesta requerido:
1. **Confirmación de causa raíz** — 1-2 oraciones
2. **Diff mínimo** — formato `--- a/file\n+++ b/file`
3. **Tests afectados** — si existen tests que cubren esta área, listarlos
4. **Riesgos del fix** — efectos secundarios o regresiones posibles
