import enum
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
import zoneinfo

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date,
    Enum, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, LocalDateTime, TZ_BOGOTA


def now_bogota() -> datetime:
    """Naive datetime en hora Bogotá. asyncpg lo guarda tal cual sin convertir."""
    return datetime.now(tz=TZ_BOGOTA).replace(tzinfo=None)


# ── Enums ────────────────────────────────────────────────────

class FuenteCaloria(str, enum.Enum):
    MANUAL   = "MANUAL"
    FOTO_LLM = "FOTO_LLM"


class SexoTipo(str, enum.Enum):
    M = "M"
    F = "F"


# ── Modelos ──────────────────────────────────────────────────

class Usuario(Base):
    __tablename__ = "usuarios"

    id:                 Mapped[UUID]          = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    email:              Mapped[str]           = mapped_column(String, nullable=False, unique=True)
    nombre:             Mapped[str | None]    = mapped_column(String)
    telegram_id:        Mapped[int | None]    = mapped_column(BigInteger, unique=True)
    telegram_username:  Mapped[str | None]    = mapped_column(String)
    activo:             Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)
    creado_en:          Mapped[datetime]      = mapped_column(LocalDateTime, default=now_bogota)
    actualizado_en:     Mapped[datetime]      = mapped_column(LocalDateTime, default=now_bogota)

    # Relaciones
    perfil:             Mapped["Perfil | None"]             = relationship(back_populates="usuario", uselist=False, cascade="all, delete-orphan")
    codigos_otp:        Mapped[list["CodigoOtp"]]           = relationship(back_populates="usuario", cascade="all, delete-orphan")
    registros_calorias: Mapped[list["RegistroCaloria"]]     = relationship(back_populates="usuario", cascade="all, delete-orphan")
    registros_ejercicio:Mapped[list["RegistroEjercicio"]]   = relationship(back_populates="usuario", cascade="all, delete-orphan")
    resumenes_diarios:  Mapped[list["ResumenDiario"]]       = relationship(back_populates="usuario", cascade="all, delete-orphan")


class CodigoOtp(Base):
    __tablename__ = "codigos_otp"

    id:         Mapped[UUID]     = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID]     = mapped_column(PGUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    codigo:     Mapped[str]      = mapped_column(String, nullable=False)
    expira_en:  Mapped[datetime] = mapped_column(LocalDateTime, nullable=False)
    usado:      Mapped[bool]     = mapped_column(Boolean, nullable=False, default=False)
    creado_en:  Mapped[datetime] = mapped_column(LocalDateTime, default=now_bogota)

    usuario: Mapped["Usuario"] = relationship(back_populates="codigos_otp")


class Perfil(Base):
    __tablename__ = "perfiles"

    id:               Mapped[UUID]          = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id:       Mapped[UUID]          = mapped_column(PGUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, unique=True)
    estatura_cm:        Mapped[int]           = mapped_column(Integer, nullable=False)
    peso_kg:            Mapped[Decimal]       = mapped_column(Numeric(5, 2), nullable=False)
    sexo:               Mapped[SexoTipo]      = mapped_column(Enum(SexoTipo, name="sexo_tipo"), nullable=False)
    fecha_nacimiento:   Mapped[date]          = mapped_column(Date, nullable=False)
    bmr:                Mapped[Decimal]       = mapped_column(Numeric(8, 2), nullable=False)
    factor_actividad:   Mapped[Decimal]       = mapped_column(Numeric(4, 2), nullable=False, default=Decimal("1.2"))
    objetivo_kcal:      Mapped[Decimal]       = mapped_column(Numeric(8, 2), nullable=False)
    creado_en:          Mapped[datetime]      = mapped_column(LocalDateTime, default=now_bogota)
    actualizado_en:     Mapped[datetime]      = mapped_column(LocalDateTime, default=now_bogota)

    usuario: Mapped["Usuario"] = relationship(back_populates="perfil")

    __table_args__ = (
        CheckConstraint("estatura_cm BETWEEN 50 AND 300", name="ck_estatura"),
        CheckConstraint("peso_kg BETWEEN 20 AND 500",     name="ck_peso"),
    )

    def calcular_edad(self) -> int:
        """Calcula la edad actual a partir de fecha_nacimiento."""
        hoy = date.today()
        anios = hoy.year - self.fecha_nacimiento.year
        if (hoy.month, hoy.day) < (self.fecha_nacimiento.month, self.fecha_nacimiento.day):
            anios -= 1
        return anios

    def calcular_bmr(self) -> Decimal:
        """Harris-Benedict revisado usando edad calculada desde fecha_nacimiento."""
        kg  = float(self.peso_kg)
        cm  = float(self.estatura_cm)
        age = float(self.calcular_edad())
        if self.sexo == SexoTipo.M:
            bmr = 88.362 + (13.397 * kg) + (4.799 * cm) - (5.677 * age)
        else:
            bmr = 447.593 + (9.247 * kg) + (3.098 * cm) - (4.330 * age)
        return Decimal(str(round(bmr, 2)))


class RegistroCaloria(Base):
    __tablename__ = "registros_calorias"

    id:           Mapped[UUID]          = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id:   Mapped[UUID]          = mapped_column(PGUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    fecha:        Mapped[date]          = mapped_column(Date, nullable=False)       # día de consumo
    registrado_en:Mapped[datetime]      = mapped_column(LocalDateTime, default=now_bogota)  # momento exacto
    descripcion:  Mapped[str]           = mapped_column(Text, nullable=False)
    kcal:         Mapped[Decimal]       = mapped_column(Numeric(8, 2), nullable=False)
    fuente:       Mapped[FuenteCaloria] = mapped_column(Enum(FuenteCaloria, name="fuente_caloria"), nullable=False, default=FuenteCaloria.MANUAL)
    foto_path:    Mapped[str | None]    = mapped_column(Text)
    nota:         Mapped[str | None]    = mapped_column(Text)

    usuario: Mapped["Usuario"] = relationship(back_populates="registros_calorias")

    __table_args__ = (
        CheckConstraint("kcal >= 0", name="ck_kcal_positiva"),
    )


class RegistroEjercicio(Base):
    __tablename__ = "registros_ejercicio"

    id:             Mapped[UUID]     = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id:     Mapped[UUID]     = mapped_column(PGUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    fecha:          Mapped[date]     = mapped_column(Date, nullable=False)       # día del ejercicio
    registrado_en:  Mapped[datetime] = mapped_column(LocalDateTime, default=now_bogota)  # momento del registro
    descripcion:    Mapped[str]      = mapped_column(Text, nullable=False)
    duracion_min:   Mapped[int | None] = mapped_column(Integer)
    kcal_quemadas:  Mapped[Decimal]  = mapped_column(Numeric(8, 2), nullable=False)
    nota:           Mapped[str | None] = mapped_column(Text)

    usuario: Mapped["Usuario"] = relationship(back_populates="registros_ejercicio")

    __table_args__ = (
        CheckConstraint("kcal_quemadas >= 0", name="ck_kcal_quemadas_positiva"),
        CheckConstraint("duracion_min > 0",   name="ck_duracion_positiva"),
    )


class ResumenDiario(Base):
    """
    Un registro por (usuario, fecha).
    kcal_disponibles es columna generada en Postgres (STORED),
    aquí se mapea como atributo de sólo lectura.
    """
    __tablename__ = "resumenes_diarios"

    id:                 Mapped[UUID]    = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id:         Mapped[UUID]    = mapped_column(PGUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    fecha:              Mapped[date]    = mapped_column(Date, nullable=False)
    kcal_consumidas:    Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal("0"))
    kcal_quemadas:      Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal("0"))
    kcal_objetivo:      Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal("0"))
    kcal_disponibles:   Mapped[Decimal | None] = mapped_column(Numeric(8, 2))   # GENERATED STORED en PG
    primera_entrada_en: Mapped[datetime] = mapped_column(LocalDateTime, default=now_bogota)
    actualizado_en:     Mapped[datetime] = mapped_column(LocalDateTime, default=now_bogota)

    usuario: Mapped["Usuario"] = relationship(back_populates="resumenes_diarios")

    __table_args__ = (
        UniqueConstraint("usuario_id", "fecha", name="uq_resumen_usuario_fecha"),
    )