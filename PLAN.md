# Arbitrage Terminal — Plan de développement

## Статус фаз

| # | Фаза | Статус | Тестов |
|---|------|--------|--------|
| 1 | Foundation | ✅ Готово | ✓ |
| 2 | Exchange Connectivity | ✅ Готово | ✓ |
| 3 | Orderbook Engine | ✅ Готово | ✓ |
| 4 | Spread Engine | ✅ Готово | ✓ |
| 5 | Execution Engine | ✅ Готово | ✓ |
| 6 | Risk Engine | ✅ Готово | ✓ |
| 7 | Watchdog System | ✅ Готово | ✓ |
| 8 | Storage Layer | ✅ Готово | ✓ |
| 9 | Monitoring | 🔲 Следующая | — |
| 10 | Frontend Terminal | ✅ Готово | ✓ |
| 11 | Backtesting | ✅ Готово | ✓ |
| 12 | Deployment | ✅ Готово | — |

---

## Что сделано

### Фаза 1 — Foundation
- Структура проекта, config YAML + APP_ENV overrides
- Типизированные модели (msgspec): Exchange, Order, SpreadOpportunity и др.
- structlog (консоль dev / JSON prod)
- AsyncLifecycle: startup/shutdown хуки, SIGINT/SIGTERM
- SecretsManager: .env + testnet/prod ключи

### Фаза 2 — Exchange Connectivity
- BaseExchange ABC + on_orderbook/on_ticker dispatch
- TokenBucket rate limiter (REST + orders)
- BaseWsClient: exponential backoff (1s→60s), ping loop, stale watchdog (35s)
- Bybit: WS (spot/linear), REST (HMAC-SHA256, place/cancel/get order)
- Binance: WS (combined streams), REST (HMAC-SHA256, testnet)

### Фаза 3 — Orderbook Engine
- LocalOrderBook: dict[price→qty], snapshot/delta, best_bid/ask, spread, imbalance
- slippage_for_qty, liquidity_usdt
- OrderBookEngine: маршрутизация, Bybit CRC32, stale watchdog

### Фаза 4 — Spread Engine
- FeeTable: maker/taker bps по биржам (Bybit spot/perp, Binance spot/perp, OKX)
- SpreadCalculator: raw→effective→net→executable, latency cost
- SpreadDetector: сканирует все пары одного символа в обе стороны

### Фаза 5 — Execution Engine
- OrderManager: place_and_wait (IOC/Limit poll), place_aggressive (3 retry IOC→MARKET)
- SmartOrderRouter: maker если spread ≥ 20bps, иначе IOC; hedge всегда IOC
- ExecutionEngine: asyncio.gather обе ноги, emergency hedge recovery

### Фаза 6 — Risk Engine
- RiskLimits: exposure, drawdown, volatility, stale signal
- InventoryManager: позиции, PnL, drawdown_pct, price_move_bps
- RiskEngine: pre-trade check (8 видов нарушений), auto-pause/halt circuit breaker

### Фаза 7 — Watchdog System
- WorkerPolicy: RestartPolicy (ALWAYS/ON_FAILURE/NEVER), exponential backoff
- StrategyWorker: 1 символ = 1 asyncio.Task, queue 100, heartbeat, изолированные ошибки
- WorkerSupervisor: watchdog каждые 5с, неблокирующий restart через create_task
- WatchdogOrchestrator: синхронная маршрутизация opp→worker, 500+ воркеров

### Фаза 8 — Storage Layer
- DatabasePool: asyncpg connection pool
- RedisClient: redis.asyncio lifecycle
- Schema: 10 таблиц (trades, fills, positions, spreads, balances, sessions, funding, latency, metrics, watchdog_logs) + индексы
- TradeRepository, PositionRepository (UPSERT), SpreadRepository
- LatencyRepository (P50/P95/P99), MetricsRepository, WatchdogLogRepository
- RedisCache: HSET позиций/PnL/балансов с TTL
- RedisPubSub: publish_spread/trade/risk_alert, async subscribe

---

## Что впереди

### Фаза 9 — Monitoring
- Prometheus exporters (custom metrics: PnL, spread bps, latency, worker health)
- /metrics HTTP endpoint (aiohttp)
- Grafana dashboard JSON (trade PnL, spread scanner, latency heatmap, watchdog status)
- Latency tracking: ws_message, rest_order, execution в микросекундах

### Фаза 10 — Frontend Terminal
- React + Next.js + TypeScript + Tailwind
- WebSocket сервер (aiohttp) для real-time push
- Страницы: PnL dashboard, spread monitor, watchdog monitor, execution log
- Компоненты: orderbook heatmap, latency chart, risk gauge, exchange balances

### Фаза 11 — Backtesting
- Исторический replay orderbook (tick-by-tick)
- Симуляция slippage, комиссий, latency
- Monte Carlo stress testing
- Сравнение стратегий по Sharpe/Sortino/max drawdown

### Фаза 12 — Deployment
- Dockerfile + docker-compose (app + postgres + redis + grafana + prometheus)
- Production конфиги (uvicorn, gunicorn, nginx)
- Kubernetes-ready (Deployment, Service, ConfigMap, Secret)
- Горизонтальное масштабирование watchdog-воркеров

---

## Текущие тесты

```
232 passed (из 232)

test_config.py         — ConfigManager, deep merge, env override
test_models.py         — SpreadOpportunity, OrderBook models
test_lifecycle.py      — AsyncLifecycle hooks, shutdown order
test_rate_limiter.py   — TokenBucket acquire/refill
test_exchange_parsers  — Bybit/Binance status parsing
test_orderbook.py      — LocalOrderBook snapshot/delta/slippage
test_spread.py         — FeeTable, SpreadCalculator, SpreadDetector
test_execution.py      — OrderManager, SmartOrderRouter, ExecutionEngine
test_risk.py           — RiskLimits, InventoryManager, RiskEngine
test_watchdog.py       — WorkerPolicy, StrategyWorker, Supervisor, Orchestrator
test_storage.py        — TradeRepo, PositionRepo, SpreadRepo, Cache, PubSub
test_backtesting.py    — SlippageModel, LatencyModel, TradeSimulator, OrderBookReplayer, BacktestMetrics, MonteCarloEngine, BacktestEngine
```
