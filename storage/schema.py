"""DDL схема и функция применения миграций."""
from __future__ import annotations

from storage.db import DatabasePool

# Полная DDL схема — все таблицы и индексы
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at    TIMESTAMPTZ,
    initial_capital_usdt NUMERIC(20,8),
    final_pnl_usdt       NUMERIC(20,8),
    total_trades INTEGER    NOT NULL DEFAULT 0,
    config      JSONB
);

CREATE TABLE IF NOT EXISTS trades (
    id                   BIGSERIAL   PRIMARY KEY,
    session_id           UUID        NOT NULL REFERENCES sessions(id),
    symbol               VARCHAR(20) NOT NULL,
    buy_exchange         VARCHAR(20) NOT NULL,
    sell_exchange        VARCHAR(20) NOT NULL,
    buy_order_id         VARCHAR(100),
    sell_order_id        VARCHAR(100),
    buy_price            NUMERIC(20,8),
    sell_price           NUMERIC(20,8),
    qty                  NUMERIC(20,8),
    pnl_usdt             NUMERIC(20,8),
    realized_spread_bps  NUMERIC(10,4),
    fee_usdt             NUMERIC(20,8),
    state                VARCHAR(20) NOT NULL,
    execution_time_ms    INTEGER,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fills (
    id          BIGSERIAL   PRIMARY KEY,
    trade_id    BIGINT      NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    exchange    VARCHAR(20) NOT NULL,
    order_id    VARCHAR(100) NOT NULL,
    side        VARCHAR(10) NOT NULL,
    filled_qty  NUMERIC(20,8),
    avg_price   NUMERIC(20,8),
    fee_usdt    NUMERIC(20,8),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS positions (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  UUID        NOT NULL,
    exchange    VARCHAR(20) NOT NULL,
    symbol      VARCHAR(20) NOT NULL,
    qty         NUMERIC(20,8) NOT NULL DEFAULT 0,
    cost_usdt   NUMERIC(20,8) NOT NULL DEFAULT 0,
    side        VARCHAR(10) NOT NULL DEFAULT 'flat',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, exchange, symbol)
);

CREATE TABLE IF NOT EXISTS spreads (
    id                   BIGSERIAL   PRIMARY KEY,
    symbol               VARCHAR(20) NOT NULL,
    buy_exchange         VARCHAR(20) NOT NULL,
    sell_exchange        VARCHAR(20) NOT NULL,
    raw_spread_bps       NUMERIC(10,4),
    executable_spread_bps NUMERIC(10,4),
    buy_price            NUMERIC(20,8),
    sell_price           NUMERIC(20,8),
    max_size_usdt        NUMERIC(20,8),
    executed             BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS balances (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  UUID        NOT NULL,
    exchange    VARCHAR(20) NOT NULL,
    asset       VARCHAR(20) NOT NULL,
    available   NUMERIC(20,8),
    total       NUMERIC(20,8),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS funding (
    id              BIGSERIAL   PRIMARY KEY,
    exchange        VARCHAR(20) NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    funding_rate    NUMERIC(12,8),
    next_funding_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS latency (
    id           BIGSERIAL   PRIMARY KEY,
    exchange     VARCHAR(20) NOT NULL,
    metric_type  VARCHAR(50) NOT NULL,
    value_us     INTEGER     NOT NULL,
    symbol       VARCHAR(20),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS metrics (
    id           BIGSERIAL    PRIMARY KEY,
    metric_name  VARCHAR(100) NOT NULL,
    value        NUMERIC(20,8),
    labels       JSONB,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS watchdog_logs (
    id             BIGSERIAL   PRIMARY KEY,
    symbol         VARCHAR(20) NOT NULL,
    event_type     VARCHAR(50) NOT NULL,
    worker_state   VARCHAR(20),
    restart_count  INTEGER     NOT NULL DEFAULT 0,
    error_message  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_session  ON trades(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_symbol   ON trades(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fills_trade     ON fills(trade_id);
CREATE INDEX IF NOT EXISTS idx_spreads_symbol  ON spreads(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_latency_lookup  ON latency(exchange, metric_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_watchdog_symbol ON watchdog_logs(symbol, created_at DESC);
"""


async def apply_schema(pool: DatabasePool) -> None:
    """Создать все таблицы и индексы (идемпотентно, IF NOT EXISTS)."""
    await pool.execute(SCHEMA_SQL)
