# api/main.py

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from api.ingestion_router import router as ingestion_router
from api.approval_router import router as approval_router
from api.metrics_router import router as metrics_router
from core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "ap_ai_startup",
        service="AP-AI Accounts Payable Automation Agent",
        brand="Datawebify",
        version="1.0.0",
    )
    yield
    logger.info("ap_ai_shutdown")


app = FastAPI(
    title="AP-AI: Enterprise Accounts Payable Automation Agent",
    description=(
        "Autonomous invoice ingestion, extraction, validation, three-way match, "
        "approval routing, payment scheduling, and ERP sync. "
        "Built by Datawebify."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ------------------------------------------------------------------
# CORS
# ------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------
# Routers
# ------------------------------------------------------------------
app.include_router(ingestion_router)
app.include_router(approval_router)
app.include_router(metrics_router)


# ------------------------------------------------------------------
# Root
# ------------------------------------------------------------------
@app.get("/", tags=["Root"])
async def root():
    return {
        "service": "AP-AI Accounts Payable Automation Agent",
        "brand": "Datawebify",
        "version": "1.0.0",
        "docs": "/docs",
        "metrics": "/metrics",
        "health": "/metrics/health",
    }