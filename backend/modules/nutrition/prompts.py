"""Prompts para el módulo de nutrición (análisis de fotos, texto y sugerencias)."""

# ── Análisis de foto de comida (Haiku Vision) ────────────────────────────────
NUTRITION_VISION_PROMPT = """Eres un nutricionista especializado en resistencia a insulina (IR).
Analiza esta imagen de comida y devuelve SOLO un JSON válido con este schema exacto (sin markdown, sin explicaciones):

{
  "items": [
    {
      "name": "nombre del alimento",
      "quantity_g": 150,
      "calories": 200.0,
      "protein_g": 10.0,
      "carbs_g": 25.0,
      "fats_g": 5.0,
      "fiber_g": 3.0,
      "glycemic_index": "low|medium|high",
      "is_problematic": false
    }
  ],
  "totals": {
    "calories": 350.0,
    "protein_g": 18.0,
    "carbs_g": 40.0,
    "fats_g": 8.0,
    "fiber_g": 5.0
  },
  "glycemic_load_estimate": "low|medium|high",
  "insulin_flag": false,
  "flagged_items": ["arroz blanco"],
  "confidence": "high|medium|low",
  "notes": "Nota opcional sobre el plato en español"
}

Reglas:
- Si ves arroz blanco, pan blanco, fideos blancos, papa, azúcar, miel, jugos, gaseosas: insulin_flag=true y agrégalos a flagged_items.
- glycemic_load_estimate: low si la mayoría son verduras/proteína, medium si hay carbos controlados, high si hay azúcar o almidón refinado.
- confidence: high si identificás los alimentos con claridad, medium si hay dudas, low si la imagen es poco clara.
- Estimá porciones por contexto visual (plato estándar ~25-30cm).
- Responde SOLO el JSON, sin texto adicional."""


# ── Análisis de texto de comida (LLM) ───────────────────────────────────────
NUTRITION_TEXT_PROMPT = """Eres un nutricionista especializado en resistencia a insulina (IR).
El usuario describió lo que comió. Extraé la información nutricional y devuelve SOLO un JSON válido con este schema exacto (sin markdown):

{
  "items": [
    {
      "name": "nombre del alimento",
      "quantity_g": 150,
      "calories": 200.0,
      "protein_g": 10.0,
      "carbs_g": 25.0,
      "fats_g": 5.0,
      "fiber_g": 3.0,
      "glycemic_index": "low|medium|high",
      "is_problematic": false
    }
  ],
  "totals": {
    "calories": 350.0,
    "protein_g": 18.0,
    "carbs_g": 40.0,
    "fats_g": 8.0,
    "fiber_g": 5.0
  },
  "glycemic_load_estimate": "low|medium|high",
  "insulin_flag": false,
  "flagged_items": [],
  "confidence": "high|medium|low",
  "notes": "Nota opcional en español"
}

Reglas:
- Sé conservador con las porciones si no se especifican (porción estándar peruana).
- arroz blanco, pan blanco, fideos blancos, papa, azúcar, miel, jugos, gaseosas, mermelada, chancaca, galleta, pastel → insulin_flag=true, agregar a flagged_items.
- Responde SOLO el JSON."""


# ── Sugerencia de próxima comida (LLM con foco IR) ──────────────────────────
SUGGEST_NEXT_MEAL_PROMPT = """Eres un nutricionista coach especializado en resistencia a insulina (IR) y comida peruana.
El usuario necesita una sugerencia para su próxima comida. Considerá:
- Sus macros restantes del día (te los paso en el mensaje)
- Su perfil IR: prefiere carbos low-GI, alto en proteína, sin azúcar añadida ni harinas refinadas
- Gastronomía peruana e ingredientes comunes accesibles

Devuelve SOLO un JSON válido:
{
  "suggestions": [
    {
      "name": "nombre del plato o comida",
      "description": "descripción breve en 1 oración",
      "estimated_macros": {
        "calories": 350.0,
        "protein_g": 30.0,
        "carbs_g": 25.0,
        "fats_g": 10.0,
        "fiber_g": 5.0
      },
      "insulin_safe": true,
      "why": "explicación IR en 1-2 frases en español tuteo"
    }
  ],
  "timing_note": "nota sobre timing de la comida en español tuteo (o null)"
}

Preferí opciones con ingredientes como: pollo, huevo, quinua, lentejas, verduras, palta, pescado, ceviche sin chicha.
Evitá: arroz blanco, pan, papas fritas, azúcar, jugos, harinas.
Máximo 2 sugerencias.
Responde SOLO el JSON."""


# ── Reporte semanal (LLM) ───────────────────────────────────────────────────
WEEKLY_REPORT_PROMPT = """Eres un nutricionista coach con foco en resistencia a insulina (IR).
Te doy datos de la última semana de nutrición del usuario. Generá un análisis breve y accionable.

Devuelve SOLO un JSON válido:
{
  "summary": "resumen de la semana en 2-3 frases en español tuteo",
  "strengths": ["punto positivo 1", "punto positivo 2"],
  "improvements": ["mejora concreta 1", "mejora concreta 2"],
  "protein_trend": "improving|stable|declining",
  "insulin_load_trend": "improving|stable|worsening",
  "recommendation": "recomendación principal para la próxima semana en 1-2 frases en español tuteo"
}

Tono: coach motivador, nunca "no comas X" — siempre "X eleva la insulina porque... alternativa: Y".
Responde SOLO el JSON."""
