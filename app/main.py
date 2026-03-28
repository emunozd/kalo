from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, perfil, calorias, ejercicio, foto, resumen

app = FastAPI(
    title="KALO API",
    description="Asistente calórico personal vía Telegram",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(perfil.router)
app.include_router(calorias.router)
app.include_router(ejercicio.router)
app.include_router(foto.router)
app.include_router(resumen.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "kalo-api"}
