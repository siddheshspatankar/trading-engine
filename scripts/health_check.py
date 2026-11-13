#!/usr/bin/env python3
"""
System health check script.

Verifies connectivity to all external dependencies.
Use this before starting the engine to confirm everything is reachable.

Usage: python scripts/health_check.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run_health_checks() -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print("\n[bold]Trading Engine — System Health Check[/bold]\n")

    checks = []

    # Database
    try:
        from src.infrastructure.database import check_database_health
        result = await check_database_health()
        status = "✓" if result["status"] == "healthy" else "✗"
        checks.append(("PostgreSQL", status, result.get("status", "unknown")))
    except Exception as e:
        checks.append(("PostgreSQL", "✗", str(e)))

    # Redis
    try:
        from src.market_data.cache.redis_cache import MarketDataCache
        cache = MarketDataCache()
        ok = await cache.health_check()
        checks.append(("Redis", "✓" if ok else "✗", "healthy" if ok else "unreachable"))
    except Exception as e:
        checks.append(("Redis", "✗", str(e)))

    # Alpaca
    try:
        from src.market_data.providers.alpaca import AlpacaDataProvider
        provider = AlpacaDataProvider()
        ok = await provider.health_check()
        checks.append(("Alpaca API", "✓" if ok else "✗", "authenticated" if ok else "auth failed"))
    except Exception as e:
        checks.append(("Alpaca API", "✗", str(e)[:60]))

    # Yahoo Finance
    try:
        from src.market_data.providers.yahoo import YahooFinanceProvider
        provider = YahooFinanceProvider()
        ok = await provider.health_check()
        checks.append(("Yahoo Finance", "✓" if ok else "✗", "reachable" if ok else "failed"))
    except Exception as e:
        checks.append(("Yahoo Finance", "✗", str(e)[:60]))

    # Print results
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Service")
    table.add_column("Status")
    table.add_column("Details")

    all_ok = True
    for service, status, details in checks:
        color = "green" if status == "✓" else "red"
        table.add_row(service, f"[{color}]{status}[/{color}]", details)
        if status != "✓":
            all_ok = False

    console.print(table)

    if all_ok:
        console.print("\n[bold green]All systems healthy — ready to trade![/bold green]\n")
    else:
        console.print("\n[bold red]Some systems are unhealthy. Review before starting.[/bold red]\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_health_checks())
