"""
FastAPI application for the isolated second bot dashboard.

Run:
    uvicorn app.api.bot2_main:app --reload --port 8001
"""

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.bot2_settings import router as settings_router
from app.api.routes.bot2_status import router as status_router
from app.api.routes.bot2_worker import router as worker_router

app = FastAPI(title="BTC 5-Min Bot 2 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_API_TOKEN = os.getenv("BOT2_API_TOKEN") or os.getenv("API_TOKEN")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if _API_TOKEN and request.url.path != "/health":
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {_API_TOKEN}":
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


app.include_router(status_router)
app.include_router(worker_router)
app.include_router(settings_router)
