import base64
import json
import logging
import re
from decimal import Decimal

import httpx

from app.core.config import settings
from app.schemas.schemas import FotoAnalisisOut

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """Eres un nutricionista experto en análisis calórico de alimentos.
El usuario te enviará una foto de un plato de comida junto con un objeto de referencia
(generalmente un cubierto: tenedor o cuchillo) para que puedas estimar el tamaño de las porciones.

Responde SIEMPRE en JSON con este formato exacto (sin markdown, sin backticks):
{
  "descripcion": "Descripción breve del plato y sus componentes",
  "kcal_estimadas": 450,
  "confianza": "ALTA",
  "detalle": "Arroz blanco ~200kcal, pechuga de pollo ~180kcal, ensalada ~70kcal"
}

Reglas:
- confianza: ALTA (alimentos claramente visibles), MEDIA (parcialmente visible o ambiguo), BAJA (muy difícil de determinar)
- kcal_estimadas: número entero, calorias totales del plato visible
- Si no hay objeto de referencia visible, asume una porción estándar colombiana y baja la confianza a MEDIA
- Responde SOLO el JSON, sin texto adicional"""


async def analizar_foto_comida(imagen_bytes: bytes, mime_type: str = "image/jpeg") -> FotoAnalisisOut:
    """
    Envía la imagen al LLM Vision y devuelve el análisis calórico.
    Compatible con cualquier endpoint OpenAI /v1/chat/completions.
    """
    imagen_b64 = base64.b64encode(imagen_bytes).decode("utf-8")

    payload = {
        "model": settings.llm_vision_model,
        "max_tokens": 512,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{imagen_b64}",
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": "¿Cuántas calorías tiene este plato? El cubierto es la referencia de tamaño.",
                    },
                ],
            },
        ],
    }

    headers = {"Content-Type": "application/json"}
    if settings.llm_vision_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_vision_api_key}"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(settings.llm_vision_url, json=payload, headers=headers)
        log.info("LLM Vision status: %s", resp.status_code)
        if resp.status_code != 200:
            log.error("LLM Vision error body: %s", resp.text[:500])
        resp.raise_for_status()

    data = resp.json()
    raw_content = data["choices"][0]["message"]["content"].strip()

    # Limpiar posibles backticks que algunos modelos añaden
    raw_content = re.sub(r"```(?:json)?", "", raw_content).strip()

    parsed = json.loads(raw_content)

    return FotoAnalisisOut(
        descripcion=parsed["descripcion"],
        kcal_estimadas=Decimal(str(parsed["kcal_estimadas"])),
        confianza=parsed.get("confianza", "MEDIA"),
        detalle=parsed.get("detalle"),
    )