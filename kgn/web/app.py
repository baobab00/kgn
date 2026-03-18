"""KGN Web — FastAPI application factory.

Creates a FastAPI instance with project context, mounts static files
and templates, and includes API routes. All business logic is delegated
to the existing service layer (R3/R12).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kgn import __version__
from kgn.web.routes.agents import router as agents_router
from kgn.web.routes.edges import router as edges_router
from kgn.web.routes.health import router as health_router
from kgn.web.routes.nodes import router as nodes_router
from kgn.web.routes.search import router as search_router
from kgn.web.routes.stats import router as stats_router
from kgn.web.routes.subgraph import router as subgraph_router
from kgn.web.routes.tasks import router as tasks_router

_WEB_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _WEB_DIR / "static"
_TEMPLATES_DIR = _WEB_DIR / "templates"


def create_app(project_name: str, project_id: uuid.UUID) -> FastAPI:
    """Create a FastAPI application for the given project.

    Args:
        project_name: Human-readable project name.
        project_id: UUID of the project in the database.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="KGN Web",
        version=__version__,
        description="Knowledge Graph Node — Visualization Dashboard",
    )

    # Store project context in app state
    app.state.project_name = project_name
    app.state.project_id = project_id

    # CORS — local use only (R14: read-only Phase 9)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # Optional API key middleware (I-08 security hardening)
    api_key = os.environ.get("KGN_API_KEY", "")
    if api_key:

        @app.middleware("http")
        async def _api_key_guard(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
            path = request.url.path
            if path.startswith("/api/v1/") and path != "/api/v1/health":
                provided = request.headers.get("X-API-Key", "")
                if provided != api_key:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or missing API key"},
                    )
            return await call_next(request)

    # API routes
    app.include_router(nodes_router, prefix="/api/v1")
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(subgraph_router, prefix="/api/v1")
    app.include_router(edges_router, prefix="/api/v1")
    app.include_router(tasks_router, prefix="/api/v1")
    app.include_router(stats_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(agents_router, prefix="/api/v1")

    # Static files
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Templates
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        """Render the main SPA page."""
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "project_name": project_name,
                "version": __version__,
            },
        )

    return app
