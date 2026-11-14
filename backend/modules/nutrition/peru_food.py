"""Catálogo curado de platos peruanos con flags IR pre-computados.

Usado como hint cuando el análisis visual tiene baja confianza o como
contexto en las sugerencias de próxima comida.
"""
from __future__ import annotations

import re
from typing import Optional

# Formato: {flag: bool, flagged_items: [...], note: str}
# flag=True → eleva la insulina / tiene ingredientes problemáticos para IR
# flag=False → apto para perfil IR sin modificaciones o con nota

PERU_FOOD: dict[str, dict] = {
    # ── Proteínas sin problema ──────────────────────────────────────────
    "ceviche": {
        "flag": False,
        "flagged_items": [],
        "note": "Alto en proteína y bajo en carbos — ideal para IR. Limón, leche de tigre y ají no afectan la insulina.",
    },
    "tiradito": {
        "flag": False,
        "flagged_items": [],
        "note": "Similar al ceviche: proteína, ácido, sin carbos problemáticos.",
    },
    "pollo a la brasa": {
        "flag": False,
        "flagged_items": [],
        "note": "Sin papas fritas y con ensalada en lugar de arroz: proteína limpia, apto IR.",
    },
    "seco de pollo": {
        "flag": True,
        "flagged_items": ["arroz blanco", "menestras"],
        "note": "La proteína es excelente; pedilo sin arroz o pedí quinua. Las menestras en porción controlada están bien.",
    },
    "lomo saltado": {
        "flag": True,
        "flagged_items": ["arroz blanco", "papas fritas"],
        "note": "La carne y las verduras son buenos; el arroz blanco y las papas fritas elevan la insulina rápido. Pedilo sin arroz o con quinua, y sin papas.",
    },
    "aji de gallina": {
        "flag": True,
        "flagged_items": ["pan de molde", "arroz blanco"],
        "note": "La base con pan de molde tiene carbos refinados. Pedilo con porciones chicas y sin arroz; la proteína de pollo compensa.",
    },
    "papa a la huancaina": {
        "flag": True,
        "flagged_items": ["papa"],
        "note": "La papa amarilla es medium-GI; porción chica (1 rebanada) es manejable. La salsa huancaína en sí tiene ají, leche y queso — sin problema.",
    },
    "carapulcra": {
        "flag": True,
        "flagged_items": ["papa seca"],
        "note": "Papa seca es carbohidrato concentrado con GI alto. Porción pequeña con mucha proteína encima.",
    },
    "causa limeña": {
        "flag": True,
        "flagged_items": ["papa amarilla"],
        "note": "Papa amarilla es medium-GI; porción pequeña (1 rebanada) es manejable para IR. El relleno de atún o pollo compensa.",
    },
    "anticuchos": {
        "flag": False,
        "flagged_items": [],
        "note": "Corazón de res a la brasa: proteína y grasa sin carbos. Excelente para IR, evitá la papa con todo.",
    },
    "picarones": {
        "flag": True,
        "flagged_items": ["harina", "azúcar", "miel de caña"],
        "note": "Postre con harina y miel: pico glucémico alto. Evitar o consumir muy ocasionalmente.",
    },
    "mazamorra morada": {
        "flag": True,
        "flagged_items": ["azúcar", "chuño", "harina"],
        "note": "Alto en azúcar añadida y almidones. Evitar para perfil IR.",
    },
    "arroz con leche": {
        "flag": True,
        "flagged_items": ["arroz", "azúcar"],
        "note": "Doble carga glucémica: arroz + azúcar. Evitar.",
    },
    "chicha morada": {
        "flag": True,
        "flagged_items": ["azúcar"],
        "note": "Carga glucémica alta por el azúcar añadido. Preferí agua o infusión de maíz morado sin azúcar.",
    },
    "chicha de jora": {
        "flag": True,
        "flagged_items": ["azúcar", "maíz fermentado"],
        "note": "Fermentado con carbohidratos; contiene azúcar. Evitar en IR.",
    },
    "inca kola": {
        "flag": True,
        "flagged_items": ["azúcar"],
        "note": "Gaseosa con alto contenido de azúcar. Reemplazala con agua o chicha morada sin azúcar.",
    },
    "pollo a la brasa con papas": {
        "flag": True,
        "flagged_items": ["papas fritas"],
        "note": "Las papas fritas elevan rápido la glucosa. Pedí ensalada en lugar de papas.",
    },
    "arroz blanco": {
        "flag": True,
        "flagged_items": ["arroz blanco"],
        "note": "Carbohidrato refinado con GI alto — eleva la insulina rápidamente. Reemplazalo por quinua o arroz integral en porción controlada.",
    },
    "quinua": {
        "flag": False,
        "flagged_items": [],
        "note": "Cereal completo con proteína, fibra y GI bajo. Ideal para IR como reemplazo del arroz.",
    },
    "pescado a la plancha": {
        "flag": False,
        "flagged_items": [],
        "note": "Proteína magra sin carbos. Excelente para IR, acompañar con verduras.",
    },
    "saltado de verduras": {
        "flag": False,
        "flagged_items": [],
        "note": "Sin carbos refinados, mucha fibra y antioxidantes. Ideal para IR.",
    },
    "tacu tacu": {
        "flag": True,
        "flagged_items": ["arroz blanco", "menestras"],
        "note": "Mezcla de arroz y frijoles: índice glucémico moderado-alto. Porción pequeña como acompañamiento, no como plato principal.",
    },
    "suspiro limeño": {
        "flag": True,
        "flagged_items": ["azúcar", "manjar blanco"],
        "note": "Postre muy alto en azúcar. Evitar para perfil IR.",
    },
    "pan de yema": {
        "flag": True,
        "flagged_items": ["harina refinada", "azúcar"],
        "note": "Pan tradicional con harina refinada. Reemplazalo por pan integral o de centeno en mínima cantidad.",
    },
    "empanada": {
        "flag": True,
        "flagged_items": ["harina refinada"],
        "note": "Masa de harina blanca con GI alto. Preferí proteína sin envoltorio de harina.",
    },
}


def lookup_dish(name: str) -> Optional[dict]:
    """Fuzzy match del nombre de plato en el catálogo.

    Estrategia:
    1. Match exacto (normalizado).
    2. Match por substring (el nombre buscado está contenido en la clave o viceversa).
    3. Match por palabras clave principales (al menos 2 palabras en común).
    """
    if not name:
        return None

    norm = _normalize(name)

    # 1. Exacto
    if norm in PERU_FOOD:
        return PERU_FOOD[norm]

    # 2. Substring
    for key, val in PERU_FOOD.items():
        if norm in key or key in norm:
            return val

    # 3. Palabras clave (al menos 2 palabras en común)
    norm_words = set(norm.split())
    best_key = None
    best_count = 0
    for key in PERU_FOOD:
        key_words = set(key.split())
        common = norm_words & key_words
        if len(common) >= 2 and len(common) > best_count:
            best_count = len(common)
            best_key = key

    if best_key:
        return PERU_FOOD[best_key]

    return None


def _normalize(text: str) -> str:
    """Normaliza a minúsculas sin acentos ni caracteres especiales."""
    text = text.lower().strip()
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "ü": "u", "ñ": "n",
    }
    for a, b in replacements.items():
        text = text.replace(a, b)
    # Quitar puntuación sobrante
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
