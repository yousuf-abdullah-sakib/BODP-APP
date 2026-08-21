from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.limiter import limiter
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.routers import (
    admin_about_team,
    admin_analytics,
    admin_audit,
    admin_backups,
    admin_blog,
    admin_boundary,
    admin_bulk_import,
    admin_categories,
    admin_cms,
    admin_contact,
    admin_dataset_schema,
    admin_datasets,
    admin_media,
    admin_overview,
    admin_qc,
    admin_reports,
    admin_requests,
    admin_roles,
    admin_settings,
    admin_support,
    admin_team,
    admin_users,
    auth,
    catalog,
    content,
    me,
    requests,
    visualize,
)

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        debug=settings.DEBUG,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url="/api/redoc" if not settings.is_production else None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    register_exception_handlers(app)

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_datasets.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_bulk_import.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_dataset_schema.router, prefix=settings.API_V1_PREFIX)
    app.include_router(catalog.router, prefix=settings.API_V1_PREFIX)
    app.include_router(requests.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_requests.router, prefix=settings.API_V1_PREFIX)
    app.include_router(me.router, prefix=settings.API_V1_PREFIX)
    app.include_router(visualize.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_boundary.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_settings.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_categories.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_users.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_roles.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_qc.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_team.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_overview.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_media.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_cms.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_blog.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_analytics.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_reports.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_backups.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_audit.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_about_team.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_contact.router, prefix=settings.API_V1_PREFIX)
    app.include_router(admin_support.router, prefix=settings.API_V1_PREFIX)
    app.include_router(content.router, prefix=settings.API_V1_PREFIX)

    @app.get("/api/health", tags=["health"])
    async def health():
        return {"status": "ok", "environment": settings.ENVIRONMENT}

    return app


app = create_app()
