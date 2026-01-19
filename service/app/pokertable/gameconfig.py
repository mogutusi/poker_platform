from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / "poker.env"

class GameConfig(BaseSettings):
    MAX_SEATS: int
    DEFAULT_SMALL_BLIND: int
    MIN_SMALL_BLIND: int
    MAX_SMALL_BLIND: int
    MIN_BUY_IN: int
    MAX_BUY_IN: int

    model_config = SettingsConfigDict(
        env_file=ENV_PATH, 
        env_file_encoding='utf-8',
        case_sensitive=True,
        extra='ignore' 
    )

gameconfig = GameConfig()