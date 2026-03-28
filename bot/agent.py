"""
Clasificador de intent para el agente conversacional de KALO.

Intents soportados:
  REGISTRAR_CALORIA  — "me comí una arepa con queso"
  REGISTRAR_EJERCICIO — "hice 30 min de bicicleta y quemé 200 kcal"
  VER_RESUMEN        — "cuántas calorías me quedan", "cómo voy hoy"
  VER_HISTORIAL      — "qué comí ayer", "resumen de la semana"
  BORRAR_REGISTRO    — "borra el último", "elimina el registro 2"
  VER_PERFIL         — "cuál es mi BMR", "mi perfil"
  ACTUALIZAR_PERFIL  — "cambié mi peso a 70kg"
  AYUDA              — "qué puedes hacer", "help"
  DESCONOCIDO        — cualquier otra cosa
"""

import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    REGISTRAR_CALORIA   = "REGISTRAR_CALORIA"
    REGISTRAR_EJERCICIO = "REGISTRAR_EJERCICIO"
    VER_RESUMEN         = "VER_RESUMEN"
    VER_HISTORIAL       = "VER_HISTORIAL"
    BORRAR_REGISTRO     = "BORRAR_REGISTRO"
    VER_PERFIL          = "VER_PERFIL"
    ACTUALIZAR_PERFIL   = "ACTUALIZAR_PERFIL"
    AYUDA               = "AYUDA"
    DESCONOCIDO         = "DESCONOCIDO"


@dataclass
class IntentResult:
    intent: Intent
    confianza: float          # 0.0 – 1.0
    parametros: dict          # datos extraídos del texto


# ── Patrones por intent ──────────────────────────────────────

_PATRONES: list[tuple[Intent, list[str]]] = [
    (Intent.REGISTRAR_EJERCICIO, [
        r"\b(ejercit|corr|camin|biciclet|nadar|nataci|gym|gimnasio|entrena|yoga|cardio|pesas|quem)\w*",
        r"\b(minutos?|min|horas?)\b.*(ejerc|activ|entrena)",
        r"\b(kcal|calor[íi]as?)\b.*\b(quem|gast)\w+",
        r"\b(quem[éeé]|gast[éeé])\b.*\b(kcal|calor[íi]as?)\b",
    ]),
    (Intent.REGISTRAR_CALORIA, [
        r"\b(com[íi]|desayun[éeé]|almorcé|cen[éeé]|tom[éeé]|beb[íi]|trag[uú])\w*",
        r"\b(comida|plato|almuerzo|desayuno|cena|snack|merienda|onces|picada)\b",
        r"\b(arepa|arroz|pollo|carne|sopa|ensalada|fruta|jugo|café|pizza|hamburguesa|pasta)\b",
        r"\b(\d+)\s*(kcal|calor[íi]as?)\b",
    ]),
    (Intent.VER_RESUMEN, [
        r"\b(c[oó]mo voy|cu[aá]nto (llevo|he comido|me queda))\b",
        r"\b(resumen|balance|estado)\b.*(hoy|d[íi]a|jornada)",
        r"\b(kcal|calor[íi]as?).*(quedan?|disponibles?|restantes?)\b",
        r"\bhoy\b.*(kcal|calor[íi]as?|comido|consumido)",
    ]),
    (Intent.VER_HISTORIAL, [
        r"\b(ayer|semana|semanas?|mes|meses?|historial|registro|registros)\b",
        r"\bqu[eé] com[íi]\b",
        r"\b(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b",
    ]),
    (Intent.BORRAR_REGISTRO, [
        r"\b(borra?|elimina?|quita?|bórr[ao]|elimin[ao])\b",
        r"\b(último|[úu]ltimo|anterior|registro \d+|el \d+)\b.*\b(borra?|quita?)",
    ]),
    (Intent.ACTUALIZAR_PERFIL, [
        r"\b(cambi[éeé]|actualiz[éeé]|mi nuevo peso|ahora peso|mido)\b",
        r"\bpeso\b.*\b\d+\s*kg\b",
        r"\b\d+\s*(kg|kilos?|centímetros?|cm|años?)\b.*(peso|mido|tengo)",
    ]),
    (Intent.VER_PERFIL, [
        r"\b(bmr|metabolismo|perfil|objetivo calórico|meta)\b",
        r"\bcu[aá]l es mi (peso|estatura|objetivo|meta)\b",
    ]),
    (Intent.AYUDA, [
        r"\b(ayuda|help|qué puedes|comandos|opciones|cómo funciona)\b",
        r"^/?(start|ayuda|help)$",
    ]),
]


def clasificar(texto: str) -> IntentResult:
    """
    Clasifica el texto libre en un Intent.
    Usa patrones regex ponderados — el intent con más matches gana.
    Devuelve DESCONOCIDO si ninguno supera el umbral.
    """
    texto_lower = texto.lower().strip()
    puntos: dict[Intent, int] = {}

    for intent, patrones in _PATRONES:
        for patron in patrones:
            if re.search(patron, texto_lower):
                puntos[intent] = puntos.get(intent, 0) + 1

    if not puntos:
        return IntentResult(intent=Intent.DESCONOCIDO, confianza=0.0, parametros={})

    mejor_intent = max(puntos, key=puntos.__getitem__)
    total_patrones = sum(len(p) for _, p in _PATRONES if _ == mejor_intent)
    confianza = min(puntos[mejor_intent] / max(total_patrones, 1), 1.0)

    # Extraer parámetros básicos del texto
    parametros = _extraer_parametros(texto_lower, mejor_intent)

    return IntentResult(intent=mejor_intent, confianza=confianza, parametros=parametros)


def _extraer_parametros(texto: str, intent: Intent) -> dict:
    """Extrae datos numéricos y entidades clave del texto."""
    params = {}

    # Extraer cantidad de kcal mencionadas
    match_kcal = re.search(r"(\d+(?:[.,]\d+)?)\s*(kcal|calor[íi]as?)", texto)
    if match_kcal:
        params["kcal"] = float(match_kcal.group(1).replace(",", "."))

    # Extraer duración en minutos
    match_dur = re.search(r"(\d+)\s*(minutos?|min)", texto)
    if match_dur:
        params["duracion_min"] = int(match_dur.group(1))

    # Extraer peso en kg
    match_peso = re.search(r"(\d+(?:[.,]\d+)?)\s*kg", texto)
    if match_peso:
        params["peso_kg"] = float(match_peso.group(1).replace(",", "."))

    # Extraer número de registro a borrar
    if intent == Intent.BORRAR_REGISTRO:
        match_num = re.search(r"\b(\d+)\b", texto)
        if match_num:
            params["numero"] = int(match_num.group(1))

    return params
