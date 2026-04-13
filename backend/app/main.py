import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import settings
from .database import engine
from .routers import ask, auth, community, documents, graph, graph_tools, link, maintenance, notes, search, settings as settings_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SQLite pragmas
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA foreign_keys=ON"))

    # Recovery: resume interrupted documents from checkpoints
    try:
        from .worker import resume_interrupted_documents
        await resume_interrupted_documents()
    except Exception as e:
        logger.warning("Startup recovery skipped: %s", e)

    yield
    await engine.dispose()


app = FastAPI(title="Note Website API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(notes.router)
app.include_router(search.router)
app.include_router(graph.router)
app.include_router(ask.router)
app.include_router(link.router)
app.include_router(settings_router.router)
app.include_router(graph_tools.router)
app.include_router(maintenance.router)
app.include_router(community.router)
