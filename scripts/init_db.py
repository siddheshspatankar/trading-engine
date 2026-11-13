#!/usr/bin/env python3
"""
Database initialization script.

Creates all tables defined in SQLAlchemy models.
Run this once after first deployment or when adding new models.

Usage: python scripts/init_db.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def init_db() -> None:
    from src.infrastructure.database import Base, get_engine
    from src.observability.logging.setup import configure_logging

    configure_logging(log_level="INFO", json_logs=False)

    print("Initializing database...")
    engine = get_engine()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("✓ Database tables created successfully")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_db())
