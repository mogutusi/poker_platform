from pydantic_settings import BaseSettings
from dotenv import load_dotenv
from pathlib import Path
import os

env_path = Path(__file__).resolve().parent.parent/".env"
load_dotenv(dotenv_path=env_path)
    
class GameConfig(BaseSettings):
    MAX_SEATS: int = int(os.getenv("MAX_SEATS").strip())
    SMALL_BLIND: int = int(os.getenv("SMALL_BLIND").strip())
    BIG_BLIND: int = int(os.getenv("BIG_BLIND").strip())
    ANTE: int = int(os.getenv("ANTE").strip())
    BLIND_INTERVAL: int = int(os.getenv("BLIND_INTERVAL").strip())
    BLIND_INTERVAL_TYPE: str = os.getenv("BLIND_INTERVAL_TYPE").strip()
    BLIND_INTERVAL_VALUE: int = int(os.getenv("BLIND_INTERVAL_VALUE").strip())

gameconfig = GameConfig()