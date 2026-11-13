# System Architecture

## Overview

The AI-Native Intelligent Trading Engine follows a layered, modular architecture where:
- **Data flows top-down** through well-defined interfaces
- **AI agents provide context** but never directly authorize orders
- **Deterministic systems control** all risk and execution decisions
- **Every component is independently testable** and replaceable

---

## Layer Diagram

```
┌────────────────────────────────────────────────────────────┐
│                    External Data Sources                   │
│  Alpaca · Polygon · Yahoo Finance · Interactive Brokers   │
└────────────────────────────┬───────────────────────────────┘
                             │
┌────────────────────────────▼───────────────────────────────┐
│               Market Data Abstraction Layer                │
│         Unified interface: MarketDataProvider              │
│         Cache: Redis (quotes 5s TTL, bars 24h TTL)        │
└────────────────────────────┬───────────────────────────────┘
                             │
┌────────────────────────────▼───────────────────────────────┐
│            Quantitative Signal Engine                      │
│  MomentumStrategy · MeanReversionStrategy · PairTrading   │
│  All strategies implement: BaseStrategy.generate_signals() │
└────────────────────────────┬───────────────────────────────┘
                             │ raw signals
┌────────────────────────────▼───────────────────────────────┐
│          AI Multi-Agent Orchestration Layer                │
│                                                            │
│  ┌─────────────────────┐  ┌──────────────────────────┐   │
│  │ Market Intelligence │  │   Risk Advisor Agent     │   │
│  │ Agent (LLM)         │  │   (LLM, advisory only)   │   │
│  │ - News sentiment    │  │   - Tail risk flags      │   │
│  │ - Macro context     │  │   - Regime assessment    │   │
│  │ - Sector themes     │  │   - Position guidance    │   │
│  └─────────────────────┘  └──────────────────────────┘   │
│                                                            │
│  Agents RECOMMEND, never authorize. Output is advisory.   │
│  Orchestrator applies soft filters (strength reduction)   │
└────────────────────────────┬───────────────────────────────┘
                             │ filtered signals
┌────────────────────────────▼───────────────────────────────┐
│          Independent Risk Management Engine                │
│          (DETERMINISTIC — no AI, no external calls)        │
│                                                            │
│  Checks (in order):                                        │
│  1. Trading halt                                           │
│  2. Daily loss limit → auto-halt                          │
│  3. Max drawdown → block new longs                         │
│  4. Position size limit → size reduction                   │
│  5. Concentration limit                                    │
│  6. Leverage limit                                         │
│                                                            │
│  Result: APPROVED | MODIFIED | REJECTED                   │
└────────────────────────────┬───────────────────────────────┘
                             │ authorized orders
┌────────────────────────────▼───────────────────────────────┐
│                 Order Execution Engine                     │
│  Position sizing → Order creation → Algorithm selection   │
│  → Broker submission → Fill tracking                      │
│                                                            │
│  Shadow mode: logs orders but does NOT submit             │
└────────────────────────────┬───────────────────────────────┘
                             │
┌────────────────────────────▼───────────────────────────────┐
│                   Broker Adapters                          │
│    Alpaca (paper/live) · Interactive Brokers · Paper sim   │
└────────────────────────────────────────────────────────────┘
```

---

## Domain Events

All significant state changes publish domain events on the internal `EventBus`.
This enables loose coupling between layers and simplifies audit logging.

Key events:
- `SignalGenerated` — strategy emitted a signal
- `RiskCheckPassed / RiskCheckFailed` — risk engine decision
- `OrderCreated / OrderFilled / OrderRejected` — order lifecycle
- `DrawdownAlertTriggered` — drawdown approaching limit
- `TradingHaltTriggered` — circuit breaker activated
- `PortfolioUpdated` — portfolio state changed

---

## Data Flow for a Single Cycle

```
1. Market data arrives (tick / bar)
2. Strategy.execute(market_data, portfolio) → [Signal, ...]
3. Orchestrator.run_cycle(signals, ...) → filtered signals
   ├── Market Intelligence Agent (async, non-blocking)
   └── Risk Advisor Agent (async, non-blocking)
4. RiskEngine.evaluate_order(order, portfolio) → APPROVED/REJECTED
5. ExecutionEngine.execute_signals(authorized_signals, ...)
6. BrokerAdapter.submit_order(order) → filled Order
7. EventBus.publish(OrderFilled)
8. PortfolioTracker updates positions and equity
```

---

## Trading Modes

| Mode | Data | Execution | Use Case |
|------|------|-----------|----------|
| `simulation` | Synthetic | Paper simulation | Development, testing |
| `paper` | Live market | Paper simulation | Strategy validation |
| `shadow` | Live market | Logged, not sent | Pre-live validation |
| `live` | Live market | Real broker | Production |

**Live mode requires `--mode live --confirm` flags.** The risk engine runs all checks before any order is submitted.

---

## Security Model

- All secrets in environment variables (never in code)
- API endpoints protected by JWT authentication
- Risk engine cannot be bypassed — it's in the execution path
- Audit log for every order and risk decision
- Shadow mode for pre-live validation
- Pre-commit hooks block secrets from being committed

---

## Observability

- **Structured logging** via structlog → JSON in production
- **Prometheus metrics** at `:9090/metrics`
- **Distributed tracing** via OpenTelemetry → Jaeger
- **Business metrics**: order fill rate, signal accuracy, drawdown, P&L
- **Alerts**: PagerDuty + Slack for critical events (trading halt, circuit breaker)
