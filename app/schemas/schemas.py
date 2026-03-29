from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_serializer, model_validator


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
    sexo: str = Field(pattern="^(M|F)$")
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
    peso_saludable_kg: Optional[Decimal] = None
    diferencia_peso_kg: Optional[Decimal] = None
    kcal_mantenimiento: Optional[Decimal] = None  # referencia sin ajuste
    actualizado_en: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def calcular_campos(self) -> "PerfilOut":
        # Edad
        hoy = date.today()
        fn = self.fecha_nacimiento
        anios = hoy.year - fn.year
        if (hoy.month, hoy.day) < (fn.month, fn.day):
            anios -= 1
        self.edad = anios

        # Peso saludable con IMC 22
        estatura_m = float(self.estatura_cm) / 100
        peso_ideal = round(22 * estatura_m ** 2, 1)
        self.peso_saludable_kg  = Decimal(str(peso_ideal))
        self.diferencia_peso_kg = Decimal(str(round(peso_ideal - float(self.peso_kg), 1)))

        # Kcal de mantenimiento puro (sin ajuste de meta)
        self.kcal_mantenimiento = Decimal(str(round(float(self.bmr) * float(self.factor_actividad), 0)))

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

    @field_serializer("registrado_en")
    def serializar_registrado_en(self, v: datetime) -> str:
        from datetime import timedelta
        # asyncpg entrega TIMESTAMPTZ en UTC — convertir a Bogotá (UTC-5)
        bogota = v.replace(tzinfo=None) - timedelta(hours=5) if v.tzinfo else v
        return bogota.strftime("%Y-%m-%dT%H:%M:%S")


# ── Foto → Calorías ──────────────────────────────────────────

class FotoAnalisisOut(BaseModel):
    tipo: str = "PLATO"
    descripcion: str
    kcal_estimadas: Decimal
    confianza: str
    detalle: Optional[str]
    kcal_por_porcion: Optional[Decimal] = None
    porciones_por_envase: Optional[Decimal] = None
    porcion_g: Optional[Decimal] = None


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

    @field_serializer("registrado_en")
    def serializar_registrado_en(self, v: datetime) -> str:
        from datetime import timedelta
        bogota = v.replace(tzinfo=None) - timedelta(hours=5) if v.tzinfo else v
        return bogota.strftime("%Y-%m-%dT%H:%M:%S")


# ── Resumen diario ───────────────────────────────────────────

class ResumenDiarioOut(BaseModel):
    fecha: date
    kcal_consumidas: Decimal
    kcal_quemadas: Decimal
    kcal_objetivo: Decimal
    kcal_disponibles: Optional[Decimal]
    primera_entrada_en: Optional[datetime] = None
    actualizado_en: Optional[datetime] = None
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