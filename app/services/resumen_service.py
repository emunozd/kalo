from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def actualizar_resumen(db: AsyncSession, usuario_id: UUID, fecha: date, objetivo_kcal: Decimal) -> None:
    """
    Llama a la función PL/pgSQL que recalcula y hace upsert del resumen diario.
    Debe invocarse después de cada INSERT/DELETE en registros_calorias o registros_ejercicio.
    """
    await db.execute(
        text("SELECT upsert_resumen_diario(:uid, :fecha, :obj)"),
        {"uid": str(usuario_id), "fecha": fecha, "obj": float(objetivo_kcal)},
    )
    await db.commit()
