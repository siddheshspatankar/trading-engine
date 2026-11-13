"""Input validation helpers shared by API routes."""

from __future__ import annotations

import re

from fastapi import HTTPException, status

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
MAX_SYMBOLS_PER_REQUEST = 50


def normalize_symbol(raw: str) -> str:
    """Uppercase and validate a ticker, raising 422 if it is not a plausible symbol."""
    symbol = raw.strip().upper()
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid symbol. Use 1-10 characters: letters, digits, '.' or '-'.",
        )
    return symbol


def parse_symbols(raw: str) -> list[str]:
    """Parse a comma-separated symbol list, de-duplicated and order-preserving."""
    symbols: list[str] = []
    for part in raw.split(","):
        if part.strip():
            sym = normalize_symbol(part)
            if sym not in symbols:
                symbols.append(sym)
    if not symbols:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No symbols provided.")
    if len(symbols) > MAX_SYMBOLS_PER_REQUEST:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"At most {MAX_SYMBOLS_PER_REQUEST} symbols per request.",
        )
    return symbols
