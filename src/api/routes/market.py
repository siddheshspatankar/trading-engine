"""Market data endpoints: quotes, daily bars, index tickers."""

from __future__ import annotations

from fastapi import APIRouter, Query

from src.api.state import state
from src.api.validation import normalize_symbol, parse_symbols

router = APIRouter(prefix="/market", tags=["market"])

# US index proxies shown in the header (Kite shows NIFTY 50 / SENSEX in the same spot)
INDEX_PROXIES = [
    {"symbol": "SPY", "label": "S&P 500"},
    {"symbol": "QQQ", "label": "NASDAQ 100"},
    {"symbol": "DIA", "label": "DOW 30"},
]


@router.get("/status")
async def market_status() -> dict:
    """Which data source the engine is using."""
    return {
        "alpaca_configured": state.market.alpaca_enabled,
        "preferred_source": "alpaca_iex" if state.market.alpaca_enabled else "simulated",
    }


@router.get("/quotes")
async def get_quotes(symbols: str = Query(..., description="Comma-separated, e.g. AAPL,MSFT")) -> dict:
    quotes = await state.market.get_quotes(parse_symbols(symbols))
    return {"quotes": [q.to_dict() for q in quotes]}


@router.get("/indices")
async def get_indices() -> dict:
    quotes = await state.market.get_quotes([i["symbol"] for i in INDEX_PROXIES])
    return {
        "indices": [
            {**q.to_dict(), "label": meta["label"]} for meta, q in zip(INDEX_PROXIES, quotes)
        ]
    }


@router.get("/bars/{symbol}")
async def get_bars(symbol: str, days: int = Query(180, ge=30, le=1500)) -> dict:
    sym = normalize_symbol(symbol)
    df, source = await state.market.get_bars(sym, days)
    bars = [
        {
            "t": ts.isoformat(),
            "o": round(float(row.open), 2),
            "h": round(float(row.high), 2),
            "l": round(float(row.low), 2),
            "c": round(float(row.close), 2),
            "v": float(row.volume),
        }
        for ts, row in df.iterrows()
    ]
    return {"symbol": sym, "source": source, "bars": bars}
