import os


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    IDENTITY_ENCRYPTION_KEY = os.getenv("IDENTITY_ENCRYPTION_KEY")

    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")

    if not SQLALCHEMY_DATABASE_URI:
        raise RuntimeError(
            "DATABASE_URL is required. PesaPilot only supports PostgreSQL."
        )

    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    elif SQLALCHEMY_DATABASE_URI.startswith("postgresql://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )

    SQLALCHEMY_ENGINE_OPTIONS = {}

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    JWT_ACCESS_TOKEN_EXPIRES = 900
    JWT_REFRESH_TOKEN_EXPIRES = 60 * 60 * 24 * 30

    CORS_ORIGINS = os.getenv(
        "CORS_ORIGINS", "http://localhost:5173"
    ).split(",")

    # External AI is opt-in and disabled by default.
    AI_EXTERNAL_ENABLED = (
        os.getenv("AI_EXTERNAL_ENABLED", "false").lower() == "true"
    )
    AI_PROVIDER = os.getenv("AI_PROVIDER", "groq").lower()
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
    AI_REQUEST_TIMEOUT = 30

class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


def get_config():
    environment = os.getenv("FLASK_ENV", "development").lower()

    if environment == "production":
        return ProductionConfig

    return DevelopmentConfig
