import sys
from pathlib import Path

# Garante que a raiz do projeto está no PYTHONPATH
root_path = str(Path(__file__).resolve().parents[2])
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, data, health, intelligence
from app.core.config import get_settings
from app.admin_bootstrap import bootstrap_admin
from app.db.session import Base, engine

settings = get_settings()

app = FastAPI(
    title="MarBet API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"https://[a-zA-Z0-9-]+\.onrender\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(data.router, prefix=settings.api_v1_prefix)
app.include_router(intelligence.router, prefix=settings.api_v1_prefix)

@app.on_event("startup")
def validate_database_configuration():
    if settings.app_env == "production" and settings.database_url.startswith("sqlite"):
        raise RuntimeError("SQLite is not permitted in production")
    if settings.app_env == "production" and (
        settings.jwt_secret_key == "change-this-secret-in-production"
        or len(settings.jwt_secret_key) < 32
    ):
        raise RuntimeError("JWT_SECRET_KEY must be a unique secret with at least 32 characters in production")
    bootstrap_admin()


@app.on_event("startup")
async def start_data_sync():
    from app.background_sync import start_background_sync
    start_background_sync()
