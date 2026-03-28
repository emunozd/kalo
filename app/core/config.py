from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Base de datos
    database_url: str

    # Seguridad
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 30  # 30 días

    # Brevo
    brevo_api_key: str
    brevo_from_email: str
    brevo_from_name: str = "KALO"

    # LLM Vision
    llm_vision_url: str
    llm_vision_api_key: str = ""
    llm_vision_model: str = "gpt-4o"

    # Telegram
    telegram_token: str

    class Config:
        env_file = ".env"


settings = Settings()
