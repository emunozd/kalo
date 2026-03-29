import random
import string
import zoneinfo
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import create_access_token, get_current_user
from app.models.models import CodigoOtp, Usuario
from app.schemas.schemas import (
    SolicitarCodigoIn, VerificarCodigoIn,
    VincularTelegramIn, TokenOut,
)
from app.services.brevo_client import enviar_codigo_otp

TZ_BOGOTA = zoneinfo.ZoneInfo("America/Bogota")

def _now() -> datetime:
    """Datetime naive en hora Bogotá — consistente con DateTime(timezone=False) en los modelos."""
    return datetime.now(tz=TZ_BOGOTA).replace(tzinfo=None)
from app.services.brevo_client import enviar_codigo_otp

router = APIRouter(prefix="/auth", tags=["auth"])


def _generar_codigo() -> str:
    return "".join(random.choices(string.digits, k=6))


@router.post("/solicitar-codigo", status_code=status.HTTP_200_OK)
async def solicitar_codigo(body: SolicitarCodigoIn, db: AsyncSession = Depends(get_db)):
    """
    Crea o recupera el usuario por email y envía un OTP de 6 dígitos vía Brevo.
    Flujo passwordless: no se almacena contraseña en ningún momento.
    """
    result = await db.execute(select(Usuario).where(Usuario.email == body.email))
    usuario = result.scalar_one_or_none()

    if usuario is None:
        usuario = Usuario(email=body.email, nombre=body.nombre)
        db.add(usuario)
        await db.flush()

    elif body.nombre and not usuario.nombre:
        usuario.nombre = body.nombre

    codigo = _generar_codigo()
    otp = CodigoOtp(
        usuario_id=usuario.id,
        codigo=codigo,
        expira_en=_now() + timedelta(minutes=10),
    )
    db.add(otp)
    await db.commit()

    enviado = await enviar_codigo_otp(body.email, usuario.nombre or body.email, codigo)
    if not enviado:
        raise HTTPException(status_code=502, detail="Error al enviar el código. Intenta de nuevo.")

    return {"mensaje": f"Código enviado a {body.email}"}


@router.post("/verificar-codigo", response_model=TokenOut)
async def verificar_codigo(body: VerificarCodigoIn, db: AsyncSession = Depends(get_db)):
    """Verifica el OTP y retorna un JWT de larga duración."""
    result = await db.execute(
        select(CodigoOtp)
        .join(Usuario)
        .where(
            Usuario.email == body.email,
            CodigoOtp.codigo == body.codigo,
            CodigoOtp.usado.is_(False),
            CodigoOtp.expira_en > _now(),
        )
    )
    otp = result.scalar_one_or_none()
    if otp is None:
        raise HTTPException(status_code=400, detail="Código inválido o expirado")

    otp.usado = True
    await db.commit()

    token = create_access_token(otp.usuario_id)
    return TokenOut(access_token=token)


@router.post("/vincular-telegram", status_code=200)
async def vincular_telegram(
    body: VincularTelegramIn,
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vincula un telegram_id al usuario autenticado."""
    # Verificar que el telegram_id no esté usado por otro usuario
    result = await db.execute(
        select(Usuario).where(Usuario.telegram_id == body.telegram_id)
    )
    existente = result.scalar_one_or_none()
    if existente and existente.id != usuario.id:
        raise HTTPException(status_code=409, detail="Este Telegram ya está vinculado a otra cuenta")

    usuario.telegram_id = body.telegram_id
    usuario.telegram_username = body.telegram_username
    await db.commit()
    return {"mensaje": "Telegram vinculado correctamente"}


@router.delete("/desvincular-telegram", status_code=200)
async def desvincular_telegram(
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Desvincula el Telegram. El historial de datos se preserva."""
    usuario.telegram_id = None
    usuario.telegram_username = None
    await db.commit()
    return {"mensaje": "Telegram desvinculado. Tu historial de datos se conserva."}


@router.get("/token-telegram/{telegram_id}", response_model=TokenOut)
async def token_por_telegram(telegram_id: int, db: AsyncSession = Depends(get_db)):
    """
    El bot llama a este endpoint para obtener un JWT a partir del telegram_id.
    Permite que el bot opere en nombre del usuario sin que este tenga que autenticarse
    cada vez.
    """
    result = await db.execute(
        select(Usuario).where(Usuario.telegram_id == telegram_id, Usuario.activo.is_(True))
    )
    usuario = result.scalar_one_or_none()
    if usuario is None:
        raise HTTPException(status_code=404, detail="Usuario no vinculado")

    token = create_access_token(usuario.id)
    return TokenOut(access_token=token)