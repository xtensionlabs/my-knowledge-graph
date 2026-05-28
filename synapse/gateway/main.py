"""FastAPI gateway — the central nervous system of Synapse.

Boots the app, mounts routers, configures middleware. In Milestone 0 it
exposes `/health` and the `/ingest/*` capture endpoints. Agents, integrations,
and graph endpoints are mounted in later milestones.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from synapse import __version__
from synapse.config import DASHBOARD_CORS_ORIGINS, get_settings
from synapse.gateway.middleware import RequestLogMiddleware
from synapse.gateway.routes import agents, auth, context, dashboard, graph, health, ingest, reviews
from synapse.graph.db import init_db
from synapse.logging_setup import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize logging + DB on startup."""
    configure_logging(component="gateway")
    settings = get_settings()
    # Ensure vault internal dir + DB tables exist. `synapse init` is the
    # canonical setup path, but starting the gateway against a partially
    # configured vault should not crash — it should self-heal.
    settings.vault_internal_dir.mkdir(parents=True, exist_ok=True)
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    settings.archive_dir.mkdir(parents=True, exist_ok=True)
    settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    logger.info(
        "gateway up (v{ver}) on {host}:{port} — vault={vault}",
        ver=__version__,
        host=settings.synapse_gateway_host,
        port=settings.synapse_gateway_port,
        vault=settings.synapse_vault_path,
    )
    yield
    logger.info("gateway shutting down")


def create_app() -> FastAPI:
    """Construct the FastAPI application.

    Returns:
        A configured FastAPI instance ready to be served by uvicorn.
    """
    app = FastAPI(
        title="Synapse Gateway",
        version=__version__,
        description="Personal cognitive operating system — gateway API.",
        lifespan=lifespan,
    )
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DASHBOARD_CORS_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["x-synapse-api-key", "content-type"],
    )
    app.include_router(health.router)
    app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
    app.include_router(graph.router)
    app.include_router(agents.router)
    app.include_router(reviews.router)
    app.include_router(context.router)
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
    return app


app = create_app()
