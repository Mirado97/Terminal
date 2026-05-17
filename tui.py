"""
tui.py — Terminal UI для арбитражного бота.
Запуск: python3 tui.py
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
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
    "profitable":      0,
    "best_spread":     0.0,
    "best_symbol":     "—",
    "bybit_connected": False,
    "mexc_connected":  False,
    "start_time":      time.time(),
    "mx_balance":      -1.0,   # MX токен MEXC (для оплаты комиссий)
}
_paused: bool = False

FEE_BPS = 12.2

# ── Virtual trading ───────────────────────────────────────────────────────
_trades: list[dict] = []
_positions: dict[tuple, dict] = {}
_portfolio  = {"balance": 300.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0}
_cooldown: dict[str, float] = {}       # symbol → monotonic time когда кулдаун истекает
_pending_entries: set[tuple] = set()   # ключи с задержанным входом (100мс)
_trades_page = 0

COOLDOWN_S = 1800   # 30 минут после тайм-аут закрытия

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def _log_trade(trade: dict) -> None:
    log_file = LOG_DIR / f"trades_{time.strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(trade, ensure_ascii=False) + "\n")

VIRTUAL_SIZE_USDT  = 50.0   # размер позиции на сторону (лонг $50 + шорт $50 = $100)
ENTRY_THRESHOLD    = 15.0   # bps executable — порог входа
EXIT_THRESHOLD     = -2.0   # bps executable — порог выхода (спред развернулся)
DYNAMIC_EXIT_RATIO = 0.20   # выход когда осталось ≤20% от входного спреда (захвачено 80%)
MAX_HOLD_S         = 60     # принудительный выход через 1 минуту
MAX_POSITIONS      = 3
TRADES_PER_PAGE    = 10


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


def _pnl_str(val: float, decimals: int = 4) -> str:
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
    mexc_s  = _conn(_stats["mexc_connected"])
    best    = f"[green]+{_stats['best_spread']:.2f}bps[/] {_stats['best_symbol']}" \
              if _stats["best_spread"] > 0 else "[dim]нет[/]"
    rpnl = _portfolio["realized_pnl"]
    upnl = _portfolio["unrealized_pnl"]
    bal  = _portfolio["balance"]
    mx   = _stats["mx_balance"]
    mx_str = f"[green]{mx:.1f}[/]" if mx >= 10 else (f"[red]{mx:.1f}[/]" if mx >= 0 else "[dim]N/A[/]")
    pause_str = "  [bold red blink]⏸ ПАУЗА[/]" if _paused else ""

    layout["header"].update(Panel(
        Text.from_markup(
            f"[bold cyan]◈ ARBITRAGE TERMINAL[/]  Bybit: {bybit_s}  MEXC: {mexc_s}  │  "
            f"Пар: [yellow]{_stats['pairs']}[/]  Прибыльных: [green]{_stats['profitable']}[/]  "
            f"Лучший: {best}  Up: [dim]{_uptime()}[/]  │  "
            f"MX: {mx_str}  Виртуал: [yellow]${bal:.2f}[/]  R:{_pnl_str(rpnl, 4)}  U:{_pnl_str(upnl, 4)}"
            f"{pause_str}"
        ),
        style="on grey7",
    ))

    # ── Открытые позиции (полная ширина) ─────────────────────────────────
    pos_tbl = Table(
        box=box.SIMPLE_HEAVY, header_style="bold white on grey15",
        expand=True, show_edge=False,
    )
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
        cur_buy_price  = current.get("buy_price",  pos.get("entry_buy_price",  0.0)) if current else pos.get("entry_buy_price", 0.0)
        cur_sell_price = current.get("sell_price", pos.get("entry_sell_price", 0.0)) if current else pos.get("entry_sell_price", 0.0)
        unreal  = VIRTUAL_SIZE_USDT * (pos["entry_executable_bps"] - cur_ep) / 10_000
        hold_s  = int(time.time() - pos["opened_at"])
        hold_str = f"{hold_s//60}м{hold_s%60:02d}с"

        if cur_ep > ENTRY_THRESHOLD:
            ep_now = f"[green]{cur_ep:+.2f}[/]"
        elif cur_ep > 0:
            ep_now = f"[yellow]{cur_ep:+.2f}[/]"
        else:
            ep_now = f"[red]{cur_ep:+.2f}[/]"

        pos_tbl.add_row(
            pos["symbol"],
            pos["buy_exchange"].upper()[:5],
            f"{cur_buy_price:.4f}" if cur_buy_price else "—",
            pos["sell_exchange"].upper()[:5],
            f"{cur_sell_price:.4f}" if cur_sell_price else "—",
            f"+{pos['entry_executable_bps']:.2f}",
            ep_now,
            hold_str,
            _pnl_str(unreal, 4),
        )

    layout["positions"].update(Panel(
        pos_tbl,
        title=f"[bold]Открытые позиции[/]  {len(_positions)}/{MAX_POSITIONS}  │  порог входа >{ENTRY_THRESHOLD:.0f}bps  выход <{EXIT_THRESHOLD:.0f}bps  ${VIRTUAL_SIZE_USDT:.0f}/сторону",
    ))

    # ── История сделок (полная ширина) ───────────────────────────────────
    hist_tbl = Table(
        box=box.SIMPLE_HEAVY, header_style="bold white on grey15",
        expand=True, show_edge=False,
    )
    hist_tbl.add_column("Символ",    style="cyan",    width=12)
    hist_tbl.add_column("Вход bps",  justify="right", width=9)
    hist_tbl.add_column("Выход bps", justify="right", width=10)
    hist_tbl.add_column("P&L bps",   justify="right", width=9)
    hist_tbl.add_column("P&L $",     justify="right", width=9)
    hist_tbl.add_column("Причина",   width=10)
    hist_tbl.add_column("Реакция",   justify="right", width=8)
    hist_tbl.add_column("Держал",    justify="right", width=8)
    hist_tbl.add_column("Закрыт",    width=8)

    trades_rev = list(reversed(_trades))
    n_pages    = max(1, (len(trades_rev) + TRADES_PER_PAGE - 1) // TRADES_PER_PAGE)
    page       = _trades_page % n_pages
    page_slice = trades_rev[page * TRADES_PER_PAGE : (page + 1) * TRADES_PER_PAGE]

    for t in page_slice:
        r_ms = t.get("reaction_ms", 0)
        react_str = f"[dim]{_hold_str(r_ms)}[/]" if r_ms < 500 else f"[yellow]{_hold_str(r_ms)}[/]"
        reason = t.get("close_reason", "")
        if reason == "разворот":
            reason_str = "[cyan]разворот[/]"
        elif reason == "захват80%":
            reason_str = "[green]захват80%[/]"
        elif reason == "тайм-аут":
            reason_str = "[yellow]тайм-аут[/]"
        else:
            reason_str = f"[dim]{reason}[/]"
        hist_tbl.add_row(
            t["symbol"],
            f"+{t['entry_bps']:.2f}",
            f"{t['exit_bps']:+.2f}",
            _pnl_str(t["pnl_bps"], 2),
            _pnl_str(t["pnl_usdt"], 4),
            reason_str,
            react_str,
            t["hold"],
            t["time"],
        )

    layout["history"].update(Panel(
        hist_tbl,
        title="[bold]История виртуальных сделок[/]",
        subtitle=f"[dim]стр. {page + 1}/{n_pages}  PgUp / PgDn для переключения[/]",
    ))

    # ── Bybit Linear ↔ MEXC Futures (топ 5, полная ширина) ──────────────
    tbl = Table(
        box=box.SIMPLE_HEAVY, header_style="bold white on grey15",
        expand=True, show_edge=False,
    )
    tbl.add_column("Символ",    style="cyan",    width=14)
    tbl.add_column("Лонг",      width=7)
    tbl.add_column("Шорт",      width=7)
    tbl.add_column("Спред",     justify="right", width=9)
    tbl.add_column("Прибыль",   justify="right", width=11)
    tbl.add_column("Обновлено", width=10)

    rows = sorted(
        (v for v in _spread_map.values() if v["symbol"] not in _BLACKLIST),
        key=lambda x: x["executable_spread_bps"],
        reverse=True,
    )[:5]

    for row in rows:
        ep  = row["executable_spread_bps"]
        raw = row["raw_spread_bps"]
        if ep > 0:
            ep_str  = f"[bold green]+{ep:.2f} bps[/]"
            raw_str = f"[green]{raw:+.2f}[/]"
        elif ep > -5:
            ep_str  = f"[yellow]{ep:.2f} bps[/]"
            raw_str = f"[yellow]{raw:+.2f}[/]"
        else:
            ep_str  = f"[dim]{ep:.2f} bps[/]"
            raw_str = f"[dim]{raw:+.2f}[/]"
        tbl.add_row(
            row["symbol"],
            row["buy_exchange"].upper()[:5],
            row["sell_exchange"].upper()[:5],
            raw_str, ep_str,
            row["created_at"][11:19],
        )

    layout["spreads"].update(Panel(
        tbl,
        title="[bold]Bybit Linear  ↔  MEXC Futures  │  x1 leverage[/]",
        subtitle=f"[dim]порог прибыли >{FEE_BPS} bps  │  топ 5 из {len(_spread_map)}[/]",
    ))

    # ── Footer ───────────────────────────────────────────────────────────
    layout["footer"].update(
        Text.from_markup(
            "  [dim]q / Ctrl+C — выход   │  P — пауза/старт бота   │  "
            "Зелёный = прибыльный спред после комиссий[/]"
        )
    )

    return layout


# ── Bot logic ─────────────────────────────────────────────────────────────

async def bot_main(symbols: list[str]) -> None:
    from core.models import Exchange, MarketType
    from exchanges.bybit.adapter import BybitAdapter
    from exchanges.mexc.futures_adapter import MexcFuturesAdapter
    from orderbook.engine import OrderBookEngine
    from spread.calculator import SpreadCalculator
    from spread.fees import FeeSchedule, FeeTable
    from credentials.manager import ExchangeCredentials

    bybit_creds = ExchangeCredentials(
        api_key    = os.environ.get("BYBIT_API_KEY", ""),
        api_secret = os.environ.get("BYBIT_API_SECRET", ""),
    )
    mexc_creds = ExchangeCredentials(
        api_key    = os.environ.get("MEXC_API_KEY", ""),
        api_secret = os.environ.get("MEXC_API_SECRET", ""),
    )

    bybit_cfg = {"testnet": False, "rate_limit": {"requests_per_second": 10, "orders_per_second": 5}}
    bybit = BybitAdapter(config=bybit_cfg, credentials=bybit_creds)
    mexc  = MexcFuturesAdapter(credentials=mexc_creds)

    ob_engine = OrderBookEngine(validate_checksum=False)
    bybit.on_orderbook(ob_engine.handle)
    mexc.on_orderbook(ob_engine.handle)

    fee_table = FeeTable(overrides={
        (Exchange.MEXC,  MarketType.PERPETUAL): FeeSchedule(maker_bps=0.8,  taker_bps=3.2),
        (Exchange.BYBIT, MarketType.PERPETUAL): FeeSchedule(maker_bps=3.24, taker_bps=9.0),
    })
    calculator = SpreadCalculator(fee_table=fee_table, latency_us=10_000)

    # Мгновенный обработчик каждого тикера: пересчёт спреда + вход/выход виртуала
    async def _on_book_update(updated) -> None:
        if not updated.is_synced or updated.is_stale:
            return
        sym   = updated.symbol
        mtype = updated.market_type
        other_ex = Exchange.BYBIT if updated.exchange == Exchange.MEXC else Exchange.MEXC
        other    = ob_engine.get(other_ex, sym, mtype)
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

            # ── Виртуальная торговля: решение мгновенно на каждом тикере ──
            pos = _positions.get(key)

            if pos is not None:
                # Проверяем выход — три условия
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
                    pnl_bps  = entry_ep - ep - FEE_BPS   # entry_ep уже нет вход.комиссий, вычитаем выход
                    pnl_usdt = round(VIRTUAL_SIZE_USDT * pnl_bps / 10_000, 4)
                    _portfolio["realized_pnl"] += pnl_usdt
                    _portfolio["balance"]      += pnl_usdt
                    hold_ms = int(hold_mono * 1000)
                    _trades.append({
                        "symbol":       sym,
                        "entry_bps":    round(entry_ep, 2),
                        "exit_bps":     ep,
                        "pnl_bps":      round(pnl_bps, 2),
                        "pnl_usdt":     pnl_usdt,
                        "hold":         _hold_str(hold_ms),
                        "reaction_ms":  pos.get("reaction_ms", 0),
                        "close_reason": close_reason,
                        "time":         time.strftime("%H:%M:%S"),
                    })
                    if len(_trades) > 500:
                        _trades.pop(0)
                    _log_trade(_trades[-1])
                    if close_reason == "тайм-аут":
                        _cooldown[sym] = now_mono + COOLDOWN_S
                    del _positions[key]
            elif not _paused:
                # Проверяем вход — с задержкой 100мс (симуляция исполнения + нога-риск)
                if (ep > ENTRY_THRESHOLD
                        and len(_positions) < MAX_POSITIONS
                        and sym not in _BLACKLIST
                        and _cooldown.get(sym, 0) < now_mono
                        and key not in _pending_entries):
                    spread_entry = _spread_map.get(key)
                    reaction_ms = int((now_mono - spread_entry["_first_seen_ts"]) * 1000) if spread_entry else 0
                    _pending_entries.add(key)

                    async def _delayed_entry(
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
                            return  # спред исчез — нога не заполнилась
                        actual_ep = current["executable_spread_bps"]
                        _positions[_key] = {
                            "symbol":               _sym,
                            "buy_exchange":         _buy_ex,
                            "sell_exchange":        _sell_ex,
                            "entry_executable_bps": actual_ep,
                            "entry_buy_price":      current.get("buy_price", 0.0),
                            "entry_sell_price":     current.get("sell_price", 0.0),
                            "opened_at":            time.time(),
                            "opened_at_mono":       time.monotonic(),
                            "reaction_ms":          _reaction,
                        }

                    asyncio.create_task(_delayed_entry())

    ob_engine.on_update(_on_book_update)

    async def _fetch_mx_balance() -> None:
        api_key    = mexc_creds.api_key
        api_secret = mexc_creds.api_secret
        if not api_key or not api_secret:
            return
        total = 0.0
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            # 1) Спотовый аккаунт
            try:
                ts     = str(int(time.time() * 1000))
                params = f"timestamp={ts}"
                sig    = hmac.new(api_secret.encode(), params.encode(), hashlib.sha256).hexdigest()
                url    = f"https://api.mexc.com/api/v3/account?{params}&signature={sig}"
                async with s.get(url, headers={"X-MEXC-APIKEY": api_key}) as r:
                    data = await r.json(content_type=None)
                for b in data.get("balances", []):
                    if b["asset"] == "MX":
                        total += float(b.get("free", 0)) + float(b.get("locked", 0))
                        break
            except Exception:
                pass
            # 2) Фьючерсный (contract) аккаунт
            try:
                ts       = str(int(time.time() * 1000))
                sign_src = api_key + ts + ""
                sig      = hmac.new(api_secret.encode(), sign_src.encode(), hashlib.sha256).hexdigest()
                headers  = {"ApiKey": api_key, "Request-Time": ts, "Signature": sig}
                async with s.get("https://contract.mexc.com/api/v1/private/account/assets", headers=headers) as r:
                    data = await r.json(content_type=None)
                for a in data.get("data", []):
                    if a.get("currency", "").upper() == "MX":
                        total += float(a.get("availableBalance", 0)) + float(a.get("frozenBalance", 0))
                        break
            except Exception:
                pass
        _stats["mx_balance"] = total

    # Лёгкий цикл: обновляет статистику и чистит устаревшие записи из карты
    async def _stats_loop() -> None:
        _mx_fetch_t = [0.0]
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            stale_keys = [k for k, v in _spread_map.items() if now - v.get("_ts", 0) > 10]
            for k in stale_keys:
                del _spread_map[k]

            profitable = [v for v in _spread_map.values() if v["executable_spread_bps"] > 0]
            _stats["profitable"]      = len(profitable)
            _stats["bybit_connected"] = bybit.health.ws_connected
            _stats["mexc_connected"]  = mexc.health.ws_connected
            if profitable:
                best = max(profitable, key=lambda x: x["executable_spread_bps"])
                _stats["best_spread"] = best["executable_spread_bps"]
                _stats["best_symbol"] = best["symbol"]

            # Нереализованный PnL
            _portfolio["unrealized_pnl"] = round(sum(
                VIRTUAL_SIZE_USDT * (pos["entry_executable_bps"] - _spread_map[k]["executable_spread_bps"]) / 10_000
                for k, pos in _positions.items() if k in _spread_map
            ), 4)

            # MX баланс раз в 60 секунд
            if now - _mx_fetch_t[0] > 60:
                _mx_fetch_t[0] = now
                asyncio.create_task(_fetch_mx_balance())

    await bybit.connect()
    await mexc.connect()

    for sym in symbols:
        await bybit.subscribe_orderbook(sym, MarketType.PERPETUAL)
        await mexc.subscribe_orderbook(sym, MarketType.PERPETUAL)

    await _stats_loop()


# ── Keyboard listener (asyncio, Linux) ───────────────────────────────────

async def _key_task() -> None:
    """Asyncio корутина: PgUp/PgDn переключают страницы истории."""
    try:
        import select as _select
        import signal
        import termios
        import tty
    except ImportError:
        return  # Windows — пропускаем

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
                        sys.stdin.buffer.read(1)  # consume ~
                    n_pages = max(1, (len(_trades) + TRADES_PER_PAGE - 1) // TRADES_PER_PAGE)
                    if ch3 == b"6":    # PageDown
                        _trades_page = (_trades_page + 1) % n_pages
                    elif ch3 == b"5": # PageUp
                        _trades_page = (_trades_page - 1) % n_pages
            elif ch in (b"p", b"P"):
                _paused = not _paused
            elif ch in (b"q", b"Q", b"\x03"):
                os.kill(os.getpid(), signal.SIGINT)
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

    bot_task = asyncio.create_task(bot_main(symbols))

    asyncio.create_task(_key_task())

    console = Console()
    with Live(
        build_ui(),
        console=console,
        refresh_per_second=2,
        screen=True,
    ) as live:
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
