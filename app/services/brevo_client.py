import httpx

from app.core.config import settings

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


async def enviar_codigo_otp(email: str, nombre: str, codigo: str) -> bool:
    """Envía el código OTP al email del usuario usando Brevo transaccional."""
    payload = {
        "sender": {
            "name": settings.brevo_from_name,
            "email": settings.brevo_from_email,
        },
        "to": [{"email": email, "name": nombre or email}],
        "subject": f"Tu código de acceso KALO: {codigo}",
        "htmlContent": f"""
        <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px;">
          <h2 style="color:#2D6A4F">KALO 🥗</h2>
          <p>Tu código de verificación es:</p>
          <div style="font-size:36px;font-weight:bold;letter-spacing:8px;
                      text-align:center;padding:24px;background:#F0F4F2;
                      border-radius:12px;margin:24px 0;">
            {codigo}
          </div>
          <p style="color:#666;font-size:13px;">
            Válido por 10 minutos. Si no solicitaste este código, ignora este mensaje.
          </p>
        </div>
        """,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            BREVO_API_URL,
            json=payload,
            headers={
                "api-key": settings.brevo_api_key,
                "Content-Type": "application/json",
            },
        )
        return resp.status_code in (200, 201)
