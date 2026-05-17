"""
tui_real.py — Arbitrage Terminal с реальным исполнением ордеров.
Запуск: python3 tui_real.py
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

load_dotenv(Path(".env"))

from run import _BLACKLIST  # noqa: E402

# ── Shared state ──────────────────────────────────────────────────────────
_spread_map: dict[tuple, dict] = {}
_stats = {
    "pairs":           0,
    "bybit_connected": False,
    "gate_connected":  False,
    "start_time":      time.time(),
    "bybit_usdt":      -1.0,
    "gate_usdt":       -1.0,
    "bybit_usdt_start": -1.0,
    "gate_usdt_start":  -1.0,
}
_paused: bool = False
_auto_paused: bool = False  # авто-пауза из-за недостаточного баланса

FEE_BPS = 14.0  # Bybit 9.0 taker + Gate.io 5.0 taker

# ── Реальные позиции и история ────────────────────────────────────────────
_trades: list[dict] = []
_positions: dict[tuple, dict] = {}
_portfolio = {"realized_pnl": 0.0, "unrealized_pnl": 0.0}
_cooldown: dict[str, float] = {}
_pending_entries: set[tuple] = set()
_trades_page = 0
_bybit_qty_steps: dict[str, float] = {}  # symbol → qtyStep из instruments-info
_last_entry_at: float = 0.0              # monotonic time последнего входа

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

TRADES_PER_PAGE = 10

# ── Горячий конфиг ────────────────────────────────────────────────────────
VIRTUAL_SIZE_USDT    = 50.0
ENTRY_THRESHOLD      = 15.0
EXIT_THRESHOLD       = -2.0
DYNAMIC_EXIT_RATIO   = 0.20
MAX_HOLD_S           = 60
MAX_POSITIONS        = 3
COOLDOWN_S           = 1800
MIN_ENTRY_INTERVAL_S = 60.0


def _reload_config() -> None:
    global ENTRY_THRESHOLD, EXIT_THRESHOLD, MAX_HOLD_S, DYNAMIC_EXIT_RATIO
    global MAX_POSITIONS, COOLDOWN_S, VIRTUAL_SIZE_USDT, MIN_ENTRY_INTERVAL_S
    try:
        if "config" in sys.modules:
            mod = importlib.reload(sys.modules["config"])
        else:
            import config as mod  # type: ignore
        ENTRY_THRESHOLD      = float(getattr(mod, "ENTRY_THRESHOLD",      ENTRY_THRESHOLD))
        EXIT_THRESHOLD       = float(getattr(mod, "EXIT_THRESHOLD",       EXIT_THRESHOLD))
        MAX_HOLD_S           = int(getattr(mod,   "MAX_HOLD_S",           MAX_HOLD_S))
        DYNAMIC_EXIT_RATIO   = float(getattr(mod, "DYNAMIC_EXIT_RATIO",   DYNAMIC_EXIT_RATIO))
        MAX_POSITIONS        = int(getattr(mod,   "MAX_POSITIONS",        MAX_POSITIONS))
        COOLDOWN_S           = int(getattr(mod,   "COOLDOWN_S",           COOLDOWN_S))
        VIRTUAL_SIZE_USDT    = float(getattr(mod, "VIRTUAL_SIZE_USDT",    VIRTUAL_SIZE_USDT))
        MIN_ENTRY_INTERVAL_S = float(getattr(mod, "MIN_ENTRY_INTERVAL_S", MIN_ENTRY_INTERVAL_S))
    except Exception:
        pass


_reload_config()


def _log_trade(trade: dict) -> None:
    log_file = LOG_DIR / f"real_trades_{time.strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(trade, ensure_ascii=False) + "\n")


def _hold_str(hold_ms: int) -> str:
    if hold_ms < 1000:
        return f"{hold_ms}мс"
    elif hold_ms < 60_000:
        return f"{hold_ms / 1000:.1f}с"
    else:
        s = hold_ms // 1000
        return f"{s // 60}м{s % 60:02d}с"


# ── UI builder ────────────────────────────────────────────────────────────

def _uptime() -> str:
    s = int(time.time() - _stats["start_time"])
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _conn(ok: bool) -> str:
    return "[green]●[/]" if ok else "[red]●[/]"


def _pnl_str(val: float, decimals: int = 2) -> str:
    fmt = f".{decimals}f"
    if val >= 0:
        return f"[green]+{val:{fmt}}[/]"
    return f"[red]{val:{fmt}}[/]"


def build_ui() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header",    size=3),
        Layout(name="positions", size=7),
        Layout(name="history",   size=14),
        Layout(name="spreads",   size=9),
        Layout(name="footer",    size=1),
    )

    # ── Header ──────────────────────────────────────────────────────────
    bybit_s = _conn(_stats["bybit_connected"])
    gate_s  = _conn(_stats["gate_connected"])

    def _bal_str(v: float) -> str:
        return f"[yellow]${v:.0f}[/]" if v >= 0 else "[dim]?[/]"

    def _r_str(current: float, start: float) -> str:
        if current < 0 or start < 0:
            return ""
        diff = current - start
        return f" {_pnl_str(diff, 0)}"

    by_r   = _r_str(_stats["bybit_usdt"], _stats["bybit_usdt_start"])
    gate_r = _r_str(_stats["gate_usdt"],  _stats["gate_usdt_start"])

    rpnl = _portfolio["realized_pnl"]
    upnl = _portfolio["unrealized_pnl"]
    if _auto_paused:
        pause_str = "  [bold yellow]⏸ НЕТ БАЛАНСА[/]"
    elif _paused:
        pause_str = "  [bold red]⏸ ПАУЗА[/]"
    else:
        pause_str = ""

    layout["header"].update(Panel(
        Text.from_markup(
            f"[bold cyan]◈ REAL TRADING[/]  Bybit: {bybit_s} {_bal_str(_stats['bybit_usdt'])}{by_r}  "
            f"Gate.io: {gate_s} {_bal_str(_stats['gate_usdt'])}{gate_r}  │  "
            f"Пар: [yellow]{_stats['pairs']}[/]  Up: [dim]{_uptime()}[/]  │  "
            f"Сессия: R:{_pnl_str(rpnl)}  U:{_pnl_str(upnl)}"
            f"{pause_str}"
        ),
        style="on grey7",
    ))

    # ── Открытые позиции ─────────────────────────────────────────────────
    pos_tbl = Table(box=box.SIMPLE_HEAVY, header_style="bold white on grey15",
                    expand=True, show_edge=False)
    pos_tbl.add_column("Символ",       style="cyan",    width=12)
    pos_tbl.add_column("Лонг",         width=5)
    pos_tbl.add_column("Цена лонг",    justify="right", width=12)
    pos_tbl.add_column("Шорт",         width=5)
    pos_tbl.add_column("Цена шорт",    justify="right", width=12)
    pos_tbl.add_column("Вход bps",     justify="right", width=9)
    pos_tbl.add_column("Сейчас bps",   justify="right", width=11)
    pos_tbl.add_column("Держим",       width=8)
    pos_tbl.add_column("Unreal $",     justify="right", width=10)

    for key, pos in _positions.items():
        current        = _spread_map.get(key)
        cur_ep         = current["executable_spread_bps"] if current else pos["entry_executable_bps"]
        cur_buy_price  = current.get("buy_price",  pos.get("entry_buy_price",  0.0)) if current else 0.0
        cur_sell_price = current.get("sell_price", pos.get("entry_sell_price", 0.0)) if current else 0.0
        unreal  = VIRTUAL_SIZE_USDT * (pos["entry_executable_bps"] - cur_ep) / 10_000
        hold_s  = int(time.time() - pos["opened_at"])
        hold_str = f"{hold_s//60}м{hold_s%60:02d}с"
        ep_now = (f"[green]{cur_ep:+.2f}[/]" if cur_ep > ENTRY_THRESHOLD
                  else f"[yellow]{cur_ep:+.2f}[/]" if cur_ep > 0
                  else f"[red]{cur_ep:+.2f}[/]")
        pos_tbl.add_row(
            pos["symbol"],
            pos["buy_exchange"].upper()[:5],
            f"{cur_buy_price:.4f}" if cur_buy_price else "—",
            pos["sell_exchange"].upper()[:5],
            f"{cur_sell_price:.4f}" if cur_sell_price else "—",
            f"+{pos['entry_executable_bps']:.2f}",
            ep_now, hold_str,
            _pnl_str(unreal, 4),
        )

    layout["positions"].update(Panel(
        pos_tbl,
        title=f"[bold red]РЕАЛЬНЫЕ Позиции[/]  {len(_positions)}/{MAX_POSITIONS}  │  "
              f"порог >{ENTRY_THRESHOLD:.0f}bps  выход <{EXIT_THRESHOLD:.0f}bps  ${VIRTUAL_SIZE_USDT:.0f}/сторону",
    ))

    # ── История сделок ───────────────────────────────────────────────────
    hist_tbl = Table(box=box.SIMPLE_HEAVY, header_style="bold white on grey15",
                     expand=True, show_edge=False)
    hist_tbl.add_column("Символ",    style="cyan",    width=12)
    hist_tbl.add_column("Вход bps",  justify="right", width=9)
    hist_tbl.add_column("Выход bps", justify="right", width=10)
    hist_tbl.add_column("P&L bps",   justify="right", width=9)
    hist_tbl.add_column("P&L $",     justify="right", width=9)
    hist_tbl.add_column("Причина",   width=10)
    hist_tbl.add_column("Статус",    width=10)
    hist_tbl.add_column("Держал",    justify="right", width=8)
    hist_tbl.add_column("Закрыт",    width=8)

    trades_rev = list(reversed(_trades))
    n_pages    = max(1, (len(trades_rev) + TRADES_PER_PAGE - 1) // TRADES_PER_PAGE)
    page       = _trades_page % n_pages
    for t in trades_rev[page * TRADES_PER_PAGE : (page + 1) * TRADES_PER_PAGE]:
        reason = t.get("close_reason", "")
        reason_str = (f"[cyan]{reason}[/]"   if reason == "разворот"  else
                      f"[green]{reason}[/]"  if reason == "захват80%" else
                      f"[yellow]{reason}[/]" if reason == "тайм-аут"  else
                      f"[dim]{reason}[/]")
        status = t.get("order_status", "ok")
        status_str = "[green]ok[/]" if status == "ok" else f"[red]{status}[/]"
        hist_tbl.add_row(
            t["symbol"],
            f"+{t['entry_bps']:.2f}",
            f"{t['exit_bps']:+.2f}",
            _pnl_str(t["pnl_bps"], 2),
            _pnl_str(t["pnl_usdt"], 2),
            reason_str, status_str,
            t["hold"],
            t["time"],
        )

    layout["history"].update(Panel(
        hist_tbl,
        title="[bold]История реальных сделок[/]",
        subtitle=f"[dim]стр. {page + 1}/{n_pages}  PgUp / PgDn[/]",
    ))

    # ── Спреды (топ 5) ───────────────────────────────────────────────────
    tbl = Table(box=box.SIMPLE_HEAVY, header_style="bold white on grey15",
                expand=True, show_edge=False)
    tbl.add_column("Символ",    style="cyan",    width=14)
    tbl.add_column("Лонг",      width=7)
    tbl.add_column("Шорт",      width=7)
    tbl.add_column("Спред",     justify="right", width=9)
    tbl.add_column("Прибыль",   justify="right", width=11)
    tbl.add_column("Обновлено", width=10)

    rows = sorted(
        (v for v in _spread_map.values() if v["symbol"] not in _BLACKLIST),
        key=lambda x: x["executable_spread_bps"], reverse=True,
    )[:5]

    for row in rows:
        ep  = row["executable_spread_bps"]
        raw = row["raw_spread_bps"]
        ep_str  = (f"[bold green]+{ep:.2f} bps[/]" if ep > 0
                   else f"[yellow]{ep:.2f} bps[/]"  if ep > -5
                   else f"[dim]{ep:.2f} bps[/]")
        raw_str = (f"[green]{raw:+.2f}[/]" if ep > 0
                   else f"[yellow]{raw:+.2f}[/]" if ep > -5
                   else f"[dim]{raw:+.2f}[/]")
        tbl.add_row(
            row["symbol"],
            row["buy_exchange"].upper()[:5],
            row["sell_exchange"].upper()[:5],
            raw_str, ep_str,
            row["created_at"][11:19],
        )

    layout["spreads"].update(Panel(
        tbl,
        title="[bold]Bybit Linear  ↔  Gate.io Futures  │  x1 leverage[/]",
        subtitle=f"[dim]порог >{FEE_BPS} bps  │  топ 5 из {len(_spread_map)}[/]",
    ))

    layout["footer"].update(Text.from_markup(
        "  [bold red]РЕАЛЬНЫЕ ДЕНЬГИ[/]  [dim]│  q/Ctrl+C — выход  │  P — пауза/старт[/]"
    ))

    return layout


# ── Real bot logic ────────────────────────────────────────────────────────

def _bybit_qty(sym: str, size_usdt: float, price: float) -> float:
    """USDT → количество базовой валюты для Bybit linear, с учётом qtyStep."""
    if price <= 0:
        return 0.0
    step = _bybit_qty_steps.get(sym, 1.0)  # default=1: целый контракт
    raw  = size_usdt / price
    qty  = round(round(raw / step) * step, 8)
    return max(step, qty)  # минимум один шаг


async def bot_main_real(symbols: list[str]) -> None:
    from core.models import Exchange, MarketType, OrderSide, OrderType
    from exchanges.bybit.adapter import BybitAdapter
    from exchanges.gate.adapter_real import GateAdapterReal
    from orderbook.engine import OrderBookEngine
    from spread.calculator import SpreadCalculator
    from spread.fees import FeeSchedule, FeeTable
    from credentials.manager import ExchangeCredentials

    bybit_creds = ExchangeCredentials(
        api_key    = os.environ.get("BYBIT_API_KEY", ""),
        api_secret = os.environ.get("BYBIT_API_SECRET", ""),
    )
    gate_creds = ExchangeCredentials(
        api_key    = os.environ.get("GATE_API_KEY", ""),
        api_secret = os.environ.get("GATE_API_SECRET", ""),
    )

    bybit_cfg = {"testnet": False, "rate_limit": {"requests_per_second": 10, "orders_per_second": 5}}
    bybit = BybitAdapter(config=bybit_cfg, credentials=bybit_creds)
    gate  = GateAdapterReal(credentials=gate_creds)

    ob_engine = OrderBookEngine(validate_checksum=False)
    bybit.on_orderbook(ob_engine.handle)
    gate.on_orderbook(ob_engine.handle)

    fee_table = FeeTable(overrides={
        (Exchange.GATE,  MarketType.PERPETUAL): FeeSchedule(maker_bps=0.0, taker_bps=5.0),
        (Exchange.BYBIT, MarketType.PERPETUAL): FeeSchedule(maker_bps=3.24, taker_bps=9.0),
    })
    calculator = SpreadCalculator(fee_table=fee_table, latency_us=10_000)

    # Хелперы для размещения ордеров на нужной бирже
    async def _do_open_long(exchange: str, sym: str, price: float) -> tuple:
        """Открыть лонг. Возвращает (order_id, qty_for_close)."""
        if exchange == "bybit":
            qty = _bybit_qty(sym, VIRTUAL_SIZE_USDT, price)
            order = await bybit.place_order(sym, MarketType.PERPETUAL,
                                            OrderSide.BUY, OrderType.MARKET, qty)
            return order.id, qty
        else:  # gate
            order = await gate.open_long(sym, VIRTUAL_SIZE_USDT, price)
            return order.id, order.qty

    async def _do_open_short(exchange: str, sym: str, price: float) -> tuple:
        """Открыть шорт. Возвращает (order_id, qty_for_close)."""
        if exchange == "bybit":
            qty = _bybit_qty(sym, VIRTUAL_SIZE_USDT, price)
            order = await bybit.place_order(sym, MarketType.PERPETUAL,
                                            OrderSide.SELL, OrderType.MARKET, qty)
            return order.id, qty
        else:  # gate
            order = await gate.open_short(sym, VIRTUAL_SIZE_USDT, price)
            return order.id, order.qty

    async def _do_close_long(exchange: str, sym: str, qty: float, price: float) -> None:
        """Закрыть лонг."""
        if exchange == "bybit":
            await bybit.place_order(sym, MarketType.PERPETUAL,
                                    OrderSide.SELL, OrderType.MARKET, qty)
        else:
            await gate.close_long(sym, qty, price)

    async def _do_close_short(exchange: str, sym: str, qty: float, price: float) -> None:
        """Закрыть шорт."""
        if exchange == "bybit":
            await bybit.place_order(sym, MarketType.PERPETUAL,
                                    OrderSide.BUY, OrderType.MARKET, qty)
        else:
            await gate.close_short(sym, qty, price)

    async def _on_book_update(updated) -> None:
        global _last_entry_at
        if not updated.is_synced or updated.is_stale:
            return
        sym      = updated.symbol
        other_ex = Exchange.BYBIT if updated.exchange == Exchange.GATE else Exchange.GATE
        other    = ob_engine.get(other_ex, sym, MarketType.PERPETUAL)
        if other is None or not other.is_synced or other.is_stale:
            return

        for buy_b, sell_b in [(updated, other), (other, updated)]:
            if buy_b.best_bid <= 0 or sell_b.best_ask <= 0:
                continue
            res = calculator.compute(buy_b, sell_b, 1_000.0)
            if res is None or res.size_usdt < 10 or res.raw_spread_bps > 500:
                continue
            key = (sym, buy_b.exchange.value, sell_b.exchange.value)
            ep  = round(res.executable_spread_bps, 2)
            now_mono = time.monotonic()
            existing = _spread_map.get(key)
            _spread_map[key] = {
                "symbol":                sym,
                "buy_exchange":          buy_b.exchange.value,
                "sell_exchange":         sell_b.exchange.value,
                "raw_spread_bps":        round(res.raw_spread_bps, 2),
                "executable_spread_bps": ep,
                "fee_cost_bps":          FEE_BPS,
                "buy_price":             buy_b.best_ask,
                "sell_price":            sell_b.best_bid,
                "created_at":            time.strftime("%Y-%m-%dT%H:%M:%S"),
                "_ts":                   now_mono,
                "_first_seen_ts":        existing["_first_seen_ts"] if existing else now_mono,
            }

            pos = _positions.get(key)

            if pos is not None:
                # ── Выход ────────────────────────────────────────────────
                entry_ep  = pos["entry_executable_bps"]
                hold_mono = now_mono - pos.get("opened_at_mono", now_mono)
                close_reason = None
                if ep < EXIT_THRESHOLD:
                    close_reason = "разворот"
                elif ep < entry_ep * DYNAMIC_EXIT_RATIO:
                    close_reason = "захват80%"
                elif hold_mono > MAX_HOLD_S:
                    close_reason = "тайм-аут"

                if close_reason:
                    del _positions[key]  # убираем сразу — не ждём ордеров

                    buy_ex  = pos["buy_exchange"]
                    sell_ex = pos["sell_exchange"]
                    cur_buy  = buy_b.best_bid if buy_b.exchange.value == buy_ex else sell_b.best_bid
                    cur_sell = sell_b.best_ask if sell_b.exchange.value == sell_ex else buy_b.best_ask

                    order_status = "ok"
                    try:
                        await _do_close_long(buy_ex,  sym, pos["buy_qty"],  cur_buy)
                        await _do_close_short(sell_ex, sym, pos["sell_qty"], cur_sell)
                    except Exception as e:
                        order_status = "err"
                        # Логируем — трейдер должен проверить позиции вручную
                        log_file = LOG_DIR / f"real_errors_{time.strftime('%Y-%m-%d')}.log"
                        with open(log_file, "a") as f:
                            f.write(f"{time.strftime('%H:%M:%S')} CLOSE ERROR {sym}: {e}\n")

                    pnl_bps  = entry_ep - ep - FEE_BPS
                    pnl_usdt = round(VIRTUAL_SIZE_USDT * pnl_bps / 10_000, 2)
                    _portfolio["realized_pnl"] += pnl_usdt
                    hold_ms = int(hold_mono * 1000)

                    trade = {
                        "symbol":       sym,
                        "entry_bps":    round(entry_ep, 2),
                        "exit_bps":     ep,
                        "pnl_bps":      round(pnl_bps, 2),
                        "pnl_usdt":     pnl_usdt,
                        "hold":         _hold_str(hold_ms),
                        "close_reason": close_reason,
                        "order_status": order_status,
                        "time":         time.strftime("%H:%M:%S"),
                    }
                    _trades.append(trade)
                    if len(_trades) > 500:
                        _trades.pop(0)
                    _log_trade(trade)

                    if close_reason == "тайм-аут":
                        _cooldown[sym] = now_mono + COOLDOWN_S

            elif not _paused:
                # ── Вход с 100мс задержкой ───────────────────────────────
                if (ep > ENTRY_THRESHOLD
                        and len(_positions) < MAX_POSITIONS
                        and sym not in _BLACKLIST
                        and _cooldown.get(sym, 0) < now_mono
                        and key not in _pending_entries
                        and now_mono - _last_entry_at >= MIN_ENTRY_INTERVAL_S):

                    spread_entry = _spread_map.get(key)
                    reaction_ms = int((now_mono - spread_entry["_first_seen_ts"]) * 1000) if spread_entry else 0
                    _pending_entries.add(key)
                    _last_entry_at = now_mono  # блокируем следующий вход на MIN_ENTRY_INTERVAL_S

                    async def _delayed_entry_real(
                        _key=key, _sym=sym,
                        _buy_ex=buy_b.exchange.value, _sell_ex=sell_b.exchange.value,
                        _reaction=reaction_ms,
                    ) -> None:
                        await asyncio.sleep(0.1)
                        _pending_entries.discard(_key)
                        if _key in _positions or len(_positions) >= MAX_POSITIONS:
                            return
                        current = _spread_map.get(_key)
                        if current is None or current["executable_spread_bps"] < ENTRY_THRESHOLD:
                            return  # спред исчез — отмена

                        actual_ep  = current["executable_spread_bps"]
                        buy_price  = current.get("buy_price",  0.0)
                        sell_price = current.get("sell_price", 0.0)

                        # Открываем лонг
                        try:
                            buy_id, buy_qty = await _do_open_long(_buy_ex, _sym, buy_price)
                        except Exception as e:
                            _cooldown[_sym] = time.monotonic() + 60
                            log_file = LOG_DIR / f"real_errors_{time.strftime('%Y-%m-%d')}.log"
                            with open(log_file, "a") as f:
                                f.write(f"{time.strftime('%H:%M:%S')} OPEN LONG ERROR {_sym}: {e}\n")
                            return

                        # Открываем шорт
                        try:
                            sell_id, sell_qty = await _do_open_short(_sell_ex, _sym, sell_price)
                        except Exception as e:
                            _cooldown[_sym] = time.monotonic() + 60
                            log_file = LOG_DIR / f"real_errors_{time.strftime('%Y-%m-%d')}.log"
                            with open(log_file, "a") as f:
                                f.write(f"{time.strftime('%H:%M:%S')} OPEN SHORT ERROR {_sym}: {e}\n")
                            # Закрываем уже открытый лонг
                            try:
                                await _do_close_long(_buy_ex, _sym, buy_qty, buy_price)
                            except Exception:
                                pass
                            return

                        _positions[_key] = {
                            "symbol":               _sym,
                            "buy_exchange":         _buy_ex,
                            "sell_exchange":        _sell_ex,
                            "entry_executable_bps": actual_ep,
                            "entry_buy_price":      buy_price,
                            "entry_sell_price":     sell_price,
                            "opened_at":            time.time(),
                            "opened_at_mono":       time.monotonic(),
                            "reaction_ms":          _reaction,
                            "buy_order_id":         buy_id,
                            "sell_order_id":        sell_id,
                            "buy_qty":              buy_qty,
                            "sell_qty":             sell_qty,
                        }

                    asyncio.create_task(_delayed_entry_real())

    ob_engine.on_update(_on_book_update)

    async def _load_bybit_instruments() -> None:
        """Загружает qtyStep для всех linear-perpetuals чтобы правильно округлять qty."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                cursor = ""
                while True:
                    url = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"
                    if cursor:
                        url += f"&cursor={cursor}"
                    async with s.get(url) as r:
                        data = await r.json(content_type=None)
                    result = data.get("result", {})
                    for item in result.get("list", []):
                        sym  = item.get("symbol", "")
                        step = float(item.get("lotSizeFilter", {}).get("qtyStep", 1) or 1)
                        _bybit_qty_steps[sym] = step
                    cursor = result.get("nextPageCursor", "")
                    if not cursor:
                        break
        except Exception as e:
            log_file = LOG_DIR / f"real_errors_{time.strftime('%Y-%m-%d')}.log"
            with open(log_file, "a") as f:
                f.write(f"{time.strftime('%H:%M:%S')} Bybit instruments load error: {e}\n")

    async def _fetch_exchange_balances() -> None:
        by_key   = bybit_creds.api_key
        by_sec   = bybit_creds.api_secret
        gate_key = gate_creds.api_key
        gate_sec = gate_creds.api_secret
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            if by_key and by_sec:
                for acct in ("UNIFIED", "CONTRACT"):
                    try:
                        ts = str(int(time.time() * 1000))
                        rw = "5000"
                        qs = f"accountType={acct}&coin=USDT"
                        sig = hmac.new(by_sec.encode(), (ts + by_key + rw + qs).encode(), hashlib.sha256).hexdigest()
                        hdrs = {"X-BAPI-API-KEY": by_key, "X-BAPI-TIMESTAMP": ts,
                                "X-BAPI-SIGN": sig, "X-BAPI-RECV-WINDOW": rw}
                        async with s.get(f"https://api.bybit.com/v5/account/wallet-balance?{qs}", headers=hdrs) as r:
                            data = await r.json(content_type=None)
                        for acc in data.get("result", {}).get("list", []):
                            for coin in acc.get("coin", []):
                                if coin.get("coin") == "USDT":
                                    val = float(coin.get("walletBalance", 0) or 0)
                                    if val >= 0:
                                        _stats["bybit_usdt"] = val
                                        if _stats["bybit_usdt_start"] < 0:
                                            _stats["bybit_usdt_start"] = val
                                        break
                        if _stats["bybit_usdt"] >= 0:
                            break
                    except Exception:
                        pass
            if gate_key and gate_sec:
                try:
                    ts        = str(int(time.time()))
                    path      = "/api/v4/futures/usdt/accounts"
                    body_hash = hashlib.sha512(b"").hexdigest()
                    msg       = f"GET\n{path}\n\n{body_hash}\n{ts}"
                    sig       = hmac.new(gate_sec.encode(), msg.encode(), hashlib.sha512).hexdigest()
                    async with s.get(
                        f"https://api.gateio.ws{path}",
                        headers={"KEY": gate_key, "SIGN": sig, "Timestamp": ts, "Accept": "application/json"},
                    ) as r:
                        data = await r.json(content_type=None)
                    val = float(data.get("available") or 0)
                    _stats["gate_usdt"] = val
                    if _stats["gate_usdt_start"] < 0:
                        _stats["gate_usdt_start"] = val
                except Exception:
                    pass

    async def _stats_loop() -> None:
        _bal_fetch_t  = [0.0]
        _cfg_reload_t = [0.0]
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()

            if now - _cfg_reload_t[0] > 5:
                _cfg_reload_t[0] = now
                _reload_config()

            stale_keys = [k for k, v in _spread_map.items() if now - v.get("_ts", 0) > 10]
            for k in stale_keys:
                del _spread_map[k]

            _stats["bybit_connected"] = bybit.health.ws_connected
            _stats["gate_connected"]  = gate.health.ws_connected

            _portfolio["unrealized_pnl"] = round(sum(
                VIRTUAL_SIZE_USDT * (pos["entry_executable_bps"] - _spread_map[k]["executable_spread_bps"]) / 10_000
                for k, pos in _positions.items() if k in _spread_map
            ), 2)

            if now - _bal_fetch_t[0] > 60:
                _bal_fetch_t[0] = now
                asyncio.create_task(_fetch_exchange_balances())

            # Авто-пауза если баланс ещё не загружен или недостаточен
            global _paused, _auto_paused
            bybit_ok = _stats["bybit_usdt"] >= VIRTUAL_SIZE_USDT
            gate_ok  = _stats["gate_usdt"]  >= VIRTUAL_SIZE_USDT
            if not (bybit_ok and gate_ok):
                if not _paused:
                    _paused = True
                    _auto_paused = True
            else:
                if _auto_paused:
                    _paused = False
                    _auto_paused = False

    await _load_bybit_instruments()
    await bybit.connect()
    await gate.connect()

    for sym in symbols:
        await bybit.subscribe_orderbook(sym, MarketType.PERPETUAL)
        await gate.subscribe_orderbook(sym, MarketType.PERPETUAL)

    await _stats_loop()


# ── Keyboard listener ─────────────────────────────────────────────────────

async def _key_task() -> None:
    try:
        import select as _select
        import signal
        import termios
        import tty
    except ImportError:
        return

    global _trades_page, _paused
    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            await asyncio.sleep(0.05)
            if not _select.select([sys.stdin], [], [], 0)[0]:
                continue
            ch = sys.stdin.buffer.read(1)
            if ch == b"\x1b":
                await asyncio.sleep(0.01)
                if not _select.select([sys.stdin], [], [], 0)[0]:
                    continue
                ch2 = sys.stdin.buffer.read(1)
                if ch2 != b"[":
                    continue
                if not _select.select([sys.stdin], [], [], 0)[0]:
                    continue
                ch3 = sys.stdin.buffer.read(1)
                if ch3 in (b"5", b"6"):
                    if _select.select([sys.stdin], [], [], 0)[0]:
                        sys.stdin.buffer.read(1)
                    n_pages = max(1, (len(_trades) + TRADES_PER_PAGE - 1) // TRADES_PER_PAGE)
                    if ch3 == b"6":
                        _trades_page = (_trades_page + 1) % n_pages
                    elif ch3 == b"5":
                        _trades_page = (_trades_page - 1) % n_pages
            elif ch in (b"p", b"P"):
                _auto_paused = False
                _paused = not _paused
            elif ch in (b"q", b"Q", b"\x03"):
                import os as _os
                _os.kill(_os.getpid(), signal.SIGINT)
                break
    except Exception:
        pass
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass


# ── Entry point ───────────────────────────────────────────────────────────

async def main() -> None:
    logging.disable(logging.WARNING)
    from core.logging import setup_logging
    setup_logging(level="ERROR", json_output=False)

    from run import fetch_futures_symbols
    symbols = await fetch_futures_symbols(500)
    _stats["pairs"] = len(symbols)

    bot_task = asyncio.create_task(bot_main_real(symbols))
    asyncio.create_task(_key_task())

    console = Console()
    with Live(build_ui(), console=console, refresh_per_second=2, screen=True) as live:
        try:
            while True:
                live.update(build_ui())
                await asyncio.sleep(0.5)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            bot_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
