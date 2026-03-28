import base64
import json
import logging
import re
from decimal import Decimal

import httpx

from app.core.config import settings
from app.schemas.schemas import FotoAnalisisOut

log = logging.getLogger(__name__)


async def analizar_foto_comida(imagen_bytes: bytes, mime_type: str = "image/jpeg") -> FotoAnalisisOut:
    """
    Envía la imagen al endpoint nativo de AIBase /kalo/analizar-foto-comida
    y devuelve el análisis calórico.
    """
    imagen_b64 = base64.b64encode(imagen_bytes).decode("utf-8")

    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    payload = {"imagen_b64": imagen_b64}

    async with httpx.AsyncClient(timeout=60) as client:
        url = f"{settings.llm_base_url}/analizar-foto-comida"
        resp = await client.post(url, json=payload, headers=headers)
        log.info("LLM Vision status: %s", resp.status_code)
        if resp.status_code != 200:
            log.error("LLM Vision error body: %s", resp.text[:500])
        resp.raise_for_status()

    data = resp.json()

    return FotoAnalisisOut(
        descripcion=data["descripcion"],
        kcal_estimadas=Decimal(str(data["kcal_estimadas"])),
        confianza=data.get("confianza", "MEDIA"),
        detalle=data.get("detalle"),
    )