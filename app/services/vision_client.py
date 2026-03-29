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

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            url = f"{settings.llm_base_url}/analizar-foto-comida"
            log.info("Llamando a LLM Vision: %s (imagen: %d bytes)", url, len(imagen_bytes))
            resp = await client.post(url, json=payload, headers=headers)
            log.info("LLM Vision status: %s", resp.status_code)
            if resp.status_code != 200:
                log.error("LLM Vision error body: %s", resp.text[:1000])
            resp.raise_for_status()
            log.info("LLM Vision response: %s", resp.text[:500])
    except Exception as e:
        log.error("Excepción en LLM Vision: %s: %s", type(e).__name__, e)
        raise

    data = resp.json()
    tipo = data.get("tipo", "PLATO").upper()

    if tipo == "TABLA_NUTRICIONAL":
        kcal_porcion = Decimal(str(data.get("kcal_por_porcion", 0)))
        return FotoAnalisisOut(
            tipo="TABLA_NUTRICIONAL",
            descripcion=data.get("producto") or "Tabla nutricional",
            kcal_estimadas=kcal_porcion,  # por porción — el bot preguntará cuántas
            confianza="ALTA",
            detalle=data.get("detalle") or f"{kcal_porcion} kcal por porción",
            kcal_por_porcion=kcal_porcion,
            porcion_g=Decimal(str(data["porcion_g"])) if data.get("porcion_g") else None,
        )

    return FotoAnalisisOut(
        tipo="PLATO",
        descripcion=data["descripcion"],
        kcal_estimadas=Decimal(str(data["kcal_estimadas"])),
        confianza=data.get("confianza", "MEDIA"),
        detalle=data.get("detalle"),
    )