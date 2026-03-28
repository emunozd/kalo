from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.models import Perfil, RegistroEjercicio, Usuario
from app.schemas.schemas import RegistroEjercicioIn, RegistroEjercicioOut, HistorialEjercicioOut
from app.services.resumen_service import actualizar_resumen

router = APIRouter(prefix="/ejercicio", tags=["ejercicio"])


async def _get_objetivo(db, usuario_id):
    result = await db.execute(select(Perfil.objetivo_kcal).where(Perfil.usuario_id == usuario_id))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=400, detail="Primero registra tu perfil en POST /perfil")
    return obj


@router.post("", response_model=RegistroEjercicioOut, status_code=status.HTTP_201_CREATED)
async def registrar_ejercicio(
    body: RegistroEjercicioIn,
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    objetivo = await _get_objetivo(db, usuario.id)

    registro = RegistroEjercicio(
        usuario_id=usuario.id,
        fecha=body.fecha,
        descripcion=body.descripcion,
        duracion_min=body.duracion_min,
        kcal_quemadas=body.kcal_quemadas,
        nota=body.nota,
    )
    db.add(registro)
    await db.flush()
    await actualizar_resumen(db, usuario.id, body.fecha, objetivo)
    await db.refresh(registro)
    return registro


@router.get("", response_model=list[RegistroEjercicioOut])
async def listar_ejercicios(
    fecha: date = Query(default_factory=date.today),
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RegistroEjercicio)
        .where(RegistroEjercicio.usuario_id == usuario.id, RegistroEjercicio.fecha == fecha)
        .order_by(RegistroEjercicio.registrado_en)
    )
    return result.scalars().all()


@router.get("/historial", response_model=list[HistorialEjercicioOut])
async def historial_ejercicio(
    desde: date = Query(...),
    hasta: date = Query(default_factory=date.today),
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RegistroEjercicio)
        .where(
            RegistroEjercicio.usuario_id == usuario.id,
            RegistroEjercicio.fecha >= desde,
            RegistroEjercicio.fecha <= hasta,
        )
        .order_by(RegistroEjercicio.fecha, RegistroEjercicio.registrado_en)
    )
    registros = result.scalars().all()

    agrupado: dict[date, list[RegistroEjercicio]] = {}
    for r in registros:
        agrupado.setdefault(r.fecha, []).append(r)

    return [
        HistorialEjercicioOut(
            fecha=f,
            total_kcal_quemadas=sum(r.kcal_quemadas for r in regs),
            registros=regs,
        )
        for f, regs in sorted(agrupado.items())
    ]


@router.delete("/{registro_id}", status_code=status.HTTP_204_NO_CONTENT)
async def borrar_ejercicio(
    registro_id: UUID,
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RegistroEjercicio).where(
            RegistroEjercicio.id == registro_id,
            RegistroEjercicio.usuario_id == usuario.id,
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
