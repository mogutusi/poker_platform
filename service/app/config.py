from pydantic_settings import BaseSettings
from dotenv import load_dotenv
from pathlib import Path
import os

env_path = Path(__file__).resolve().parent.parent/".env"
load_dotenv(dotenv_path=env_path)

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL").strip()


    

settings = Settings()