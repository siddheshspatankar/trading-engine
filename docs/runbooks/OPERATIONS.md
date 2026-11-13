# Operations Runbook

## Starting the Engine

### Paper Trading Mode (recommended for validation)
```bash
python -m src.main run --mode paper
```

### Shadow Mode (live decisions, no execution)
```bash
python -m src.main run --mode shadow
```

### Live Mode (REAL MONEY — requires explicit confirmation)
```bash
python -m src.main run --mode live --confirm
```

**Before starting live mode:**
1. Run health check: `python scripts/health_check.py`
2. Verify risk limits in `.env`
3. Confirm strategy has passed shadow mode validation
4. Ensure sufficient buying power
5. Verify broker API credentials

---

## Circuit Breaker — Emergency Halt

### Halt via API
```bash
curl -X POST http://localhost:8000/risk/halt \
  -H "Authorization: Bearer <token>" \
  -d '{"reason": "Manual halt — investigating anomaly"}'
```

### Halt via CLI (if API is down)
```python
from src.risk.engine.risk_engine import RiskEngine
engine = RiskEngine()
engine.halt_trading("Emergency halt")
```

### Resume Trading
```bash
curl -X POST http://localhost:8000/risk/resume \
  -H "Authorization: Bearer <token>"
```

---

## Risk Limit Triggers

| Event | Automatic Action |
|-------|------------------|
| Daily loss > 3% | Auto-halt all trading |
| Drawdown > 15% | Block new long entries |
| Drawdown > 11.25% (75% of limit) | Alert sent |
| Concentration > 60% (top 3) | Block new entries |
| Leverage > 2x | Block new entries |

---

## Monitoring

### Check System Health
```bash
python scripts/health_check.py
```

### View Metrics
Prometheus: http://localhost:9091
Grafana: http://localhost:3001 (admin/admin)

### View Traces
Jaeger UI: http://localhost:16686

### View Logs
```bash
# Follow live logs
docker logs trading-engine -f

# Filter by level
docker logs trading-engine 2>&1 | grep '"level":"error"'
```

---

## Running Backtests

```bash
# Momentum strategy, 2020-2023
python scripts/run_backtest.py \
  --strategy momentum \
  --symbols AAPL,MSFT,GOOGL,AMZN,NVDA \
  --start 2020-01-01 \
  --end 2023-12-31 \
  --capital 100000

# Mean reversion strategy
python scripts/run_backtest.py \
  --strategy mean_reversion \
  --symbols AAPL,MSFT,GOOGL \
  --start 2021-01-01 \
  --end 2023-12-31
```

---

## Troubleshooting

### "Order rejected by risk engine"
Check the risk engine logs for the specific check that failed:
```bash
docker logs trading-engine 2>&1 | grep "risk_check_failed"
```

### "Broker connection error"
1. Verify API keys in `.env`
2. Check broker API status page
3. Run `python scripts/health_check.py`

### "Market data unavailable"
1. Check data provider rate limits
2. Redis cache may be stale — flush: `redis-cli FLUSHDB`
3. Try fallback provider (Yahoo Finance)

### "Trading halted — won't resume"
Only operations team can resume trading:
```bash
curl -X POST http://localhost:8000/risk/resume \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```
Investigate the halt reason in logs before resuming.
