"""
config.py — Application configuration with hard validation
Server refuses to start if required secrets are missing.
"""
import sys
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── REQUIRED ─────────────────────────────────────────────────
    api_key: str  # No default: raises if missing

    # ── OLLAMA ───────────────────────────────────────────────────
    ollama_host: str = "http://localhost:11434"
    # Must be a model that supports tool/function calling.
    # Recommended: llama3.1, mistral-nemo, qwen2.5, command-r
    ollama_model: str = "llama3.1"

    # ── LOCATION ─────────────────────────────────────────────────
    latitude: float = 19.0760
    longitude: float = 72.8777
    location: str = "Mumbai"
    timezone: str = "Asia/Kolkata"

    # ── WEATHER (optional) ───────────────────────────────────────
    openweather_api_key: str = ""

    # ── DATABASE ─────────────────────────────────────────────────
    database_url: str = "solar_agent.db"

    # ── API ───────────────────────────────────────────────────────
    rate_limit_telemetry: str = "30/minute"
    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:8080"]

    @field_validator("api_key")
    @classmethod
    def api_key_must_not_be_placeholder(cls, v: str) -> str:
        if not v or v in ("your-api-key", "change-me-before-deploying", ""):
            raise ValueError(
                "API_KEY is not set or still uses a placeholder value. "
                "Set a real secret in your .env file or environment."
            )
        return v

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def _load_settings() -> Settings:
    try:
        return Settings()
    except Exception as exc:
        print(f"\n[FATAL] Configuration error:\n  {exc}", file=sys.stderr)
        print("  Copy .env.example → .env and fill in required values.\n", file=sys.stderr)
        sys.exit(1)


settings = _load_settings()
