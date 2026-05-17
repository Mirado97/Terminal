"""
Bybit адаптер для реальной торговли.
Идентичен adapter.py — place_order() уже реализован через BybitRestClient.
Создан как отдельный файл для симметрии с mexc/futures_adapter_real.py.
"""
from exchanges.bybit.adapter import BybitAdapter as BybitAdapterReal  # noqa: F401
