from pydantic_settings import BaseSettings
from dotenv import load_dotenv
from pathlib import Path
import os

env_path = Path(__file__).resolve().parent.parent/".env"
load_dotenv(dotenv_path=env_path)

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL").strip()
    JWT_SECRET: str = os.getenv("JWT_SECRET").strip()
    ACCESS_TOKEN_EXPIRY: int = int(os.getenv("ACCESS_TOKEN_EXPIRY"))
    REFRESH_TOKEN_EXPIRY: int = int(os.getenv("REFRESH_TOKEN_EXPIRY"))
    REFRESH_TOKEN_POOL_EXPIRY: int = int(os.getenv("REFRESH_TOKEN_POOL_EXPIRY"))

settings = Settings()