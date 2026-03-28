from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator


# ── Auth ─────────────────────────────────────────────────────

class SolicitarCodigoIn(BaseModel):
    email: EmailStr
    nombre: Optional[str] = None


class VerificarCodigoIn(BaseModel):
    email: EmailStr
    codigo: str = Field(min_length=6, max_length=6)


class VincularTelegramIn(BaseModel):
    telegram_id: int
    telegram_username: Optional[str] = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Perfil / BMR ─────────────────────────────────────────────

class PerfilIn(BaseModel):
    estatura_cm: int = Field(ge=50, le=300)
    peso_kg: Decimal = Field(ge=20, le=500)
    sexo: str = Field(pattern="^(M|F|OTRO)$")
    fecha_nacimiento: date
    factor_actividad: Decimal = Field(default=Decimal("1.2"), ge=1.0, le=2.5)


class PerfilOut(BaseModel):
    id: UUID
    estatura_cm: int
    peso_kg: Decimal
    sexo: str
    fecha_nacimiento: date
    edad: int = 0
    bmr: Decimal
    factor_actividad: Decimal
    objetivo_kcal: Decimal
    actualizado_en: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def calcular_edad(self) -> "PerfilOut":
        hoy = date.today()
        fn = self.fecha_nacimiento
        anios = hoy.year - fn.year
        if (hoy.month, hoy.day) < (fn.month, fn.day):
            anios -= 1
        self.edad = anios
        return self


# ── Calorías ─────────────────────────────────────────────────

class RegistroCaloriaIn(BaseModel):
    fecha: date = Field(default_factory=date.today)
    descripcion: str = Field(min_length=1, max_length=500)
    kcal: Decimal = Field(ge=0)
    nota: Optional[str] = None


class RegistroCaloriaOut(BaseModel):
    id: UUID
    fecha: date
    registrado_en: datetime
    descripcion: str
    kcal: Decimal
    fuente: str
    nota: Optional[str]

    model_config = {"from_attributes": True}


# ── Foto → Calorías ──────────────────────────────────────────

class FotoAnalisisOut(BaseModel):
    descripcion: str
    kcal_estimadas: Decimal
    confianza: str          # ALTA / MEDIA / BAJA
    detalle: Optional[str]  # desglose del LLM


class FotoConfirmarIn(BaseModel):
    fecha: date = Field(default_factory=date.today)
    descripcion: str
    kcal: Decimal = Field(ge=0)
    foto_path: Optional[str] = None
    nota: Optional[str] = None


# ── Ejercicio ────────────────────────────────────────────────

class RegistroEjercicioIn(BaseModel):
    fecha: date = Field(default_factory=date.today)
    descripcion: str = Field(min_length=1, max_length=500)
    duracion_min: Optional[int] = Field(default=None, ge=1)
    kcal_quemadas: Decimal = Field(ge=0)
    nota: Optional[str] = None


class RegistroEjercicioOut(BaseModel):
    id: UUID
    fecha: date
    registrado_en: datetime
    descripcion: str
    duracion_min: Optional[int]
    kcal_quemadas: Decimal
    nota: Optional[str]

    model_config = {"from_attributes": True}


# ── Resumen diario ───────────────────────────────────────────

class ResumenDiarioOut(BaseModel):
    fecha: date
    kcal_consumidas: Decimal
    kcal_quemadas: Decimal
    kcal_objetivo: Decimal
    kcal_disponibles: Optional[Decimal]
    primera_entrada_en: datetime
    actualizado_en: datetime

    # Campos calculados para el bot
    porcentaje_usado: Optional[Decimal] = None
    mensaje_orientacion: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Historial paginado ───────────────────────────────────────

class HistorialCaloriasOut(BaseModel):
    fecha: date
    total_kcal: Decimal
    registros: list[RegistroCaloriaOut]


class HistorialEjercicioOut(BaseModel):
    fecha: date
    total_kcal_quemadas: Decimal
    registros: list[RegistroEjercicioOut]