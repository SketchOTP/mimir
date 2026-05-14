"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from mimir.config import get_settings, validate_config
from mimir.__version__ import __version__
from mimir.logging import configure_logging, log_event
from storage.database import init_db, validate_db
from api.routes import events, memory, skills, reflections, approvals, dashboard, slack, auth, system, telemetry, graph, simulation, mcp_http, oauth, connection, doctor, projects
from api.routes.telemetry import providers_router

configure_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_config(settings)
    await init_db()
    await validate_db()
    log_event("startup", component="api", status="ok",
              host=settings.host, port=settings.port, env=settings.env)
    yield
    log_event("shutdown", component="api", status="ok")


app = FastAPI(
    title="Mimir",
    description="Standalone AI Memory + Learning Core",
    version=__version__,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health", tags=["health"])
async def health():
    """Public health check — no auth required."""
    return {"status": "ok", "service": "mimir", "version": __version__}


# Register API routes (prefix all under /api)
for _router in [auth.router, events.router, memory.router, skills.router, reflections.router,
                approvals.router, dashboard.router, slack.router, system.router,
                system.metrics_router, telemetry.router, providers_router, graph.router, simulation.router]:
    app.include_router(_router, prefix="/api")

# Projects API (prefix already embedded in router)
app.include_router(projects.router)

# MCP Streamable HTTP endpoint at /mcp (no /api prefix — remote clients connect directly)
app.include_router(mcp_http.router)

# OAuth 2.1 + well-known discovery (no prefix — must be at root)
app.include_router(oauth.router)
app.include_router(connection.router)

# Doctor endpoint (unauthenticated, prefix embedded in router)
app.include_router(doctor.router)

# Serve web UI in production (web/dist must exist)
_web_dist = Path(__file__).parent.parent / "web" / "dist"
if _web_dist.exists():
    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="ui")


def run():
    import uvicorn
    uvicorn.run("api.main:app", host=settings.host, port=settings.port, reload=settings.env == "development")


if __name__ == "__main__":
    run()
