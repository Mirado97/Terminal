"""
Сравнение спредов Bybit vs Bitget / OKX / HTX.
Показывает какая биржа даёт больше возможностей для арбитража с Bybit.
Не нужны API ключи — только публичные REST.
"""
import asyncio
import statistics
import aiohttp


async def get_bybit(s: aiohttp.ClientSession) -> dict[str, float]:
    async with s.get("https://api.bybit.com/v5/market/tickers?category=linear") as r:
        data = await r.json(content_type=None)
    return {
        t["symbol"]: (float(t["bid1Price"]) + float(t["ask1Price"])) / 2
        for t in data.get("result", {}).get("list", [])
        if t["symbol"].endswith("USDT")
        and float(t.get("bid1Price") or 0) > 0
        and float(t.get("ask1Price") or 0) > 0
    }


async def get_bitget(s: aiohttp.ClientSession) -> dict[str, float]:
    async with s.get(
        "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES"
    ) as r:
        data = await r.json(content_type=None)
    result = {}
    for t in data.get("data", []):
        sym = t.get("symbol", "").replace("_UMCBL", "").replace("USDT_UMCBL", "USDT")
        # Bitget: symbol = "BTCUSDT" уже в нужном формате в v2
        bid = float(t.get("bidPr") or t.get("bestBid") or 0)
        ask = float(t.get("askPr") or t.get("bestAsk") or 0)
        if sym.endswith("USDT") and bid > 0 and ask > 0:
            result[sym] = (bid + ask) / 2
    return result


async def get_okx(s: aiohttp.ClientSession) -> dict[str, float]:
    async with s.get("https://www.okx.com/api/v5/market/tickers?instType=SWAP") as r:
        data = await r.json(content_type=None)
    result = {}
    for t in data.get("data", []):
        inst = t.get("instId", "")           # BTC-USDT-SWAP
        if not inst.endswith("-USDT-SWAP"):
            continue
        sym = inst.replace("-USDT-SWAP", "USDT")  # BTCUSDT
        bid = float(t.get("bidPx") or 0)
        ask = float(t.get("askPx") or 0)
        if bid > 0 and ask > 0:
            result[sym] = (bid + ask) / 2
    return result


async def get_htx(s: aiohttp.ClientSession) -> dict[str, float]:
    async with s.get(
        "https://api.hbdm.com/linear-swap-ex/market/detail/batch_merged?contract_code=all"
    ) as r:
        data = await r.json(content_type=None)
    result = {}
    for item in data.get("ticks", []):
        code = item.get("contract_code", "")  # BTC-USDT
        if not code.endswith("-USDT"):
            continue
        sym = code.replace("-", "")  # BTCUSDT
        bid = float(item.get("bid", [0])[0] if item.get("bid") else 0)
        ask = float(item.get("ask", [0])[0] if item.get("ask") else 0)
        if bid > 0 and ask > 0:
            result[sym] = (bid + ask) / 2
    return result


def stats(spreads: list[float], label: str, n_common: int) -> None:
    if not spreads:
        print(f"  {label}: нет данных")
        return
    spreads_s = sorted(spreads)
    above14 = sum(1 for x in spreads if x > 14)
    above20 = sum(1 for x in spreads if x > 20)
    above30 = sum(1 for x in spreads if x > 30)
    print(f"\n  ── {label} ({n_common} пар) ──")
    print(f"  Медиана спреда:    {statistics.median(spreads_s):6.2f} bps")
    print(f"  Среднее:           {statistics.mean(spreads_s):6.2f} bps")
    print(f"  90-й перцентиль:   {spreads_s[int(len(spreads_s)*0.9)]:6.2f} bps")
    print(f"  Макс:              {spreads_s[-1]:6.2f} bps")
    print(f"  Пар >14 bps:       {above14} ({above14/len(spreads)*100:.0f}%)")
    print(f"  Пар >20 bps:       {above20} ({above20/len(spreads)*100:.0f}%)")
    print(f"  Пар >30 bps:       {above30} ({above30/len(spreads)*100:.0f}%)")

    # Топ-10 пар
    top = sorted(zip(spreads, [""]*len(spreads)), reverse=True)[:10]
    print(f"  Топ пары по спреду:")


def top_pairs(bybit: dict, other: dict, label: str) -> None:
    common = {s for s in bybit if s in other}
    pairs = []
    for s in common:
        spread = abs(bybit[s] - other[s]) / bybit[s] * 10_000
        pairs.append((spread, s))
    pairs.sort(reverse=True)
    print(f"\n  Топ-15 пар {label}:")
    for bps, sym in pairs[:15]:
        print(f"    {sym:20s}  {bps:7.2f} bps")


async def main() -> None:
    print("Загружаю цены со всех бирж...\n")
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15),
        headers={"User-Agent": "Mozilla/5.0"},
    ) as s:
        bybit, bitget, okx, htx = await asyncio.gather(
            get_bybit(s), get_bitget(s), get_okx(s), get_htx(s),
            return_exceptions=True,
        )

    if isinstance(bybit, Exception):
        print(f"Bybit ошибка: {bybit}")
        return

    print(f"Bybit: {len(bybit)} пар")
    print(f"Bitget: {len(bitget) if not isinstance(bitget, Exception) else f'ОШИБКА: {bitget}'}")
    print(f"OKX:   {len(okx)   if not isinstance(okx,   Exception) else f'ОШИБКА: {okx}'}")
    print(f"HTX:   {len(htx)   if not isinstance(htx,   Exception) else f'ОШИБКА: {htx}'}")

    print("\n" + "="*55)

    for label, other in [("Bitget", bitget), ("OKX", okx), ("HTX", htx)]:
        if isinstance(other, Exception):
            print(f"\n{label}: ошибка — {other}")
            continue
        common = {s for s in bybit if s in other}
        spreads = [abs(bybit[s] - other[s]) / bybit[s] * 10_000 for s in common]
        stats(spreads, label, len(common))
        top_pairs(bybit, other, label)

    print("\n" + "="*55)
    print("Вывод: биржа с большей медианой и больше пар >14 bps — лучший кандидат.")


asyncio.run(main())
