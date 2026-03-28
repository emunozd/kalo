from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.models import FuenteCaloria, Perfil, RegistroCaloria, Usuario
from app.schemas.schemas import FotoAnalisisOut, FotoConfirmarIn, RegistroCaloriaOut
from app.services.resumen_service import actualizar_resumen
from app.services.vision_client import analizar_foto_comida

router = APIRouter(prefix="/foto", tags=["foto"])

UPLOAD_DIR = Path("/tmp/kalo_fotos")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_SIZE_MB = 10


@router.post("/preview", response_model=FotoAnalisisOut)
async def preview_foto(
    request: Request,
    usuario: Usuario = Depends(get_current_user),
):
    """
    Recibe la imagen como body crudo (bytes) y devuelve la estimación calórica.
    No guarda nada — el usuario confirma después con /foto/confirmar.
    """
    content_type = request.headers.get("content-type", "image/jpeg")
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Formato no soportado. Usa JPEG, PNG o WEBP.")

    imagen_bytes = await request.body()
    if len(imagen_bytes) > MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"La imagen no debe superar {MAX_SIZE_MB}MB")

    if not imagen_bytes:
        raise HTTPException(status_code=400, detail="No se recibió imagen.")

    try:
        analisis = await analizar_foto_comida(imagen_bytes, content_type)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error al analizar la imagen: {str(e)}")

    return analisis


@router.post("/confirmar", response_model=RegistroCaloriaOut, status_code=status.HTTP_201_CREATED)
async def confirmar_foto(
    body: FotoConfirmarIn,
    usuario: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Guarda el registro calórico confirmado por el usuario después del preview.
    El usuario puede ajustar las kcal estimadas antes de confirmar.
    """
    result = await db.execute(select(Perfil.objetivo_kcal).where(Perfil.usuario_id == usuario.id))
    objetivo = result.scalar_one_or_none()
    if objetivo is None:
        raise HTTPException(status_code=400, detail="Primero registra tu perfil en POST /perfil")

    registro = RegistroCaloria(
        usuario_id=usuario.id,
        fecha=body.fecha,
        descripcion=body.descripcion,
        kcal=body.kcal,
        fuente=FuenteCaloria.FOTO_LLM,
        foto_path=body.foto_path,
        nota=body.nota,
    )
    db.add(registro)
    await db.flush()
    await actualizar_resumen(db, usuario.id, body.fecha, objetivo)
    await db.refresh(registro)
    return registro