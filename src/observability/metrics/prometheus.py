"""
Prometheus metrics for the trading engine.

Exposes an HTTP endpoint at /metrics for Prometheus scraping.
All key business and operational metrics are defined here as module-level singletons.
"""

from __future__ import annotations

from prometheus_client import (  # type: ignore
    Counter,
    Gauge,
    Histogram,
    Summary,
    start_http_server,
)
import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Order Metrics
# ---------------------------------------------------------------------------

orders_total = Counter(
    "trading_orders_total",
    "Total number of orders created",
    ["symbol", "side", "strategy"],
)

orders_filled_total = Counter(
    "trading_orders_filled_total",
    "Total number of orders filled",
    ["symbol", "side"],
)

orders_rejected_total = Counter(
    "trading_orders_rejected_total",
    "Total number of orders rejected (by risk engine or broker)",
    ["reason"],
)

order_fill_latency_seconds = Histogram(
    "trading_order_fill_latency_seconds",
    "Time from order submission to fill",
    ["symbol"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

# ---------------------------------------------------------------------------
# Risk Metrics
# ---------------------------------------------------------------------------

risk_checks_total = Counter(
    "trading_risk_checks_total",
    "Total risk checks performed",
    ["check_type", "result"],
)

drawdown_current = Gauge(
    "trading_drawdown_current",
    "Current drawdown from peak equity (0-1)",
)

daily_pnl_dollars = Gauge(
    "trading_daily_pnl_dollars",
    "Current daily P&L in USD",
)

portfolio_equity_dollars = Gauge(
    "trading_portfolio_equity_dollars",
    "Current total portfolio equity in USD",
)

portfolio_positions_count = Gauge(
    "trading_portfolio_positions_count",
    "Number of open positions",
)

# ---------------------------------------------------------------------------
# Signal Metrics
# ---------------------------------------------------------------------------

signals_generated_total = Counter(
    "trading_signals_generated_total",
    "Total trading signals generated",
    ["strategy", "direction"],
)

signal_strength_histogram = Histogram(
    "trading_signal_strength",
    "Distribution of signal strengths",
    ["strategy"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# ---------------------------------------------------------------------------
# System Metrics
# ---------------------------------------------------------------------------

agent_call_duration_seconds = Histogram(
    "trading_agent_call_duration_seconds",
    "Duration of AI agent calls",
    ["agent_type"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

agent_errors_total = Counter(
    "trading_agent_errors_total",
    "Total AI agent errors",
    ["agent_type", "error_type"],
)

market_data_requests_total = Counter(
    "trading_market_data_requests_total",
    "Total market data API requests",
    ["provider", "data_type"],
)

market_data_cache_hits_total = Counter(
    "trading_market_data_cache_hits_total",
    "Market data cache hits",
    ["provider"],
)

execution_cycle_duration_seconds = Histogram(
    "trading_execution_cycle_duration_seconds",
    "Full execution cycle duration",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def start_metrics_server(port: int = 9090) -> None:
    """Start Prometheus HTTP metrics server on the given port."""
    try:
        start_http_server(port)
        log.info("prometheus_metrics_server_started", port=port)
    except OSError as exc:
        log.warning("metrics_server_start_failed", port=port, error=str(exc))
