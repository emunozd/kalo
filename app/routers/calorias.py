from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.models import Perfil, RegistroCaloria, FuenteCaloria, Usuario
from app.schemas.schemas import RegistroCaloriaIn, RegistroCaloriaOut, HistorialCaloriasOut
from app.services.resumen_service import actualizar_resumen

router = APIRouter(prefix="/calorias", tags=["calorias"])


async def _get_objetivo(db: AsyncSession, usuario_id: UUID) -> float:
    result = await db.execute(select(Perfil.objetivo_kcal).where(Perfil.usuario_id == usuario_id))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(
            status_code=400,
            detail="Primero debes registrar tu perfil físico en POST /perfil"
        )
    return obj


@router.post("", response_model=RegistroCaloriaOut, status_code=status.HTTP_201_CREATED)
async def registrar_caloria(
    body: RegistroCaloriaIn,
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Registra una comida o snack de forma manual."""
    objetivo = await _get_objetivo(db, usuario.id)

    registro = RegistroCaloria(
        usuario_id=usuario.id,
        fecha=body.fecha,
        descripcion=body.descripcion,
        kcal=body.kcal,
        fuente=FuenteCaloria.MANUAL,
        nota=body.nota,
    )
    db.add(registro)
    await db.flush()
    await actualizar_resumen(db, usuario.id, body.fecha, objetivo)
    await db.refresh(registro)
    return registro


@router.get("", response_model=list[RegistroCaloriaOut])
async def listar_calorias(
    fecha: date = Query(default_factory=date.today, description="Día a consultar (YYYY-MM-DD)"),
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista todos los registros calóricos de un día específico."""
    result = await db.execute(
        select(RegistroCaloria)
        .where(RegistroCaloria.usuario_id == usuario.id, RegistroCaloria.fecha == fecha)
        .order_by(RegistroCaloria.registrado_en)
    )
    return result.scalars().all()


@router.get("/historial", response_model=list[HistorialCaloriasOut])
async def historial_calorias(
    desde: date = Query(..., description="Fecha inicio (YYYY-MM-DD)"),
    hasta: date = Query(default_factory=date.today, description="Fecha fin (YYYY-MM-DD)"),
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Agrupa los registros por día en un rango de fechas."""
    result = await db.execute(
        select(RegistroCaloria)
        .where(
            RegistroCaloria.usuario_id == usuario.id,
            RegistroCaloria.fecha >= desde,
            RegistroCaloria.fecha <= hasta,
        )
        .order_by(RegistroCaloria.fecha, RegistroCaloria.registrado_en)
    )
    registros = result.scalars().all()

    # Agrupar por fecha
    agrupado: dict[date, list[RegistroCaloria]] = {}
    for r in registros:
        agrupado.setdefault(r.fecha, []).append(r)

    return [
        HistorialCaloriasOut(
            fecha=f,
            total_kcal=sum(r.kcal for r in regs),
            registros=regs,
        )
        for f, regs in sorted(agrupado.items())
    ]


@router.delete("/{registro_id}", status_code=status.HTTP_204_NO_CONTENT)
async def borrar_caloria(
    registro_id: UUID,
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RegistroCaloria).where(
            RegistroCaloria.id == registro_id,
            RegistroCaloria.usuario_id == usuario.id,
        )
    )
    registro = result.scalar_one_or_none()
    if registro is None:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    fecha = registro.fecha
    objetivo = await _get_objetivo(db, usuario.id)
    await db.delete(registro)
    await db.flush()
    await actualizar_resumen(db, usuario.id, fecha, objetivo)
