from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.models import ResumenDiario, Usuario
from app.schemas.schemas import ResumenDiarioOut

router = APIRouter(prefix="/resumen", tags=["resumen"])


def _mensaje_orientacion(disponibles: Decimal, objetivo: Decimal) -> str:
    """Genera un mensaje motivacional según las calorías disponibles."""
    porcentaje = float(disponibles / objetivo * 100) if objetivo else 0

    if disponibles < 0:
        exceso = abs(disponibles)
        return f"⚠️ Has superado tu objetivo en {exceso:.0f} kcal. Considera una actividad física ligera."
    elif disponibles < 200:
        return f"🔴 Solo te quedan {disponibles:.0f} kcal. Elige algo muy liviano para el resto del día."
    elif disponibles < 500:
        return f"🟡 Te quedan {disponibles:.0f} kcal. Suficiente para una comida ligera o snack."
    elif porcentaje > 80:
        return f"🟢 Vas bien. Te quedan {disponibles:.0f} kcal disponibles hoy."
    else:
        return f"✅ Excelente gestión. Tienes {disponibles:.0f} kcal para usar durante el día."


@router.get("/dia", response_model=ResumenDiarioOut)
async def resumen_dia(
    fecha: date = Query(default_factory=date.today, description="Día a consultar (YYYY-MM-DD)"),
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Devuelve el balance calórico completo del día solicitado."""
    result = await db.execute(
        select(ResumenDiario).where(
            ResumenDiario.usuario_id == usuario.id,
            ResumenDiario.fecha == fecha,
        )
    )
    resumen = result.scalar_one_or_none()

    if resumen is None:
        # El día no tiene actividad todavía — devolver estructura vacía
        from app.models.models import Perfil
        perfil_result = await db.execute(select(Perfil).where(Perfil.usuario_id == usuario.id))
        perfil = perfil_result.scalar_one_or_none()
        objetivo = perfil.objetivo_kcal if perfil else Decimal("0")

        return ResumenDiarioOut(
            fecha=fecha,
            kcal_consumidas=Decimal("0"),
            kcal_quemadas=Decimal("0"),
            kcal_objetivo=objetivo,
            kcal_disponibles=objetivo,
            primera_entrada_en=None,
            actualizado_en=None,
            porcentaje_usado=Decimal("0"),
            mensaje_orientacion=f"🌅 Día sin registros aún. Tu objetivo es {objetivo:.0f} kcal.",
        )

    disponibles = resumen.kcal_disponibles or Decimal("0")
    porcentaje = (
        round(resumen.kcal_consumidas / resumen.kcal_objetivo * 100, 1)
        if resumen.kcal_objetivo > 0 else Decimal("0")
    )

    out = ResumenDiarioOut.model_validate(resumen)
    out.porcentaje_usado = porcentaje
    out.mensaje_orientacion = _mensaje_orientacion(disponibles, resumen.kcal_objetivo)
    return out


@router.get("/semana", response_model=list[ResumenDiarioOut])
async def resumen_semana(
    desde: date = Query(..., description="Fecha inicio"),
    hasta: date = Query(default_factory=date.today, description="Fecha fin"),
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista los resúmenes de un rango de fechas, ordenados cronológicamente."""
    result = await db.execute(
        select(ResumenDiario)
        .where(
            ResumenDiario.usuario_id == usuario.id,
            ResumenDiario.fecha >= desde,
            ResumenDiario.fecha <= hasta,
        )
        .order_by(ResumenDiario.fecha)
    )
    resumenes = result.scalars().all()

    salida = []
    for r in resumenes:
        out = ResumenDiarioOut.model_validate(r)
        disp = r.kcal_disponibles or Decimal("0")
        out.porcentaje_usado = (
            round(r.kcal_consumidas / r.kcal_objetivo * 100, 1) if r.kcal_objetivo > 0 else Decimal("0")
        )
        out.mensaje_orientacion = _mensaje_orientacion(disp, r.kcal_objetivo)
        salida.append(out)
    return salida
