"""Валидация checksum стакана (Bybit V5 CRC32)."""
from __future__ import annotations

import zlib

from core.models import Price


def bybit_checksum(bids: list[Price], asks: list[Price], n: int = 25) -> int:
    """
    Bybit V5 orderbook checksum.

    Алгоритм (из Bybit API docs):
    - Берём top-N bids (desc) и top-N asks (asc)
    - Чередуем уровни: bid[0], ask[0], bid[1], ask[1], ...
    - Каждый уровень: "price:qty"
    - Соединяем символом ":"
    - CRC32 результирующей строки → знаковый int32

    Примечание: точный формат числа (кол-во знаков после запятой)
    должен совпадать с тем, что присылает биржа. Строки price/qty
    передаются как есть из WS — не конвертируем в float и обратно.
    Для корректной работы необходимо хранить оригинальные строки.
    Текущая реализация использует float repr — подходит для проверки
    логики, точную валидацию проверяем на live данных.
    """
    bid_levels = sorted(bids, key=lambda p: p.price, reverse=True)[:n]
    ask_levels = sorted(asks, key=lambda p: p.price)[:n]

    parts: list[str] = []
    max_i = max(len(bid_levels), len(ask_levels))
    for i in range(max_i):
        if i < len(bid_levels):
            b = bid_levels[i]
            parts.append(f"{b.price}:{b.size}")
        if i < len(ask_levels):
            a = ask_levels[i]
            parts.append(f"{a.price}:{a.size}")

    raw = ":".join(parts).encode("ascii")
    crc = zlib.crc32(raw) & 0xFFFFFFFF
    # Bybit возвращает знаковый int32
    return crc if crc < 2**31 else crc - 2**32
