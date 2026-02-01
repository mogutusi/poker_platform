from pydantic import BaseModel

from app.config import settings

class RefreshTokenRequest(BaseModel):
    user_name: str

class RefreshTokenResponse(BaseModel):
    access_token: str
    access_token_type: str = "Bearer"
    access_token_expiry: int = settings.ACCESS_TOKEN_EXPIRY
    refresh_token_expiry: int = settings.REFRESH_TOKEN_EXPIRY