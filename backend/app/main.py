"""The VivaAI API. Run from backend/: uvicorn app.main:app --reload"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import close_pool
from app.modules.auth.router import router as auth_router
from app.modules.ingestion.router import router as ingestion_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    close_pool()


app = FastAPI(title="VivaAI", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(ingestion_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
