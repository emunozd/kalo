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

import io
import os
import logging
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

from bot.agent import Intent, clasificar

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

API_BASE = os.environ["API_BASE_URL"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]

# ── Estados de conversación ──────────────────────────────────
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
        await update.message.reply_text(
            f"📊 *Tu perfil KALO*\n\n"
            f"📏 Estatura: {p['estatura_cm']} cm\n"
            f"⚖️ Peso: {p['peso_kg']} kg\n"
            f"🔥 BMR: *{float(p['bmr']):.0f} kcal/día*\n"
            f"🎯 Objetivo: *{float(p['objetivo_kcal']):.0f} kcal/día*\n\n"
            "¿Deseas actualizar tu perfil? /perfil\\_actualizar",
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
    keyboard = [["Masculino", "Femenino", "Otro"]]
    await update.message.reply_text(
        "👤 ¿Cuál es tu sexo biológico? (para el cálculo de BMR)",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return PERFIL_SEXO


async def perfil_sexo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sexo_map = {"masculino": "M", "femenino": "F", "otro": "OTRO"}
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
    disp = float(s.get("kcal_disponibles") or 0)
    await update.message.reply_text(
        f"📊 *Balance de hoy — {s['fecha']}*\n\n"
        f"🎯 Objetivo: {float(s['kcal_objetivo']):.0f} kcal\n"
        f"🍽️ Consumidas: {float(s['kcal_consumidas']):.0f} kcal\n"
        f"🏃 Quemadas (ejercicio): {float(s['kcal_quemadas']):.0f} kcal\n"
        f"✨ Disponibles: *{disp:.0f} kcal*\n\n"
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
    context.user_data["cal_desc"] = update.message.text.strip()
    await update.message.reply_text("🔢 ¿Cuántas calorías tenía aproximadamente? (número entero, ej: 350)")
    return CALORIA_KCAL


async def caloria_kcal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        kcal = float(update.message.text.strip().replace(",", "."))
        assert kcal >= 0
    except (ValueError, AssertionError):
        await update.message.reply_text("⚠️ Ingresa un número válido de calorías.")
        return CALORIA_KCAL

    context.user_data["cal_kcal"] = kcal
    keyboard = [["Hoy", "Ayer"], ["Cancelar"]]
    await update.message.reply_text(
        "📅 ¿En qué fecha fue esta comida?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return CALORIA_FECHA


async def caloria_fecha(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        resumen_txt = ""
        if rs.status_code == 200:
            s = rs.json()
            resumen_txt = (
                f"\n\n📊 *Balance del día:* {float(s.get('kcal_disponibles') or 0):.0f} kcal disponibles\n"
                f"{s.get('mensaje_orientacion', '')}"
            )

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
    context.user_data["eje_desc"] = update.message.text.strip()
    await update.message.reply_text("⏱️ ¿Cuántos minutos duró? (escribe 0 si no lo sabes)")
    return EJERCICIO_DUR


async def ejercicio_dur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        dur = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("⚠️ Ingresa un número.")
        return EJERCICIO_DUR

    context.user_data["eje_dur"] = dur if dur > 0 else None
    await update.message.reply_text("🔥 ¿Cuántas calorías quemaste aproximadamente?")
    return EJERCICIO_KCAL


async def ejercicio_kcal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        kcal = float(update.message.text.strip().replace(",", "."))
        assert kcal >= 0
    except (ValueError, AssertionError):
        await update.message.reply_text("⚠️ Ingresa un número válido.")
        return EJERCICIO_KCAL

    context.user_data["eje_kcal"] = kcal
    keyboard = [["Hoy", "Ayer"], ["Cancelar"]]
    await update.message.reply_text(
        "📅 ¿En qué fecha hiciste el ejercicio?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return EJERCICIO_FECHA


async def ejercicio_fecha(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        resumen_txt = ""
        if rs.status_code == 200:
            s = rs.json()
            resumen_txt = (
                f"\n\n📊 *Balance del día:* {float(s.get('kcal_disponibles') or 0):.0f} kcal disponibles"
            )

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

    # Obtener resumen de la semana
    rs = await _api("get", f"/resumen/semana?desde={desde}&hasta={hasta}", token)
    if rs.status_code != 200 or not rs.json():
        await update.message.reply_text("📭 Sin registros esta semana.")
        return

    lineas = ["📅 *Últimos 7 días:*\n"]
    for dia in rs.json():
        consumidas = float(dia["kcal_consumidas"])
        quemadas   = float(dia["kcal_quemadas"])
        objetivo   = float(dia["kcal_objetivo"])
        bal        = objetivo - consumidas + quemadas
        emoji      = "✅" if bal >= 0 else "⚠️"
        lineas.append(
            f"{emoji} *{dia['fecha']}* — 🍽️ {consumidas:.0f} | 🏃 {quemadas:.0f} | Disp: {bal:.0f} kcal"
        )

    context.user_data["ultimo_historial_tipo"] = "resumen_semana"
    await update.message.reply_text("\n".join(lineas), parse_mode=ParseMode.MARKDOWN)


# ── /borrar ──────────────────────────────────────────────────

async def cmd_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: /borrar <id_registro>\n"
            "Primero usa /historial para ver los IDs de tus registros."
        )
        return

    registro_id = args[0]
    # Intentar primero en calorías, luego en ejercicio
    r = await _api("delete", f"/calorias/{registro_id}", token)
    if r.status_code == 404:
        r = await _api("delete", f"/ejercicio/{registro_id}", token)

    if r.status_code == 204:
        await update.message.reply_text("✅ Registro eliminado.")
    else:
        await update.message.reply_text("❌ No encontré ese registro.")


# ── Foto de comida ───────────────────────────────────────────

async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = await _require_token(update, context)
    if not token:
        return

    await update.message.reply_text("🔍 Analizando tu plato... un momento.")

    foto = update.message.photo[-1]  # Mayor resolución disponible
    tg_file = await foto.get_file()
    foto_bytes = await tg_file.download_as_bytearray()

    async with httpx.AsyncClient(base_url=API_BASE, timeout=60) as c:
        r = await c.post(
            "/foto/preview",
            files={"imagen": ("foto.jpg", bytes(foto_bytes), "image/jpeg")},
            headers=_auth_headers(token),
        )

    if r.status_code != 200:
        await update.message.reply_text("❌ No pude analizar la foto. Intenta de nuevo o regístralo manualmente.")
        return

    analisis = r.json()
    kcal = float(analisis["kcal_estimadas"])
    desc = analisis["descripcion"]
    confianza = analisis["confianza"]
    detalle = analisis.get("detalle", "")

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
    token = context.user_data.get("token")
    analisis = context.user_data.get("foto_analisis", {})

    if query.data.startswith("foto_ok"):
        kcal = float(query.data.split(":")[1])
        payload = {
            "descripcion": analisis.get("descripcion", "Plato analizado por foto"),
            "kcal": kcal,
            "fecha": str(date.today()),
        }
        r = await _api("post", "/foto/confirmar", token, json=payload)

        if r.status_code == 201:
            rs = await _api("get", f"/resumen/dia", token)
            resumen_txt = ""
            if rs.status_code == 200:
                s = rs.json()
                resumen_txt = f"\n\n📊 *Balance:* {float(s.get('kcal_disponibles') or 0):.0f} kcal disponibles\n{s.get('mensaje_orientacion', '')}"

            await query.edit_message_text(
                f"✅ Registrado: {kcal:.0f} kcal{resumen_txt}",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await query.edit_message_text("❌ Error al guardar. ¿Tienes perfil registrado? /perfil")

    elif query.data == "foto_cancelar":
        await query.edit_message_text("❌ Registro cancelado.")

    elif query.data == "foto_editar":
        await query.edit_message_text(
            "✏️ Escribe las calorías que quieres registrar (número):"
        )
        context.user_data["esperando_kcal_foto"] = True


# ── Texto libre (agente) ─────────────────────────────────────

async def handle_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Si estamos esperando kcal ajustada de foto
    if context.user_data.get("esperando_kcal_foto"):
        context.user_data.pop("esperando_kcal_foto")
        try:
            kcal = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            await update.message.reply_text("⚠️ Ingresa solo un número.")
            return

        token = context.user_data.get("token")
        analisis = context.user_data.get("foto_analisis", {})
        payload = {
            "descripcion": analisis.get("descripcion", "Plato analizado por foto"),
            "kcal": kcal,
            "fecha": str(date.today()),
        }
        r = await _api("post", "/foto/confirmar", token, json=payload)
        if r.status_code == 201:
            await update.message.reply_text(f"✅ Registrado: {kcal:.0f} kcal")
        else:
            await update.message.reply_text("❌ Error al guardar.")
        return

    token = await _require_token(update, context)
    if not token:
        return

    texto = update.message.text.strip()
    resultado = clasificar(texto)

    if resultado.intent == Intent.VER_RESUMEN:
        await cmd_resumen(update, context)

    elif resultado.intent == Intent.VER_HISTORIAL:
        await cmd_historial(update, context)

    elif resultado.intent == Intent.REGISTRAR_CALORIA:
        # Si detectó kcal en el texto, simplificar el flujo
        if "kcal" in resultado.parametros:
            payload = {
                "descripcion": texto,
                "kcal": resultado.parametros["kcal"],
                "fecha": str(date.today()),
            }
            r = await _api("post", "/calorias", token, json=payload)
            if r.status_code == 201:
                rs = await _api("get", "/resumen/dia", token)
                resumen_txt = ""
                if rs.status_code == 200:
                    s = rs.json()
                    resumen_txt = f"\n📊 Disponibles: *{float(s.get('kcal_disponibles') or 0):.0f} kcal*\n{s.get('mensaje_orientacion', '')}"
                await update.message.reply_text(
                    f"✅ Registrado: {resultado.parametros['kcal']:.0f} kcal{resumen_txt}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await update.message.reply_text("❌ Error al guardar. ¿Tienes perfil? /perfil")
        else:
            context.user_data["cal_desc"] = texto
            await update.message.reply_text(
                f"🍽️ Entendido: *{texto}*\n¿Cuántas calorías tenía?",
                parse_mode=ParseMode.MARKDOWN,
            )
            context.user_data["_next_handler"] = "caloria_kcal"

    elif resultado.intent == Intent.REGISTRAR_EJERCICIO:
        if "kcal" in resultado.parametros:
            payload = {
                "descripcion": texto,
                "kcal_quemadas": resultado.parametros["kcal"],
                "fecha": str(date.today()),
            }
            if "duracion_min" in resultado.parametros:
                payload["duracion_min"] = resultado.parametros["duracion_min"]

            r = await _api("post", "/ejercicio", token, json=payload)
            if r.status_code == 201:
                rs = await _api("get", "/resumen/dia", token)
                resumen_txt = ""
                if rs.status_code == 200:
                    s = rs.json()
                    resumen_txt = f"\n📊 Disponibles: *{float(s.get('kcal_disponibles') or 0):.0f} kcal*"
                await update.message.reply_text(
                    f"✅ Ejercicio registrado: {resultado.parametros['kcal']:.0f} kcal quemadas{resumen_txt}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await update.message.reply_text("❌ Error. ¿Tienes perfil? /perfil")
        else:
            context.user_data["eje_desc"] = texto
            await update.message.reply_text(
                f"🏃 Entendido: *{texto}*\n¿Cuántas calorías quemaste?",
                parse_mode=ParseMode.MARKDOWN,
            )
            context.user_data["_next_handler"] = "ejercicio_kcal"

    elif resultado.intent == Intent.VER_PERFIL:
        await cmd_perfil(update, context)

    elif resultado.intent == Intent.AYUDA:
        await cmd_start(update, context)

    else:
        await update.message.reply_text(
            "🤔 No entendí bien. Puedes:\n"
            "• Registrar comida: /calorias\n"
            "• Registrar ejercicio: /ejercicio\n"
            "• Ver tu balance: /resumen\n"
            "• Enviarme una foto de tu plato 📸\n"
            "• Ver tu perfil: /perfil"
        )


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
        BotCommand("historial",   "Últimos 7 días"),
        BotCommand("borrar",      "Eliminar un registro"),
        BotCommand("desvincular", "Desvincular Telegram"),
        BotCommand("cancelar",    "Cancelar operación en curso"),
    ])


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    conv_vincular = ConversationHandler(
        entry_points=[CommandHandler("vincular", cmd_vincular)],
        states={
            VINCULAR_EMAIL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, vincular_email)],
            VINCULAR_CODIGO: [MessageHandler(filters.TEXT & ~filters.COMMAND, vincular_codigo)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )

    # Conversación: registrar perfil
    conv_perfil = ConversationHandler(
        entry_points=[
            CommandHandler("perfil", cmd_perfil),
            CommandHandler("perfil_actualizar", cmd_perfil_actualizar),
        ],
        states={
            PERFIL_ESTATURA:   [MessageHandler(filters.TEXT & ~filters.COMMAND, perfil_estatura)],
            PERFIL_PESO:       [MessageHandler(filters.TEXT & ~filters.COMMAND, perfil_peso)],
            PERFIL_SEXO:       [MessageHandler(filters.TEXT & ~filters.COMMAND, perfil_sexo)],
            PERFIL_NACIMIENTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, perfil_nacimiento)],
            PERFIL_FACTOR:     [MessageHandler(filters.TEXT & ~filters.COMMAND, perfil_factor)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )

    # Conversación: registrar caloría
    conv_caloria = ConversationHandler(
        entry_points=[CommandHandler("calorias", cmd_calorias)],
        states={
            CALORIA_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, caloria_desc)],
            CALORIA_KCAL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, caloria_kcal)],
            CALORIA_FECHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, caloria_fecha)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )

    # Conversación: registrar ejercicio
    conv_ejercicio = ConversationHandler(
        entry_points=[CommandHandler("ejercicio", cmd_ejercicio)],
        states={
            EJERCICIO_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ejercicio_desc)],
            EJERCICIO_DUR:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ejercicio_dur)],
            EJERCICIO_KCAL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ejercicio_kcal)],
            EJERCICIO_FECHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ejercicio_fecha)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_texto))

    log.info("KALO Bot arrancando con long-polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()