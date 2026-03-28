from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.models import Perfil, SexoTipo, Usuario
from app.schemas.schemas import PerfilIn, PerfilOut

router = APIRouter(prefix="/perfil", tags=["perfil"])


@router.get("", response_model=PerfilOut)
async def obtener_perfil(
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Perfil).where(Perfil.usuario_id == usuario.id))
    perfil = result.scalar_one_or_none()
    if perfil is None:
        raise HTTPException(status_code=404, detail="Perfil no encontrado. Regístralo con POST /perfil")
    return perfil


@router.post("", response_model=PerfilOut, status_code=status.HTTP_201_CREATED)
async def crear_o_actualizar_perfil(
    body: PerfilIn,
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Crea o actualiza el perfil físico del usuario y recalcula el BMR.
    Si ya existe perfil, lo reemplaza (PUT semántico en POST).
    """
    result = await db.execute(select(Perfil).where(Perfil.usuario_id == usuario.id))
    perfil = result.scalar_one_or_none()

    if perfil is None:
        perfil = Perfil(usuario_id=usuario.id)
        db.add(perfil)

    perfil.estatura_cm     = body.estatura_cm
    perfil.peso_kg         = body.peso_kg
    perfil.sexo            = SexoTipo(body.sexo)
    perfil.edad            = body.edad
    perfil.factor_actividad = body.factor_actividad

    # Calcular BMR con Harris-Benedict revisado
    perfil.bmr           = perfil.calcular_bmr()
    perfil.objetivo_kcal = round(perfil.bmr * body.factor_actividad, 2)

    await db.commit()
    await db.refresh(perfil)
    return perfil
