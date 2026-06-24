from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import engine, Base
from app.routers import admin, auth, files


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (for dev convenience; in production use alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="点子王 API",
    description="Share Timeline backend with user auth and multi-tenant isolation",
    version="2.0.0",
    lifespan=lifespan,
)

# Session middleware for admin panel
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.admin_session_secret,
    same_site="lax",
)

# Static files (CSS for admin)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers
app.include_router(auth.router)
app.include_router(files.router)
app.include_router(admin.router)


@app.get("/")
async def root():
    return {
        "name": "点子王 API",
        "version": "2.0.0",
        "docs": "/docs",
    }
