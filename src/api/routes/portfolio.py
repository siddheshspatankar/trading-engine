"""Portfolio, positions and funds — backed by the in-memory paper broker."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, status

from src.api.routes.orders import process_open_orders
from src.api.state import INITIAL_CAPITAL, state
from src.api.validation import normalize_symbol
from src.core.models import Portfolio

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


async def _marked_portfolio() -> tuple[Portfolio, dict[str, dict]]:
    """Fill any crossed limit orders, revalue positions, and return (portfolio, quotes)."""
    await process_open_orders()
    held = sorted((await state.broker.get_account()).positions.keys())
    quotes: dict[str, dict] = {}
    if held:
        for q in await state.market.get_quotes(held):
            quotes[q.symbol] = q.to_dict()
        state.broker.mark_to_market({s: Decimal(str(q["price"])) for s, q in quotes.items()})
    return await state.broker.get_account(), quotes


def _position_row(pos, quote: dict | None) -> dict:
    qty = float(pos.quantity)
    avg = float(pos.average_cost)
    ltp = float(pos.current_price or pos.average_cost)
    invested = abs(qty) * avg
    pnl = (ltp - avg) * qty
    return {
        "symbol": pos.symbol,
        "quantity": qty,
        "side": "long" if qty > 0 else "short",
        "average_price": round(avg, 2),
        "ltp": round(ltp, 2),
        "invested": round(invested, 2),
        "current_value": round(abs(qty) * ltp, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / invested * 100, 2) if invested else 0.0,
        "day_change_pct": quote["change_pct"] if quote else None,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
    }


@router.get("/")
async def get_portfolio() -> dict:
    """Account summary: funds, exposure and P&L."""
    portfolio, quotes = await _marked_portfolio()
    rows = [_position_row(p, quotes.get(s)) for s, p in portfolio.positions.items()]
    equity = float(portfolio.total_equity or portfolio.cash)
    invested = sum(r["invested"] for r in rows)
    unrealized = sum(r["pnl"] for r in rows)
    initial = float(INITIAL_CAPITAL)
    return {
        "initial_capital": initial,
        "cash": round(float(portfolio.cash), 2),
        "equity": round(equity, 2),
        "invested": round(invested, 2),
        "current_value": round(sum(r["current_value"] for r in rows), 2),
        "unrealized_pnl": round(unrealized, 2),
        "unrealized_pnl_pct": round(unrealized / invested * 100, 2) if invested else 0.0,
        "total_pnl": round(equity - initial, 2),
        "total_pnl_pct": round((equity - initial) / initial * 100, 2),
        "commission_paid": round(float(state.broker.total_commission), 2),
        "num_positions": len(rows),
        "exposure_pct": round(sum(r["current_value"] for r in rows) / equity * 100, 2) if equity else 0.0,
    }


@router.get("/positions")
async def get_positions() -> dict:
    portfolio, quotes = await _marked_portfolio()
    rows = [_position_row(p, quotes.get(s)) for s, p in portfolio.positions.items()]
    rows.sort(key=lambda r: r["current_value"], reverse=True)
    return {"positions": rows}


@router.get("/positions/{symbol}")
async def get_position(symbol: str) -> dict:
    sym = normalize_symbol(symbol)
    portfolio, quotes = await _marked_portfolio()
    pos = portfolio.positions.get(sym)
    if pos is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No open position for {sym}")
    return _position_row(pos, quotes.get(sym))


@router.post("/reset")
async def reset_paper_account() -> dict:
    """Wipe the paper account back to starting capital (positions, orders, risk log)."""
    halted = state.risk_engine.is_halted
    state.reset()
    return {"status": "reset", "initial_capital": float(INITIAL_CAPITAL), "was_halted": halted}
