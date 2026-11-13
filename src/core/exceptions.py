"""
Domain exceptions for the trading engine.

Exceptions are hierarchical so callers can catch at the right level of specificity.
"""


class TradingEngineError(Exception):
    """Base exception for all trading engine errors."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, code={self.code!r})"


# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------


class MarketDataError(TradingEngineError):
    """Raised when market data cannot be retrieved or is invalid."""


class DataProviderUnavailable(MarketDataError):
    """Raised when a data provider is unreachable or rate-limited."""


class InsufficientHistoricalData(MarketDataError):
    """Raised when there is not enough history to compute a signal."""


class StaleDataError(MarketDataError):
    """Raised when data is older than the acceptable staleness threshold."""


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class StrategyError(TradingEngineError):
    """Base class for strategy-level errors."""


class StrategyNotFound(StrategyError):
    """Raised when a named strategy does not exist in the registry."""


class StrategyConfigError(StrategyError):
    """Raised when a strategy's configuration is invalid."""


class SignalGenerationError(StrategyError):
    """Raised when signal generation fails."""


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


class RiskError(TradingEngineError):
    """Base class for risk engine errors."""


class RiskLimitBreached(RiskError):
    """Raised when a proposed order would breach a risk limit."""

    def __init__(self, message: str, limit_type: str, limit_value: float, actual_value: float) -> None:
        super().__init__(message, code="RISK_LIMIT_BREACHED")
        self.limit_type = limit_type
        self.limit_value = limit_value
        self.actual_value = actual_value


class DrawdownLimitBreached(RiskError):
    """Raised when portfolio drawdown exceeds the configured threshold."""


class DailyLossLimitBreached(RiskError):
    """Raised when daily loss exceeds the configured limit — triggers trading halt."""


class PositionSizeLimitBreached(RiskLimitBreached):
    """Raised when a position would exceed the maximum allowed size."""


class CorrelationRiskError(RiskError):
    """Raised when adding a position would create excessive correlation exposure."""


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class ExecutionError(TradingEngineError):
    """Base class for order execution errors."""


class OrderRejectedError(ExecutionError):
    """Raised when a broker rejects an order."""

    def __init__(self, message: str, order_id: str, broker_reason: str | None = None) -> None:
        super().__init__(message, code="ORDER_REJECTED")
        self.order_id = order_id
        self.broker_reason = broker_reason


class InsufficientFundsError(ExecutionError):
    """Raised when there is not enough buying power for an order."""


class BrokerConnectionError(ExecutionError):
    """Raised when the broker API is unreachable."""


class OrderNotFoundError(ExecutionError):
    """Raised when an order ID cannot be found."""


class InvalidOrderError(ExecutionError):
    """Raised when an order has invalid parameters."""


# ---------------------------------------------------------------------------
# Broker
# ---------------------------------------------------------------------------


class BrokerError(TradingEngineError):
    """Base class for broker adapter errors."""


class BrokerAuthenticationError(BrokerError):
    """Raised when broker authentication fails."""


class MarketClosedError(BrokerError):
    """Raised when an order is submitted outside market hours."""


# ---------------------------------------------------------------------------
# Backtesting
# ---------------------------------------------------------------------------


class BacktestError(TradingEngineError):
    """Base class for backtesting errors."""


class BacktestConfigError(BacktestError):
    """Raised when backtest configuration is invalid."""


class LookAheadBiasError(BacktestError):
    """Raised when a strategy attempts to access future data during backtest."""


# ---------------------------------------------------------------------------
# AI Agent
# ---------------------------------------------------------------------------


class AgentError(TradingEngineError):
    """Base class for AI agent errors."""


class AgentTimeoutError(AgentError):
    """Raised when an AI agent does not respond within the allowed time."""


class AgentResponseInvalid(AgentError):
    """Raised when an AI agent returns a malformed or unprocessable response."""


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


class ConfigurationError(TradingEngineError):
    """Raised when required configuration is missing or invalid."""


class DatabaseError(TradingEngineError):
    """Raised when a database operation fails."""


class CacheError(TradingEngineError):
    """Raised when a cache operation fails."""


class LiveTradingNotAuthorized(TradingEngineError):
    """
    Raised when live trading is attempted without explicit authorization.
    This is a safety guard — live mode requires opt-in flags and passes risk pre-checks.
    """

    def __init__(self) -> None:
        super().__init__(
            "Live trading requires explicit authorization. "
            "Start the engine with --mode live --confirm and ensure all risk checks pass.",
            code="LIVE_TRADING_NOT_AUTHORIZED",
        )
