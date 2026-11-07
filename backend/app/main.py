"""
Application entrypoint: assembles config, logging, middleware, exception
handlers, routers, CORS, and DB table creation into one FastAPI app.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.logging_config import configure_logging
configure_logging()

from app.config import settings
from app.database import Base, engine
from app.middleware import RequestLoggingMiddleware
from app.exceptions import (
    QueryPilotException,
    querypilot_exception_handler,
    unhandled_exception_handler,
)
from app.routers import (
    auth_router,
    query_router,
    history_router,
    schema_router,
    health_router,
)

# Creates QueryPilot's own app tables (users, query_history) if they don't
# exist. In a real production deploy this would be Alembic migrations
# instead of create_all — noted in README as a known simplification.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="QueryPilot",
    description="Natural Language to SQL Analytics Copilot",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

app.add_exception_handler(QueryPilotException, querypilot_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.include_router(health_router.router)
app.include_router(auth_router.router)
app.include_router(query_router.router)
app.include_router(history_router.router)
app.include_router(schema_router.router)
