from fastapi import APIRouter, Response, Cookie, HTTPException
from typing import Annotated

from app.auth.models import RefreshTokenResponse, RefreshTokenRequest
from app.database.core import DBsession
from app.auth.services import user_refresh

auth_route = APIRouter(prefix="/auth",tags=["auth"])

@auth_route.post("/refresh",response_model=RefreshTokenResponse)
async def refresh(
    request: RefreshTokenRequest, 
    db: DBsession, response: Response, 
    refresh_token: Annotated[str|None, Cookie(alias="refresh_token")] = None
) -> RefreshTokenResponse:
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    access_token, refresh_token = await user_refresh(request.user_name, refresh_token, db)
    response.set_cookie(
        key="refresh_token", 
        value=refresh_token,
        httponly=True, 
        # secure=True, 
        same_site="lax",
        path=f"{auth_route.prefix}/refresh"
    )
    return RefreshTokenResponse(access_token=access_token)