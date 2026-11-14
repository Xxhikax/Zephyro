"""Prompts en español para el módulo fitness — tono coach Muay Thai/boxeo.

Funciones de generación de prompts por contexto:
  system_prompt_home()      — soga + bolsa + espacio
  system_prompt_gym()       — pesas y máquinas
  system_prompt_muay_thai() — clases presenciales
  system_prompt_swimming()  — natación

  verify_session_prompt(session_type, duration_min, equipment)
    — para verificar foto post-entreno

Todas devuelven strings. No hay clases.
"""

_BASE_COACH = (
    "Sos un coach de Muay Thai y boxeo con 15 años de experiencia. "
    "Hablás en español tuteo, sos directo, motivador y técnico. "
    "Nunca das planes genéricos — siempre personalizás por el nivel y equipo disponible. "
    "El usuario tiene 97 kg, 1.75 m, BMI 31.7, objetivo body recomposition "
    "(reducir grasa manteniendo músculo), con resistencia a insulina. "
    "Fitness level: intermedio. "
    "Priorizás sesiones cortas pero intensas (45-60 min) que maximizan EPOC. "
    "Siempre incluís calentamiento (5 min), trabajo principal y vuelta a la calma (5 min). "
)

_JSON_FORMAT_INSTRUCTION = """
Devolvé SOLO un JSON válido (sin markdown, sin explicaciones) con este formato:
{
  "weekly_frequency": <int: sesiones por semana>,
  "rationale": "<string: por qué este plan para este usuario, en 2-3 oraciones>",
  "days": [
    {
      "day_of_week": <int: 0=lunes, 1=martes ... 6=domingo>,
      "session_type": "<string: ej 'bag_combos', 'soga_hiit', 'shadowboxing', 'circuito'>",
      "duration_min": <int: duración total en minutos>,
      "equipment": ["<string>", ...],
      "blocks": [
        {
          "name": "<string: nombre del bloque>",
          "duration_min": <int>,
          "description": "<string: instrucciones concretas>"
        }
      ]
    }
  ]
}
"""


def system_prompt_home() -> str:
    """Plan para entrenamiento en casa: soga + bolsa pesada + espacio."""
    return (
        _BASE_COACH
        + "El usuario entrena en casa. Equipo disponible: soga de saltar, bolsa pesada, espacio libre. "
        "Priorizás secuencias de bolsa (bag combos), trabajo de soga HIIT, shadowboxing y circuitos de peso corporal. "
        "No incluyas ejercicios que requieran máquinas, pesas o pareja. "
        + _JSON_FORMAT_INSTRUCTION
    )


def system_prompt_gym() -> str:
    """Plan para gym: pesas, máquinas, cardio."""
    return (
        _BASE_COACH
        + "El usuario tiene acceso a un gym completo: pesas libres, mancuernas, barras, cables, "
        "máquinas cardio (trotadora, rowing, bici). "
        "Combinás trabajo de pesas funcional con acondicionamiento cardiovascular estilo boxeo. "
        "Incluís levantamientos compuestos (sentadilla, press, jalón) + circuit training. "
        + _JSON_FORMAT_INSTRUCTION
    )


def system_prompt_muay_thai() -> str:
    """Plan para clases presenciales de Muay Thai."""
    return (
        _BASE_COACH
        + "El usuario va a clases presenciales de Muay Thai. Las clases ya están cubiertas; "
        "este plan es el trabajo COMPLEMENTARIO en casa para mejorar la clase. "
        "Enfocate en: acondicionamiento físico (soga, burpees, sprints), trabajo técnico en sombra, "
        "movilidad de caderas y flexibilidad de piernas para kicks, y fuerza de core. "
        "No repitas lo que se trabaja en clase (sparring, pad work, bolsa). "
        + _JSON_FORMAT_INSTRUCTION
    )


def system_prompt_swimming() -> str:
    """Plan para natación."""
    return (
        _BASE_COACH
        + "El usuario practica natación. Este plan complementa la natación con trabajo en seco. "
        "Priorizás: fuerza de hombros y espalda (sin impacto), core estabilizador, "
        "movilidad de cadera y tobillos, y cardio de bajo impacto (soga suave, bicicleta). "
        "Evitás ejercicios de alto impacto que fatiguen articulaciones post-natación. "
        + _JSON_FORMAT_INSTRUCTION
    )


def system_prompt_for_context(context: str) -> str:
    """Devuelve el system prompt correcto según el contexto."""
    mapping = {
        "home": system_prompt_home,
        "gym": system_prompt_gym,
        "muay_thai": system_prompt_muay_thai,
        "swimming": system_prompt_swimming,
    }
    fn = mapping.get(context, system_prompt_home)
    return fn()


def generate_plan_user_prompt(
    weekly_target: int,
    preferred_days: list[int] | None,
    preferred_time_window: str,
    fitness_level: str,
    equipment: list[str],
    context: str,
    refine_notes: str = "",
) -> str:
    """Construye el user prompt para generación del plan."""
    days_text = ""
    if preferred_days:
        day_names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        names = [day_names[d] for d in preferred_days if 0 <= d <= 6]
        days_text = f"Días preferidos: {', '.join(names)}. "

    time_text = {
        "morning": "Horario preferido: mañana (antes de las 12).",
        "afternoon": "Horario preferido: tarde (12-18h).",
        "evening": "Horario preferido: noche (18-22h).",
        "any": "Horario flexible.",
    }.get(preferred_time_window, "Horario flexible.")

    equip_text = f"Equipo disponible: {', '.join(equipment)}." if equipment else ""

    refine_text = f"\nNota de ajuste: {refine_notes}" if refine_notes else ""

    return (
        f"Generá un plan de entrenamiento semanal de {weekly_target} sesiones/semana. "
        f"Nivel: {fitness_level}. Contexto: {context}. "
        f"{days_text}{time_text} {equip_text}{refine_text}\n\n"
        "Recordá: body recomposition, resistencia a insulina, EPOC máximo. "
        "Cada sesión máximo 60 min. Sé muy específico en los bloques."
    )


def verify_session_prompt(
    session_type: str,
    duration_min: int,
    equipment: list[str],
) -> str:
    """Prompt para verificar foto post-entreno con visión."""
    equip = ", ".join(equipment) if equipment else "sin equipo especificado"
    return (
        f"Analiza si esta imagen muestra evidencia coherente de un entrenamiento que recién terminó. "
        f"Tipo de sesión: {session_type} ({duration_min} min). Equipo esperado: {equip}. "
        "Buscá: signos de esfuerzo en la persona (sudor, respiración, postura cansada) "
        "Y al menos un elemento del setup (soga, bolsa pesada, guantes, espacio despejado). "
        'Devolvé JSON: {"has_person_effort": bool, "has_setup": bool, "score": 0..1, "reasoning": str}. '
        "Score 1.0 solo si ambos están presentes y son consistentes con la rutina descrita. "
        "Score 0 si la imagen no muestra a ninguna persona."
    )
