from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase
    supabase_url: str = ""
    supabase_key: str = ""

    # Gemini / ADK (GOOGLE_API_KEY is the canonical name ADK reads)
    gemini_api_key: str = ""
    google_api_key: str = ""

    # Qdrant
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    # Twitter (twikit cookies)
    twitter_username: str = ""
    twitter_auth_token: str = ""
    twitter_ct0: str = ""

    # Gmail
    gmail_user: str = ""
    gmail_app_password: str = ""

    # App
    environment: str = "dev"
    admin_secret: str = "nammacity-admin-2026"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
