from fastapi import FastAPI
import uvicorn

from app.app_route import app_route

app = FastAPI(title="群友德州",description="别抢钱！")

app.include_router(app_route)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9127)