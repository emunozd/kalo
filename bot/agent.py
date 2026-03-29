"""
agent.py — Cliente LLM para KALO bot.

Reemplaza el clasificador regex por llamadas reales al LLM via AIBase.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx

log = logging.getLogger(__name__)


class Intent(str, Enum):
    COMIDA    = "COMIDA"
    EJERCICIO = "EJERCICIO"
    CONSULTA  = "CONSULTA"
    OTRO      = "OTRO"


@dataclass
class IntentResult:
    intent: Intent
    confianza: str = "MEDIA"


@dataclass
class InferenciaComida:
    descripcion: str
    kcal: int
    detalle: Optional[str] = None
    confianza: str = "MEDIA"
    nota: Optional[str] = None


@dataclass
class InferenciaEjercicio:
    descripcion: str
    kcal_quemadas: int
    duracion_min: Optional[int] = None
    distancia_km: Optional[float] = None
    confianza: str = "MEDIA"
    nota: Optional[str] = None


class KaloLLMClient:
    """Cliente HTTP hacia los endpoints /kalo/ de AIBase."""

    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    async def clasificar_intent(self, texto: str) -> IntentResult:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{self.base_url}/clasificar-intent",
                json={"texto": texto},
                headers=self.headers,
            )
            r.raise_for_status()
            data = r.json()
        try:
            intent = Intent(data.get("intent", "OTRO").upper())
        except ValueError:
            intent = Intent.OTRO
        return IntentResult(intent=intent, confianza=data.get("confianza", "MEDIA"))

    async def inferir_comida(self, texto: str) -> InferenciaComida:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{self.base_url}/inferir-comida",
                json={"texto": texto},
                headers=self.headers,
            )
            r.raise_for_status()
            data = r.json()
        return InferenciaComida(
            descripcion=data.get("descripcion", texto),
            kcal=int(data.get("kcal", 0)),
            detalle=data.get("detalle"),
            confianza=data.get("confianza", "MEDIA"),
            nota=data.get("nota"),
        )

    async def inferir_ejercicio(self, texto: str, peso_kg: float = 70.0, edad: int = 30) -> InferenciaEjercicio:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{self.base_url}/inferir-ejercicio",
                json={"texto": texto, "peso_kg": peso_kg, "edad": edad},
                headers=self.headers,
            )
            r.raise_for_status()
            data = r.json()
        return InferenciaEjercicio(
            descripcion=data.get("descripcion", texto),
            kcal_quemadas=int(data.get("kcal_quemadas", 0)),
            duracion_min=data.get("duracion_min"),
            distancia_km=data.get("distancia_km"),
            confianza=data.get("confianza", "MEDIA"),
            nota=data.get("nota"),
        )