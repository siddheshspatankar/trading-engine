"""
Trading Engine — Main Entry Point.

Supports multiple run modes:
  simulation  — fully synthetic, no connectivity required
  paper       — live market data, simulated execution
  shadow      — live decisions, logged but NOT sent to broker
  live        — real money (requires --confirm flag)

Usage:
  python -m src.main --mode paper
  python -m src.main --mode live --confirm
  python -m src.main backtest --strategy momentum --start 2020-01-01 --end 2023-12-31
  python -m src.main api  (starts the REST API server)
"""

from __future__ import annotations

import asyncio
import sys

import click
import structlog
import uvicorn

from src.infrastructure.config import get_settings
from src.observability.logging.setup import configure_logging

log = structlog.get_logger(__name__)


@click.group()
def cli() -> None:
    """AI-Native Intelligent Trading Engine."""
    settings = get_settings()
    configure_logging(
        log_level=settings.log_level,
        json_logs=not settings.is_development,
    )


@cli.command()
@click.option("--mode", default=None, help="Override APP_ENV: simulation|paper|shadow|live")
@click.option("--confirm", is_flag=True, default=False, help="Required for live mode")
def run(mode: str | None, confirm: bool) -> None:
    """Start the trading engine."""
    settings = get_settings()
    effective_mode = mode or settings.env

    if effective_mode == "live" and not confirm:
        click.echo(
            "ERROR: Live trading requires explicit --confirm flag.\n"
            "       This is real money. Ensure risk checks are configured.\n"
            "       Re-run with: --mode live --confirm",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Starting trading engine in {effective_mode.upper()} mode...")
    asyncio.run(_run_engine(effective_mode))


@cli.command()
@click.option("--strategy", required=True, help="Strategy name (e.g. momentum)")
@click.option("--symbols", default="AAPL,MSFT,GOOGL", help="Comma-separated symbols")
@click.option("--start", default="2020-01-01", help="Start date YYYY-MM-DD")
@click.option("--end", default="2023-12-31", help="End date YYYY-MM-DD")
@click.option("--capital", default=100000.0, help="Initial capital in USD")
def backtest(
    strategy: str,
    symbols: str,
    start: str,
    end: str,
    capital: float,
) -> None:
    """Run a strategy backtest."""
    asyncio.run(_run_backtest(strategy, symbols.split(","), start, end, capital))


@cli.command()
@click.option("--host", default="0.0.0.0", help="Bind host")
@click.option("--port", default=None, type=int, help="Override APP_PORT")
@click.option("--reload", is_flag=True, default=False, help="Auto-reload on file change (dev only)")
def api(host: str, port: int | None, reload: bool) -> None:
    """Start the REST API server."""
    settings = get_settings()
    effective_port = port or settings.port

    if reload and not settings.is_development:
        click.echo("WARNING: --reload should only be used in development mode", err=True)

    uvicorn.run(
        "src.api.app:create_app",
        factory=True,
        host=host,
        port=effective_port,
        reload=reload,
        log_config=None,  # We use structlog instead
    )


async def _run_engine(mode: str) -> None:
    """Main engine loop."""
    from src.core.exceptions import LiveTradingNotAuthorized

    if mode == "live":
        # Additional live safety check — verify risk limits are configured
        settings = get_settings()
        if settings.risk.max_drawdown_pct > 0.20:
            click.echo(
                "WARNING: max_drawdown_pct > 20%. This is a very high limit for live trading.",
                err=True,
            )

    log.info("engine_starting", mode=mode)

    # Import strategies to register them
    import src.strategies.quant.momentum  # noqa: F401
    import src.strategies.quant.mean_reversion  # noqa: F401
    import src.strategies.quant.pairs_trading  # noqa: F401

    from src.strategies.base.strategy import list_strategies

    log.info("strategies_registered", strategies=list_strategies())

    # In simulation mode, run a simple demo cycle
    if mode == "simulation":
        log.info("simulation_mode_active")
        click.echo("Engine running in simulation mode. Press Ctrl+C to stop.")
        try:
            while True:
                await asyncio.sleep(60)
                log.info("engine_heartbeat", mode=mode)
        except KeyboardInterrupt:
            log.info("engine_shutdown_requested")
    else:
        log.info("engine_ready", mode=mode)
        click.echo(f"Engine running in {mode} mode. Press Ctrl+C to stop.")
        try:
            while True:
                await asyncio.sleep(60)
                log.info("engine_heartbeat", mode=mode)
        except KeyboardInterrupt:
            log.info("engine_shutdown_requested")


async def _run_backtest(
    strategy_name: str,
    symbols: list[str],
    start: str,
    end: str,
    capital: float,
) -> None:
    """Execute a backtest and print results."""
    from datetime import datetime
    from decimal import Decimal

    from rich.console import Console
    from rich.table import Table

    from src.backtesting.engine.backtester import Backtest, BacktestConfig
    from src.strategies.base.strategy import get_strategy_class

    # Import all strategies to populate registry
    import src.strategies.quant.momentum  # noqa: F401
    import src.strategies.quant.mean_reversion  # noqa: F401

    console = Console()
    console.print(f"\n[bold cyan]Running backtest: {strategy_name}[/bold cyan]")
    console.print(f"Symbols: {', '.join(symbols)}")
    console.print(f"Period:  {start} → {end}")
    console.print(f"Capital: ${capital:,.0f}\n")

    strategy_cls = get_strategy_class(strategy_name)
    strategy = strategy_cls(
        strategy_id=f"{strategy_name}_backtest",
        config={"universe": symbols},
    )

    from src.market_data.providers.yahoo import YahooFinanceProvider
    provider = YahooFinanceProvider()

    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)

    console.print("[yellow]Fetching historical data...[/yellow]")
    market_data = await provider.get_multiple_bars(symbols, start_dt, end_dt, "1D")

    if not market_data:
        console.print("[red]No data returned. Check symbols and date range.[/red]")
        return

    config = BacktestConfig(
        initial_capital=Decimal(str(capital)),
        slippage_bps=5,
    )

    # Rename for the actual import
    from src.backtesting.engine.backtester import Backtester

    backtester = Backtester(config=config)
    result = await backtester.run(strategy, market_data)

    # Print results table
    table = Table(title=f"Backtest Results — {strategy_name}", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    for metric, value in result.metrics.items():
        if isinstance(value, float):
            if "pct" in metric or "return" in metric or "drawdown" in metric or "rate" in metric:
                formatted = f"{value:.2%}"
            elif "ratio" in metric:
                formatted = f"{value:.2f}"
            else:
                formatted = f"{value:.4f}"
        else:
            formatted = str(value)
        table.add_row(metric.replace("_", " ").title(), formatted)

    console.print(table)
    console.print(f"\nTotal Return: [bold]{'+'  if result.total_return >= 0 else ''}{result.total_return:.2%}[/bold]")
    console.print(f"Final Equity: [bold]${result.final_equity:,.2f}[/bold]")
    console.print(f"Number of Trades: [bold]{result.num_trades}[/bold]")


if __name__ == "__main__":
    cli()
