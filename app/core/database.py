from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import DateTime as SADateTime
from sqlalchemy.types import TypeDecorator
import zoneinfo
from datetime import datetime

TZ_BOGOTA = zoneinfo.ZoneInfo("America/Bogota")

from app.core.config import settings


class LocalDateTime(TypeDecorator):
    """
    Guarda el datetime tal como viene desde Python, sin conversión a UTC.
    asyncpg convierte datetimes aware a UTC — este decorator los convierte
    a naive antes de enviarlos a Postgres, preservando la hora local.
    Al leer, retorna el valor naive tal como está en la BD.
    """
    impl = SADateTime(timezone=False)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, datetime):
            # Si tiene tzinfo, convertir a hora Bogotá y quitar tzinfo
            if value.tzinfo is not None:
                value = value.astimezone(TZ_BOGOTA).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return value

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session