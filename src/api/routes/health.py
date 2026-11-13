"""Health check endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from src.infrastructure.database import check_database_health
from src.market_data.cache.redis_cache import MarketDataCache

router = APIRouter(prefix="/health", tags=["health"])

_cache = MarketDataCache()


@router.get("/")
async def health_check() -> dict:
    """Basic health check — returns 200 if the service is running."""
    return {
        "status": "healthy",
        "service": "trading-engine",
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/ready")
async def readiness_check() -> dict:
    """
    Deep readiness check — verifies connectivity to all dependencies.
    Returns 200 only when all dependencies are healthy.
    """
    db_health = await check_database_health()
    redis_health = await _cache.health_check()

    all_healthy = db_health["status"] == "healthy" and redis_health

    return {
        "status": "ready" if all_healthy else "not_ready",
        "timestamp": datetime.utcnow().isoformat(),
        "dependencies": {
            "database": db_health,
            "redis": {"status": "healthy" if redis_health else "unhealthy"},
        },
    }


@router.get("/live")
async def liveness_check() -> dict:
    """Kubernetes liveness probe — simple alive check."""
    return {"alive": True}
