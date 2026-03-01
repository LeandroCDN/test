"""
FastAPI application for the BTC-Five-Minutes bot dashboard.

Run:
    uvicorn app.api.main:app --reload --port 8000
"""

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.status import router as status_router
from app.api.routes.worker import router as worker_router
from app.api.routes.settings import router as settings_router

app = FastAPI(title="BTC 5-Min Bot API", version="0.1.0")

# CORS — open for local dev; tighten allow_origins for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional bearer-token auth (set API_TOKEN env var to enable)
_API_TOKEN = os.getenv("API_TOKEN")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if _API_TOKEN:
        if request.url.path != "/health":
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {_API_TOKEN}":
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


app.include_router(status_router)
app.include_router(worker_router)
app.include_router(settings_router)
