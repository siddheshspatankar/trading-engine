"""Strategy management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from src.strategies.base.strategy import list_strategies

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("/")
async def list_registered_strategies() -> dict:
    """Return all registered strategy names."""
    return {"strategies": list_strategies()}


@router.get("/{strategy_name}")
async def get_strategy(strategy_name: str) -> dict:
    """Return details about a specific strategy."""
    strategies = list_strategies()
    if strategy_name not in strategies:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy {strategy_name!r} not found",
        )
    return {"name": strategy_name, "status": "registered"}
