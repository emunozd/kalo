"""
KALO Bot — Asistente calórico personal para Telegram.

Comandos:
  /start          — Bienvenida y menú
  /vincular       — Vincular cuenta KALO con email
  /perfil         — Ver o registrar datos físicos (BMR)
  /calorias       — Registrar comida manualmente
  /ejercicio      — Registrar actividad física
  /resumen        — Balance calórico del día
  /historial      — Registros de los últimos días
  /borrar <n>     — Borrar registro de la última lista
  /desvincular    — Desvincular Telegram

Texto libre: el agente clasifica el intent y actúa.
Foto: se analiza con LLM Vision y se pide confirmación.
"""

import os
import logging
import re
from datetime import date, timedelta
from decimal import Decimal

import httpx
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler,
    filters,
)

from bot.agent import Intent, KaloLLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

API_BASE       = os.environ["API_BASE_URL"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
LLM_BASE_URL   = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY    = os.environ.get("LLM_API_KEY", "")

llm = KaloLLMClient(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

def _es_cancelar(texto: str) -> bool:
    """Verifica si el texto es una palabra de cancelación."""
    return bool(re.match(
        r"^(\/cancelar|cancelar|salir|parar|para|stop|no|nada|olvida|olvídalo|déjalo)$",
        texto.strip(),
        re.IGNORECASE,
    ))

def _hora_local(registrado_en: str) -> str:
    """Extrae HH:MM del timestamp sin conversión — la BD ya guarda en hora Bogotá."""
    try:
        parte = registrado_en.replace("T", " ")
        return parte[11:16]
    except Exception:
        return registrado_en[11:16]

CANCELAR_FILTER = filters.Regex(
    re.compile(r"^(\/cancelar|cancelar|salir|parar|para|stop|no|nada|olvida|olvídalo|déjalo)$", re.IGNORECASE)
)

(
    VINCULAR_EMAIL,
    VINCULAR_CODIGO,
    PERFIL_ESTATURA,
    PERFIL_PESO,
    PERFIL_SEXO,
    PERFIL_NACIMIENTO,
    PERFIL_FACTOR,
    CALORIA_DESC,
    CALORIA_KCAL,
    CALORIA_FECHA,
    EJERCICIO_DESC,
    EJERCICIO_KCAL,
    EJERCICIO_DUR,
    EJERCICIO_FECHA,
    FOTO_CONFIRMAR,
) = range(15)


# ── Cliente HTTP helpers ─────────────────────────────────────

async def _get_token(telegram_id: int) -> str | None:
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10) as c:
        r = await c.get(f"/auth/token-telegram/{telegram_id}")
        if r.status_code == 200:
            return r.json()["access_token"]
    return None


async def _api(method: str, path: str, token: str, **kwargs) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30) as c:
        return await getattr(c, method)(path, headers=headers, **kwargs)


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Decorador de autenticación ───────────────────────────────

async def _require_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Obtiene el JWT del usuario. Si no tiene cuenta vinculada, lo avisa."""
    token = context.user_data.get("token")
    if not token:
        token = await _get_token(update.effective_user.id)
        if token:
            context.user_data["token"] = token
    if not token:
        await update.message.reply_text(
            "⚠️ Primero debes vincular tu cuenta KALO.\n"
            "Usa /vincular para comenzar."
        )
    return token


# ── /start ───────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 ¡Hola, {nombre}! Soy *KALO*, tu asistente calórico.\n\n"
        "📋 *Comandos disponibles:*\n"
        "/vincular — Conectar tu cuenta con email\n"
        "/perfil — Ver o actualizar tu BMR\n"
        "/calorias — Registrar una comida\n"
        "/ejercicio — Registrar actividad física\n"
        "/resumen — Balance calórico de hoy\n"
        "/historial — Tus últimos registros\n"
        "/borrar — Eliminar un registro\n\n"
        "También puedes escribirme libremente o enviarme una *foto de tu plato* 🍽️ "
        "y estimo las calorías automáticamente.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /vincular ────────────────────────────────────────────────

async def cmd_vincular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📧 Ingresa tu correo electrónico para vincular tu cuenta KALO:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return VINCULAR_EMAIL


async def vincular_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip().lower()
    context.user_data["vincular_email"] = email

    async with httpx.AsyncClient(base_url=API_BASE, timeout=10) as c:
        r = await c.post("/auth/solicitar-codigo", json={"email": email})

    if r.status_code == 200:
        await update.message.reply_text(
            f"✅ Código enviado a *{email}*.\n"
            "Revisa tu bandeja e ingresa el código de 6 dígitos:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return VINCULAR_CODIGO
    else:
        await update.message.reply_text("❌ Error al enviar el código. Intenta de nuevo.")
        return ConversationHandler.END


async def vincular_codigo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    codigo = update.message.text.strip()
    email = context.user_data.get("vincular_email")

    async with httpx.AsyncClient(base_url=API_BASE, timeout=10) as c:
        r = await c.post("/auth/verificar-codigo", json={"email": email, "codigo": codigo})

    if r.status_code != 200:
        await update.message.reply_text("❌ Código inválido o expirado. Intenta /vincular de nuevo.")
        return ConversationHandler.END

    token = r.json()["access_token"]
    context.user_data["token"] = token

    # Vincular telegram_id
    tg_user = update.effective_user
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10) as c:
        await c.post(
            "/auth/vincular-telegram",
            json={"telegram_id": tg_user.id, "telegram_username": tg_user.username},
            headers=_auth_headers(token),
        )

    await update.message.reply_text(
        "🎉 ¡Cuenta vinculada exitosamente!\n\n"
        "Ahora regístrate tu perfil físico para calcular tu BMR.\n"
        "Usa /perfil para comenzar.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ── /desvincular ─────────────────────────────────────────────

async def cmd_desvincular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return

    async with httpx.AsyncClient(base_url=API_BASE, timeout=10) as c:
        await c.delete("/auth/desvincular-telegram", headers=_auth_headers(token))

    context.user_data.clear()
    await update.message.reply_text(
        "✅ Telegram desvinculado. Tu historial de datos se conserva.\n"
        "Puedes volver a vincular cuando quieras con /vincular."
    )


# ── /perfil ──────────────────────────────────────────────────

async def cmd_perfil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return

    r = await _api("get", "/perfil", token)
    if r.status_code == 200:
        p = r.json()
        diff = float(p.get("diferencia_peso_kg", 0))
        mantenim = float(p.get("kcal_mantenimiento") or p["objetivo_kcal"])
        objetivo = float(p["objetivo_kcal"])

        if diff < -2:
            # diferencia negativa = peso_ideal - peso_actual < 0 = sobrepeso
            estado_peso = f"⬇️ Estás *{abs(diff):.1f} kg* por encima de tu peso saludable"
            meta_txt = f"🎯 Objetivo para *bajar peso*: *{objetivo:.0f} kcal/día* (-500 déficit)\n📌 Mantenimiento: {mantenim:.0f} kcal/día"
        elif diff > 2:
            # diferencia positiva = peso_ideal - peso_actual > 0 = bajo peso
            estado_peso = f"⬆️ Te faltan *{abs(diff):.1f} kg* para tu peso saludable"
            meta_txt = f"🎯 Objetivo para *ganar peso*: *{objetivo:.0f} kcal/día* (+300 superávit)\n📌 Mantenimiento: {mantenim:.0f} kcal/día"
        else:
            estado_peso = "✅ Estás en tu peso saludable"
            meta_txt = f"🎯 Objetivo de mantenimiento: *{objetivo:.0f} kcal/día*"

        await update.message.reply_text(
            f"📊 *Tu perfil KALO*\n\n"
            f"📏 Estatura: {p['estatura_cm']} cm\n"
            f"⚖️ Peso actual: {float(p['peso_kg']):.1f} kg\n"
            f"🏆 Peso saludable: *{float(p['peso_saludable_kg']):.1f} kg* (IMC 22)\n"
            f"{estado_peso}\n\n"
            f"🔥 BMR: *{float(p['bmr']):.0f} kcal/día* (en reposo)\n"
            f"{meta_txt}\n\n"
            "¿Deseas actualizar tu perfil? /perfil\_actualizar",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # No tiene perfil — iniciar registro
    await update.message.reply_text(
        "📋 Vamos a registrar tu perfil para calcular tu BMR.\n\n"
        "¿Cuál es tu *estatura en centímetros*? (ej: 170)",
        parse_mode=ParseMode.MARKDOWN,
    )
    return PERFIL_ESTATURA


async def cmd_perfil_actualizar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return
    await update.message.reply_text("¿Cuál es tu *estatura en centímetros*?", parse_mode=ParseMode.MARKDOWN)
    return PERFIL_ESTATURA


async def perfil_estatura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cm = int(update.message.text.strip())
        assert 50 <= cm <= 300
    except (ValueError, AssertionError):
        await update.message.reply_text("⚠️ Ingresa un valor válido entre 50 y 300 cm.")
        return PERFIL_ESTATURA

    context.user_data["perfil_estatura"] = cm
    await update.message.reply_text("⚖️ ¿Cuál es tu *peso en kg*? (ej: 68.5)", parse_mode=ParseMode.MARKDOWN)
    return PERFIL_PESO


async def perfil_peso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        kg = float(update.message.text.strip().replace(",", "."))
        assert 20 <= kg <= 500
    except (ValueError, AssertionError):
        await update.message.reply_text("⚠️ Ingresa un valor válido entre 20 y 500 kg.")
        return PERFIL_PESO

    context.user_data["perfil_peso"] = kg
    keyboard = [["Masculino", "Femenino"]]
    await update.message.reply_text(
        "👤 ¿Cuál es tu sexo biológico? (para el cálculo de BMR)",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return PERFIL_SEXO


async def perfil_sexo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sexo_map = {"masculino": "M", "femenino": "F"}
    sexo = sexo_map.get(update.message.text.strip().lower())
    if not sexo:
        await update.message.reply_text("⚠️ Selecciona una opción válida.")
        return PERFIL_SEXO

    context.user_data["perfil_sexo"] = sexo
    await update.message.reply_text(
        "🎂 ¿Cuál es tu fecha de nacimiento?\n\nFormato: *DD/MM/AAAA* (ej: 15/04/1990)",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    return PERFIL_NACIMIENTO


async def perfil_nacimiento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from datetime import datetime, date
    texto = update.message.text.strip()
    try:
        fn = datetime.strptime(texto, "%d/%m/%Y").date()
        # Validar rango razonable
        hoy = date.today()
        edad = hoy.year - fn.year - ((hoy.month, hoy.day) < (fn.month, fn.day))
        assert 5 <= edad <= 120, "Edad fuera de rango"
        assert fn < hoy, "Fecha futura"
    except (ValueError, AssertionError):
        await update.message.reply_text(
            "⚠️ Fecha inválida. Usa el formato *DD/MM/AAAA* (ej: 15/04/1990)",
            parse_mode=ParseMode.MARKDOWN,
        )
        return PERFIL_NACIMIENTO

    context.user_data["perfil_nacimiento"] = fn.isoformat()
    keyboard = [["1.2 — Sedentario", "1.375 — Ligero"], ["1.55 — Moderado", "1.725 — Activo"], ["1.9 — Muy activo"]]
    await update.message.reply_text(
        "🏃 ¿Cuál es tu nivel de actividad física?\n\n"
        "• *Sedentario*: poca o nada de ejercicio\n"
        "• *Ligero*: 1-3 días/semana\n"
        "• *Moderado*: 3-5 días/semana\n"
        "• *Activo*: 6-7 días/semana\n"
        "• *Muy activo*: ejercicio intenso diario",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return PERFIL_FACTOR


async def perfil_factor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    factor_map = {
        "1.2": 1.2, "sedentario": 1.2,
        "1.375": 1.375, "ligero": 1.375,
        "1.55": 1.55, "moderado": 1.55,
        "1.725": 1.725, "activo": 1.725,
        "1.9": 1.9, "muy activo": 1.9,
    }
    factor = None
    for key, val in factor_map.items():
        if key in texto.lower():
            factor = val
            break

    if factor is None:
        await update.message.reply_text("⚠️ Selecciona una opción del teclado.")
        return PERFIL_FACTOR

    token = context.user_data.get("token")
    payload = {
        "estatura_cm": context.user_data["perfil_estatura"],
        "peso_kg": context.user_data["perfil_peso"],
        "sexo": context.user_data["perfil_sexo"],
        "fecha_nacimiento": context.user_data["perfil_nacimiento"],
        "factor_actividad": factor,
    }

    r = await _api("post", "/perfil", token, json=payload)
    if r.status_code in (200, 201):
        p = r.json()
        await update.message.reply_text(
            f"✅ *Perfil guardado*\n\n"
            f"🔥 Tu BMR es: *{float(p['bmr']):.0f} kcal/día* (en reposo)\n"
            f"🎯 Tu objetivo diario: *{float(p['objetivo_kcal']):.0f} kcal/día*\n\n"
            "¡Listo para empezar! Usa /calorias para registrar tus comidas.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text("❌ Error al guardar el perfil. Intenta de nuevo.")

    return ConversationHandler.END


def _formato_resumen_inline(s: dict) -> str:
    """Genera texto de resumen con mapa de calor para mostrar tras registrar."""
    consumidas  = float(s.get("kcal_consumidas", 0))
    objetivo    = float(s.get("kcal_objetivo", 1))
    disponibles = float(s.get("kcal_disponibles") or objetivo)
    pct = (consumidas / objetivo * 100) if objetivo > 0 else 0
    bloques = min(int(pct / 10), 10)

    if pct < 50:      color = "🟦"
    elif pct < 75:    color = "🟩"
    elif pct < 90:    color = "🟨"
    elif pct <= 100:  color = "🟧"
    else:             color = "🟥"

    barra = color * bloques + "⬜" * (10 - bloques)
    extra = " ⚠️" if pct > 100 else ""
    return (
        f"\n\n{barra}{extra}  *{pct:.0f}%*\n"
        f"📊 Disponibles: *{disponibles:.0f} kcal*\n"
        f"{s.get('mensaje_orientacion', '')}"
    )


# ── /resumen ─────────────────────────────────────────────────

async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return

    r = await _api("get", "/resumen/dia", token)
    if r.status_code != 200:
        await update.message.reply_text("❌ Error al obtener el resumen.")
        return

    s = r.json()
    consumidas  = float(s["kcal_consumidas"])
    quemadas    = float(s["kcal_quemadas"])
    objetivo    = float(s["kcal_objetivo"])
    disponibles = float(s.get("kcal_disponibles") or objetivo)

    # Porcentaje consumido respecto al objetivo
    pct = (consumidas / objetivo * 100) if objetivo > 0 else 0

    # Mapa de calor — barra de 10 bloques
    bloques_llenos = min(int(pct / 10), 10)
    bloques_extra  = max(int((pct - 100) / 10), 0)

    if pct < 50:
        color = "🟦"   # azul — muy por debajo
    elif pct < 75:
        color = "🟩"   # verde — bien
    elif pct < 90:
        color = "🟨"   # amarillo — acercándose
    elif pct <= 100:
        color = "🟧"   # naranja — casi en el límite
    else:
        color = "🟥"   # rojo — pasado el objetivo

    barra = color * bloques_llenos + "⬜" * (10 - bloques_llenos)
    if bloques_extra > 0:
        barra += " ⚠️"

    await update.message.reply_text(
        f"📊 *Balance de hoy — {s['fecha']}*\n\n"
        f"{barra}  *{pct:.0f}%*\n\n"
        f"🎯 Objetivo: {objetivo:.0f} kcal\n"
        f"🍽️ Consumidas: {consumidas:.0f} kcal\n"
        f"🏃 Quemadas (ejercicio): {quemadas:.0f} kcal\n"
        f"✨ Disponibles: *{disponibles:.0f} kcal*\n\n"
        f"{s.get('mensaje_orientacion', '')}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /calorias ────────────────────────────────────────────────

async def cmd_calorias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return
    await update.message.reply_text("🍽️ ¿Qué comiste? Describe brevemente el plato o alimento:")
    return CALORIA_DESC


async def caloria_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _es_cancelar(update.message.text):
        return await cancelar(update, context)
    context.user_data["cal_desc"] = update.message.text.strip()
    await update.message.reply_text("🔢 ¿Cuántas calorías tenía aproximadamente? (número entero, ej: 350)")
    return CALORIA_KCAL


async def caloria_kcal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _es_cancelar(update.message.text):
        return await cancelar(update, context)
    try:
        kcal = float(update.message.text.strip().replace(",", "."))
        assert kcal >= 0
    except (ValueError, AssertionError):
        await update.message.reply_text("⚠️ Ingresa un número válido de calorías.")
        return CALORIA_KCAL

    context.user_data["cal_kcal"] = kcal
    keyboard = [["Hoy", "Ayer"]]
    await update.message.reply_text(
        "📅 ¿En qué fecha fue esta comida?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return CALORIA_FECHA


async def caloria_fecha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _es_cancelar(update.message.text):
        return await cancelar(update, context)
    texto = update.message.text.strip().lower()
    if texto == "hoy":
        fecha = str(date.today())
    elif texto == "ayer":
        fecha = str(date.today() - timedelta(days=1))
    elif texto == "cancelar":
        await update.message.reply_text("❌ Registro cancelado.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    else:
        try:
            from datetime import datetime
            fecha = datetime.strptime(texto, "%Y-%m-%d").date().isoformat()
        except ValueError:
            await update.message.reply_text("⚠️ Formato inválido. Usa YYYY-MM-DD o selecciona Hoy/Ayer.")
            return CALORIA_FECHA

    token = context.user_data.get("token")
    payload = {
        "descripcion": context.user_data["cal_desc"],
        "kcal": context.user_data["cal_kcal"],
        "fecha": fecha,
    }
    r = await _api("post", "/calorias", token, json=payload)

    if r.status_code == 201:
        # Obtener resumen actualizado
        rs = await _api("get", f"/resumen/dia?fecha={fecha}", token)
        resumen_txt = _formato_resumen_inline(rs.json()) if rs.status_code == 200 else ""

        await update.message.reply_text(
            f"✅ Registrado: *{context.user_data['cal_desc']}* — {context.user_data['cal_kcal']:.0f} kcal{resumen_txt}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text("❌ Error al guardar el registro. ¿Ya tienes perfil registrado? /perfil")

    return ConversationHandler.END


# ── /ejercicio ───────────────────────────────────────────────

async def cmd_ejercicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return
    await update.message.reply_text("🏃 ¿Qué actividad física hiciste?")
    return EJERCICIO_DESC


async def ejercicio_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _es_cancelar(update.message.text):
        return await cancelar(update, context)
    context.user_data["eje_desc"] = update.message.text.strip()
    await update.message.reply_text("⏱️ ¿Cuántos minutos duró? (escribe 0 si no lo sabes)")
    return EJERCICIO_DUR


async def ejercicio_dur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _es_cancelar(update.message.text):
        return await cancelar(update, context)
    try:
        dur = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("⚠️ Ingresa un número.")
        return EJERCICIO_DUR

    context.user_data["eje_dur"] = dur if dur > 0 else None
    await update.message.reply_text("🔥 ¿Cuántas calorías quemaste aproximadamente?")
    return EJERCICIO_KCAL


async def ejercicio_kcal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _es_cancelar(update.message.text):
        return await cancelar(update, context)
    try:
        kcal = float(update.message.text.strip().replace(",", "."))
        assert kcal >= 0
    except (ValueError, AssertionError):
        await update.message.reply_text("⚠️ Ingresa un número válido.")
        return EJERCICIO_KCAL

    context.user_data["eje_kcal"] = kcal
    keyboard = [["Hoy", "Ayer"]]
    await update.message.reply_text(
        "📅 ¿En qué fecha hiciste el ejercicio?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return EJERCICIO_FECHA


async def ejercicio_fecha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _es_cancelar(update.message.text):
        return await cancelar(update, context)
    texto = update.message.text.strip().lower()
    if texto == "hoy":
        fecha = str(date.today())
    elif texto == "ayer":
        fecha = str(date.today() - timedelta(days=1))
    elif texto == "cancelar":
        await update.message.reply_text("❌ Cancelado.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    else:
        try:
            from datetime import datetime
            fecha = datetime.strptime(texto, "%Y-%m-%d").date().isoformat()
        except ValueError:
            await update.message.reply_text("⚠️ Formato inválido. Usa YYYY-MM-DD o selecciona Hoy/Ayer.")
            return EJERCICIO_FECHA

    token = context.user_data.get("token")
    payload = {
        "descripcion": context.user_data["eje_desc"],
        "kcal_quemadas": context.user_data["eje_kcal"],
        "fecha": fecha,
    }
    if context.user_data.get("eje_dur"):
        payload["duracion_min"] = context.user_data["eje_dur"]

    r = await _api("post", "/ejercicio", token, json=payload)
    if r.status_code == 201:
        rs = await _api("get", f"/resumen/dia?fecha={fecha}", token)
        resumen_txt = _formato_resumen_inline(rs.json()) if rs.status_code == 200 else ""

        await update.message.reply_text(
            f"✅ Ejercicio registrado: *{context.user_data['eje_desc']}* — {context.user_data['eje_kcal']:.0f} kcal quemadas{resumen_txt}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text("❌ Error al guardar el ejercicio.")

    return ConversationHandler.END


# ── /historial ───────────────────────────────────────────────

async def cmd_historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return

    desde = (date.today() - timedelta(days=6)).isoformat()
    hasta = date.today().isoformat()

    # Obtener registros individuales de calorías y ejercicio
    rc = await _api("get", f"/calorias/historial?desde={desde}&hasta={hasta}", token)
    re_ = await _api("get", f"/ejercicio/historial?desde={desde}&hasta={hasta}", token)

    registros = []

    if rc.status_code == 200:
        for dia in rc.json():
            for r in dia.get("registros", []):
                registros.append({
                    "id":   r["id"],
                    "tipo": "🍽️",
                    "fecha": r["fecha"],
                    "hora": _hora_local(r["registrado_en"]),
                    "desc": r["descripcion"],
                    "kcal": float(r["kcal"]),
                })

    if re_.status_code == 200:
        for dia in re_.json():
            for r in dia.get("registros", []):
                registros.append({
                    "id":   r["id"],
                    "tipo": "🏃",
                    "fecha": r["fecha"],
                    "hora": _hora_local(r["registrado_en"]),
                    "desc": r["descripcion"],
                    "kcal": float(r["kcal_quemadas"]),
                })

    if not registros:
        await update.message.reply_text("📭 Sin registros esta semana.")
        return

    # Ordenar por fecha+hora descendente y tomar últimos 10
    registros.sort(key=lambda x: (x["fecha"], x["hora"]), reverse=True)
    registros = registros[:10]

    # Guardar en contexto para /borrar
    context.user_data["historial"] = registros

    lineas = ["📋 *Últimos registros*\nEscribe *borrar N* para eliminar uno:\n"]
    for i, r in enumerate(registros, 1):
        lineas.append(
            f"*{i}.* {r['tipo']} {r['fecha']} {r['hora']} — {r['desc']} · *{r['kcal']:.0f} kcal*"
        )

    await update.message.reply_text("\n".join(lineas), parse_mode=ParseMode.MARKDOWN)


# ── /borrar ──────────────────────────────────────────────────

async def cmd_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: /borrar N\n"
            "Donde N es el número del registro en /historial\n"
            "Ejemplo: /borrar 3"
        )
        return

    try:
        n = int(args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Ingresa el número del registro. Ejemplo: /borrar 2")
        return

    historial = context.user_data.get("historial", [])
    if not historial:
        await update.message.reply_text("Primero usa /historial para ver tus registros.")
        return

    if n < 1 or n > len(historial):
        await update.message.reply_text(f"⚠️ Número inválido. Elige entre 1 y {len(historial)}.")
        return

    registro = historial[n - 1]
    registro_id = registro["id"]
    tipo = registro["tipo"]

    # Intentar borrar en calorías o ejercicio según tipo
    if tipo == "🍽️":
        r = await _api("delete", f"/calorias/{registro_id}", token)
    else:
        r = await _api("delete", f"/ejercicio/{registro_id}", token)

    if r.status_code == 204:
        context.user_data["historial"].pop(n - 1)
        await update.message.reply_text(
            f"✅ Eliminado: *{registro['desc']}* — {registro['kcal']:.0f} kcal",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text("❌ No pude eliminar ese registro.")


# ── Foto de comida ───────────────────────────────────────────

async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return

    # Si estamos esperando las porciones para una tabla nutricional
    if context.user_data.get("esperando_porciones_tabla"):
        context.user_data.pop("esperando_porciones_tabla")
        try:
            porciones = float(update.message.text.strip().replace(",", "."))
            assert porciones > 0
        except (ValueError, AssertionError):
            await update.message.reply_text("⚠️ Ingresa un número válido de porciones (ej: 1, 0.5, 2).")
            context.user_data["esperando_porciones_tabla"] = True
            return

        tabla = context.user_data.get("foto_tabla", {})
        kcal_porcion = float(tabla.get("kcal_por_porcion", 0))
        kcal_total   = int(kcal_porcion * porciones)
        desc         = tabla.get("producto") or "Producto (tabla nutricional)"

        context.user_data["foto_analisis"] = {
            "descripcion":    desc,
            "kcal_estimadas": kcal_total,
            "confianza":      "ALTA",
            "detalle":        f"{porciones} porción(es) × {int(kcal_porcion)} kcal",
        }

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"✅ Confirmar {kcal_total} kcal", callback_data=f"foto_ok:{kcal_total}"),
                InlineKeyboardButton("✏️ Ajustar", callback_data="foto_editar"),
            ],
            [InlineKeyboardButton("❌ Cancelar", callback_data="foto_cancelar")],
        ])

        await update.message.reply_text(
            f"🏷️ *{desc}*\n\n"
            f"📋 {porciones} porción(es) × {int(kcal_porcion)} kcal = *{kcal_total} kcal*\n\n"
            "¿Confirmas el registro?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    await update.message.reply_text("🔍 Analizando imagen... un momento.")

    foto = update.message.photo[-1]
    tg_file = await foto.get_file()
    foto_bytes = await tg_file.download_as_bytearray()

    async with httpx.AsyncClient(base_url=API_BASE, timeout=60) as c:
        r = await c.post(
            "/foto/preview",
            content=bytes(foto_bytes),
            headers={
                **_auth_headers(token),
                "Content-Type": "image/jpeg",
            },
        )

    if r.status_code != 200:
        await update.message.reply_text("❌ No pude analizar la foto. Intenta de nuevo o regístralo manualmente.")
        return

    analisis  = r.json()
    tipo_foto = analisis.get("tipo", "PLATO").upper()

    # ── Tabla nutricional detectada ───────────────────────────
    if tipo_foto == "TABLA_NUTRICIONAL":
        kcal_porcion    = int(analisis.get("kcal_por_porcion", 0))
        porcion_g       = analisis.get("porcion_g")
        porciones_env   = analisis.get("porciones_por_envase")
        producto        = analisis.get("producto") or "Producto"

        context.user_data["foto_tabla"] = analisis

        porcion_txt = f" ({porcion_g}g)" if porcion_g else ""
        env_txt     = f"\n📦 Porciones por envase: {porciones_env}" if porciones_env else ""

        await update.message.reply_text(
            f"🏷️ *Tabla nutricional detectada*\n\n"
            f"📦 Producto: *{producto}*\n"
            f"🔥 Calorías por porción{porcion_txt}: *{kcal_porcion} kcal*"
            f"{env_txt}\n\n"
            "¿*Cuántas porciones* consumiste? (ej: 1, 0.5, 2)",
            parse_mode=ParseMode.MARKDOWN,
        )
        context.user_data["esperando_porciones_tabla"] = True
        return

    # ── Plato normal ──────────────────────────────────────────
    kcal      = float(analisis.get("kcal_estimadas", 0))
    desc      = analisis.get("descripcion", "Plato")
    confianza = analisis.get("confianza", "MEDIA")
    detalle   = analisis.get("detalle", "")

    context.user_data["foto_analisis"] = analisis
    confianza_emoji = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴"}.get(confianza, "🟡")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ Confirmar {kcal:.0f} kcal", callback_data=f"foto_ok:{kcal}"),
            InlineKeyboardButton("✏️ Ajustar", callback_data="foto_editar"),
        ],
        [InlineKeyboardButton("❌ Cancelar", callback_data="foto_cancelar")],
    ])

    await update.message.reply_text(
        f"🍽️ *{desc}*\n\n"
        f"🔥 Estimado: *{kcal:.0f} kcal*\n"
        f"{confianza_emoji} Confianza: {confianza}\n"
        f"{'📋 ' + detalle if detalle else ''}\n\n"
        "¿Confirmas el registro?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


async def foto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    token   = context.user_data.get("token")
    analisis = context.user_data.get("foto_analisis", {})

    if query.data.startswith("foto_ok"):
        kcal = float(query.data.split(":")[1])
        r = await _api("post", "/foto/confirmar", token, json={
            "descripcion": analisis.get("descripcion", "Plato analizado por foto"),
            "kcal":        kcal,
            "fecha":       str(date.today()),
        })
        if r.status_code == 201:
            rs = await _api("get", "/resumen/dia", token)
            resumen_txt = _formato_resumen_inline(rs.json()) if rs.status_code == 200 else ""
            await query.edit_message_text(
                f"✅ Registrado: {kcal:.0f} kcal{resumen_txt}",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await query.edit_message_text("❌ Error al guardar. ¿Tienes perfil registrado? /perfil")

    elif query.data == "foto_cancelar":
        await query.edit_message_text("❌ Registro cancelado.")
        context.user_data.pop("foto_analisis", None)
        context.user_data.pop("foto_tabla", None)

    elif query.data == "foto_editar":
        await query.edit_message_text("✏️ Escribe las calorías que quieres registrar (número):")
        context.user_data["esperando_kcal_foto"] = True


# ── Texto libre (agente LLM) ─────────────────────────────────

async def handle_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Flujo de texto libre con LLM:
    1. Clasificar intent (COMIDA / EJERCICIO / CONSULTA / OTRO)
    2. Si COMIDA → inferir kcal → mostrar preview → pedir confirmación
    3. Si EJERCICIO → inferir kcal quemadas → mostrar preview → pedir confirmación
    4. Si CONSULTA → mostrar resumen
    5. Si OTRO → mensaje genérico
    """
    # Esperando número de porciones para tabla nutricional
    if context.user_data.get("esperando_porciones_tabla"):
        context.user_data.pop("esperando_porciones_tabla")
        try:
            porciones = float(update.message.text.strip().replace(",", "."))
            assert porciones > 0
        except (ValueError, AssertionError):
            await update.message.reply_text("⚠️ Ingresa un número válido (ej: 1, 0.5, 2).")
            context.user_data["esperando_porciones_tabla"] = True
            return

        tabla        = context.user_data.get("foto_tabla", {})
        kcal_porcion = float(tabla.get("kcal_por_porcion", 0))
        kcal_total   = int(kcal_porcion * porciones)
        desc         = tabla.get("producto") or "Producto (tabla nutricional)"

        context.user_data["foto_analisis"] = {
            "descripcion":    desc,
            "kcal_estimadas": kcal_total,
            "confianza":      "ALTA",
            "detalle":        f"{porciones} porción(es) × {int(kcal_porcion)} kcal",
        }

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"✅ Confirmar {kcal_total} kcal", callback_data=f"foto_ok:{kcal_total}"),
                InlineKeyboardButton("✏️ Ajustar", callback_data="foto_editar"),
            ],
            [InlineKeyboardButton("❌ Cancelar", callback_data="foto_cancelar")],
        ])

        await update.message.reply_text(
            f"🏷️ *{desc}*\n\n"
            f"📋 {porciones} porción(es) × {int(kcal_porcion)} kcal = *{kcal_total} kcal*\n\n"
            "¿Confirmas el registro?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    # Esperando kcal ajustada manualmente tras editar inferencia LLM
    if context.user_data.get("esperando_kcal_llm"):
        context.user_data.pop("esperando_kcal_llm")
        try:
            kcal = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            await update.message.reply_text("⚠️ Ingresa solo un número.")
            return
        token = context.user_data.get("token")
        inf   = context.user_data.get("llm_inferencia", {})
        tipo  = inf.get("tipo", "comida")
        if tipo == "ejercicio":
            r = await _api("post", "/ejercicio", token, json={
                "descripcion": inf.get("descripcion", "Ejercicio"),
                "kcal_quemadas": kcal,
                "fecha": str(date.today()),
            })
        else:
            r = await _api("post", "/calorias", token, json={
                "descripcion": inf.get("descripcion", "Comida"),
                "kcal": kcal,
                "fecha": str(date.today()),
            })
        if r.status_code == 201:
            await update.message.reply_text(f"✅ Registrado: {kcal:.0f} kcal")
        else:
            await update.message.reply_text("❌ Error al guardar.")
        return

    # Esperando kcal ajustada manualmente tras editar foto
    if context.user_data.get("esperando_kcal_foto"):
        context.user_data.pop("esperando_kcal_foto")
        try:
            kcal = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            await update.message.reply_text("⚠️ Ingresa solo un número.")
            return
        token = context.user_data.get("token")
        analisis = context.user_data.get("foto_analisis", {})
        r = await _api("post", "/foto/confirmar", token, json={
            "descripcion": analisis.get("descripcion", "Plato analizado por foto"),
            "kcal": kcal,
            "fecha": str(date.today()),
        })
        if r.status_code == 201:
            await update.message.reply_text(f"✅ Registrado: {kcal:.0f} kcal")
        else:
            await update.message.reply_text("❌ Error al guardar.")
        return

    token = await _require_token(update, context)
    if not token:
        return

    texto = update.message.text.strip()
    # Cancelar fuera de conversación activa
    if _es_cancelar(texto):
        await update.message.reply_text("✅ No hay ninguna operación activa en este momento.")
        return
    texto_lower = texto.lower()

    # Historial
    if re.search(r"\b(historial|últimos|ultimos|registros|listar|mis comidas|mis ejercicios|qué comí|que comi|lo que llevo)\b", texto_lower):
        await cmd_historial(update, context)
        return

    # Borrar registro por número — "borrar 2", "eliminar 3", "borra el 1", "2"
    match_borrar = re.match(r"^(borrar?|eliminar?|borra|elimina|quita)[\s]+(\d+)$", texto_lower)
    if not match_borrar:
        match_borrar = re.match(r"^(\d+)$", texto_lower)  # solo el número
    if match_borrar:
        numero = int(match_borrar.group(2) if match_borrar.lastindex == 2 else match_borrar.group(1))
        historial = context.user_data.get("historial", [])
        if historial:
            context.args = [str(numero)]
            await cmd_borrar(update, context)
        else:
            await update.message.reply_text(
                "Primero usa /historial para ver tus registros, luego escribe el número a borrar."
            )
        return

    # Resumen / balance
    if re.search(r"\b(resumen|balance|cómo voy|como voy|cuánto llevo|cuanto llevo|me quedan|disponibles)\b", texto_lower):
        await cmd_resumen(update, context)
        return

    # Perfil
    if re.search(r"\b(perfil|bmr|objetivo|meta calórica|meta calorica|peso saludable)\b", texto_lower):
        await cmd_perfil(update, context)
        return

    await update.message.reply_text("🤔 Analizando...")

    # ── Paso 1: clasificar intent ──────────────────────────────
    try:
        intent_result = await llm.clasificar_intent(texto)
    except Exception as e:
        log.error("Error clasificando intent: %s", e)
        await update.message.reply_text("❌ Error al procesar. Intenta de nuevo.")
        return

    # ── Paso 2A: COMIDA ───────────────────────────────────────
    if intent_result.intent == Intent.COMIDA:
        try:
            inf = await llm.inferir_comida(texto)
        except Exception as e:
            log.error("Error infiriendo comida: %s", e)
            await update.message.reply_text("❌ No pude estimar las calorías. Usa /calorias para registrar manualmente.")
            return

        confianza_emoji = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴"}.get(inf.confianza, "🟡")
        context.user_data["llm_inferencia"] = {
            "tipo": "comida",
            "descripcion": inf.descripcion,
            "kcal": inf.kcal,
        }

        detalle_txt = f"\n📋 _{inf.detalle}_" if inf.detalle else ""
        nota_txt    = f"\n💬 _{inf.nota}_" if inf.nota else ""

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"✅ Confirmar {inf.kcal} kcal", callback_data=f"llm_ok:{inf.kcal}"),
                InlineKeyboardButton("✏️ Ajustar", callback_data="llm_editar"),
            ],
            [InlineKeyboardButton("❌ Cancelar", callback_data="llm_cancelar")],
        ])

        await update.message.reply_text(
            f"🍽️ *{inf.descripcion}*\n"
            f"🔥 Estimado: *{inf.kcal} kcal*\n"
            f"{confianza_emoji} Confianza: {inf.confianza}"
            f"{detalle_txt}{nota_txt}\n\n"
            "¿Confirmas el registro?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )

    # ── Paso 2B: EJERCICIO ────────────────────────────────────
    elif intent_result.intent == Intent.EJERCICIO:
        # Obtener peso y edad del perfil para el cálculo
        peso_kg, edad = 70.0, 30
        r_perfil = await _api("get", "/perfil", token)
        if r_perfil.status_code == 200:
            p = r_perfil.json()
            peso_kg = float(p.get("peso_kg", 70))
            edad    = int(p.get("edad", 30))

        try:
            inf = await llm.inferir_ejercicio(texto, peso_kg=peso_kg, edad=edad)
        except Exception as e:
            log.error("Error infiriendo ejercicio: %s", e)
            await update.message.reply_text("❌ No pude estimar las calorías quemadas. Usa /ejercicio para registrar manualmente.")
            return

        confianza_emoji = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴"}.get(inf.confianza, "🟡")
        context.user_data["llm_inferencia"] = {
            "tipo": "ejercicio",
            "descripcion": inf.descripcion,
            "kcal_quemadas": inf.kcal_quemadas,
            "duracion_min": inf.duracion_min,
            "distancia_km": inf.distancia_km,
        }

        dur_txt  = f" · {inf.duracion_min} min" if inf.duracion_min else ""
        dist_txt = f" · {inf.distancia_km:.1f} km" if inf.distancia_km else ""
        nota_txt = f"\n💬 _{inf.nota}_" if inf.nota else ""

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"✅ Confirmar {inf.kcal_quemadas} kcal", callback_data=f"llm_ok:{inf.kcal_quemadas}"),
                InlineKeyboardButton("✏️ Ajustar", callback_data="llm_editar"),
            ],
            [InlineKeyboardButton("❌ Cancelar", callback_data="llm_cancelar")],
        ])

        await update.message.reply_text(
            f"🏃 *{inf.descripcion}*{dur_txt}{dist_txt}\n"
            f"🔥 Estimado: *{inf.kcal_quemadas} kcal quemadas*\n"
            f"{confianza_emoji} Confianza: {inf.confianza}"
            f"{nota_txt}\n\n"
            "¿Confirmas el registro?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )

    # ── CONSULTA ──────────────────────────────────────────────
    elif intent_result.intent == Intent.CONSULTA:
        await cmd_resumen(update, context)

    # ── OTRO ──────────────────────────────────────────────────
    else:
        await update.message.reply_text(
            "🤔 No entendí bien. Puedes:\n"
            "• Escribir lo que comiste: _\"tomé medio pocillo de yogur\"_\n"
            "• Escribir tu ejercicio: _\"corrí 5km\"_ o _\"pesas 40 min\"_\n"
            "• Ver tu balance: /resumen\n"
            "• Enviarme una foto de tu plato 📸\n"
            "• Ver tu perfil: /perfil",
            parse_mode=ParseMode.MARKDOWN,
        )


async def llm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja confirmación/edición/cancelación de inferencias LLM."""
    query = update.callback_query
    await query.answer()
    token = context.user_data.get("token")
    inf   = context.user_data.get("llm_inferencia", {})

    if query.data.startswith("llm_ok"):
        kcal = float(query.data.split(":")[1])
        tipo = inf.get("tipo", "comida")

        if tipo == "ejercicio":
            payload = {
                "descripcion":  inf.get("descripcion", "Ejercicio"),
                "kcal_quemadas": kcal,
                "fecha":        str(date.today()),
            }
            if inf.get("duracion_min"):
                payload["duracion_min"] = inf["duracion_min"]
            r = await _api("post", "/ejercicio", token, json=payload)
        else:
            payload = {
                "descripcion": inf.get("descripcion", "Comida"),
                "kcal":        kcal,
                "fecha":       str(date.today()),
            }
            r = await _api("post", "/calorias", token, json=payload)

        if r.status_code == 201:
            rs = await _api("get", "/resumen/dia", token)
            resumen_txt = _formato_resumen_inline(rs.json()) if rs.status_code == 200 else ""
            await query.edit_message_text(
                f"✅ Registrado: *{inf.get('descripcion')}* — {kcal:.0f} kcal{resumen_txt}",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await query.edit_message_text("❌ Error al guardar. ¿Tienes perfil registrado? /perfil")

    elif query.data == "llm_cancelar":
        await query.edit_message_text("❌ Registro cancelado.")
        context.user_data.pop("llm_inferencia", None)

    elif query.data == "llm_editar":
        tipo = inf.get("tipo", "comida")
        if tipo == "ejercicio":
            await query.edit_message_text("✏️ Escribe las calorías quemadas que quieres registrar:")
        else:
            await query.edit_message_text("✏️ Escribe las calorías que quieres registrar:")
        context.user_data["esperando_kcal_llm"] = True


# ── Cancelar conversación ────────────────────────────────────

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operación cancelada.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ── Entrypoint ───────────────────────────────────────────────

async def post_init(app: Application) -> None:
    """Registra los comandos en Telegram para que aparezcan en el menú."""
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("start",       "Bienvenida y lista de comandos"),
        BotCommand("vincular",    "Vincular cuenta KALO con email"),
        BotCommand("perfil",      "Ver o actualizar tu BMR"),
        BotCommand("calorias",    "Registrar una comida"),
        BotCommand("ejercicio",   "Registrar actividad física"),
        BotCommand("resumen",     "Balance calórico de hoy"),
        BotCommand("historial",   "Listar últimos 10 registros de comidas y entrenamientos"),
        BotCommand("borrar",      "Eliminar un registro"),
        BotCommand("desvincular", "Desvincular Telegram"),
        BotCommand("cancelar",    "Cancelar operación en curso"),
    ])


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    conv_vincular = ConversationHandler(
        entry_points=[CommandHandler("vincular", cmd_vincular)],
        states={
            VINCULAR_EMAIL:  [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, vincular_email)],
            VINCULAR_CODIGO: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, vincular_codigo)],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            MessageHandler(CANCELAR_FILTER, cancelar),
        ],
    )

    # Conversación: registrar perfil
    conv_perfil = ConversationHandler(
        entry_points=[
            CommandHandler("perfil", cmd_perfil),
            CommandHandler("perfil_actualizar", cmd_perfil_actualizar),
        ],
        states={
            PERFIL_ESTATURA:   [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, perfil_estatura)],
            PERFIL_PESO:       [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, perfil_peso)],
            PERFIL_SEXO:       [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, perfil_sexo)],
            PERFIL_NACIMIENTO: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, perfil_nacimiento)],
            PERFIL_FACTOR:     [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, perfil_factor)],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            MessageHandler(CANCELAR_FILTER, cancelar),
        ],
    )

    # Conversación: registrar caloría
    conv_caloria = ConversationHandler(
        entry_points=[CommandHandler("calorias", cmd_calorias)],
        states={
            CALORIA_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, caloria_desc)],
            CALORIA_KCAL:  [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, caloria_kcal)],
            CALORIA_FECHA: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, caloria_fecha)],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            MessageHandler(CANCELAR_FILTER, cancelar),
        ],
    )

    # Conversación: registrar ejercicio
    conv_ejercicio = ConversationHandler(
        entry_points=[CommandHandler("ejercicio", cmd_ejercicio)],
        states={
            EJERCICIO_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, ejercicio_desc)],
            EJERCICIO_DUR:   [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, ejercicio_dur)],
            EJERCICIO_KCAL:  [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, ejercicio_kcal)],
            EJERCICIO_FECHA: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~CANCELAR_FILTER, ejercicio_fecha)],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            MessageHandler(CANCELAR_FILTER, cancelar),
        ],
    )

    app.add_handler(conv_vincular)
    app.add_handler(conv_perfil)
    app.add_handler(conv_caloria)
    app.add_handler(conv_ejercicio)

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("resumen",     cmd_resumen))
    app.add_handler(CommandHandler("historial",   cmd_historial))
    app.add_handler(CommandHandler("borrar",      cmd_borrar))
    app.add_handler(CommandHandler("desvincular", cmd_desvincular))

    app.add_handler(MessageHandler(filters.PHOTO, handle_foto))
    app.add_handler(CallbackQueryHandler(foto_callback, pattern="^foto_"))
    app.add_handler(CallbackQueryHandler(llm_callback, pattern="^llm_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_texto))

    log.info("KALO Bot arrancando con long-polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()