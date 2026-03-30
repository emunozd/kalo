from decimal import Decimal

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

    perfil.estatura_cm       = body.estatura_cm
    perfil.peso_kg           = body.peso_kg
    perfil.sexo              = SexoTipo(body.sexo)
    perfil.fecha_nacimiento  = body.fecha_nacimiento
    perfil.factor_actividad  = body.factor_actividad

    perfil.bmr           = perfil.calcular_bmr()
    mantenimiento        = float(perfil.bmr) * float(body.factor_actividad)

    # Peso saludable con IMC 22
    estatura_m   = body.estatura_cm / 100
    peso_ideal   = 22 * estatura_m ** 2
    diferencia   = float(body.peso_kg) - peso_ideal  # positivo = sobrepeso

    # Ajuste calórico orientado al peso saludable
    # Déficit de 570 kcal recomendado por nutricionista para bajar ~0.5 kg/semana
    # Con Mifflin-St Jeor y factor 1.55: ~2570 - 570 = ~2000 kcal objetivo
    DEFICIT      = 570
    SUPERAVIT    = 300   # kcal/día para subir masa magra
    MARGEN_KG    = 2.0   # rango donde se considera "en peso"

    if diferencia > MARGEN_KG:
        # Sobrepeso — déficit moderado, nunca bajar del BMR + 200 (mínimo seguro)
        objetivo = max(mantenimiento - DEFICIT, float(perfil.bmr) + 200)
    elif diferencia < -MARGEN_KG:
        # Bajo peso — superávit moderado
        objetivo = mantenimiento + SUPERAVIT
    else:
        # En peso saludable — mantenimiento
        objetivo = mantenimiento

    perfil.objetivo_kcal = Decimal(str(round(objetivo, 0)))

    await db.commit()
    await db.refresh(perfil)
    return perfil