"""LatencyModel: симуляция задержки исполнения."""
from __future__ import annotations

import math
import random

from backtesting.models import BacktestConfig, LatencyModelType


class LatencyModel:
    """
    Генерирует случайную задержку исполнения (мс).

    Три распределения:
    - FIXED     — детерминированная задержка (mean_ms)
    - NORMAL    — N(mean_ms, std_ms), clamp > 0
    - LOGNORMAL — логарифмически нормальное: mu/sigma из mean_ms/std_ms
                  хвосты длиннее → реалистичнее для сетевых задержек

    После генерации задержка используется двумя способами:
    1. Для проверки, не "устарел" ли спред к моменту исполнения
       (спред может закрыться за это время — decay_rate)
    2. Для расчёта latency cost в bps (drift_bps_per_ms)
    """

    DRIFT_BPS_PER_MS = 0.1  # ценовой дрейф за 1 мс задержки

    def __init__(self, config: BacktestConfig) -> None:
        self._model = config.latency_model
        self._mean = config.latency_mean_ms
        self._std  = config.latency_std_ms

        # Параметры lognormal из mean/std
        if config.latency_std_ms > 0 and config.latency_mean_ms > 0:
            variance = config.latency_std_ms ** 2
            mean2    = config.latency_mean_ms ** 2
            self._ln_sigma = math.sqrt(math.log(1 + variance / mean2))
            self._ln_mu    = math.log(config.latency_mean_ms) - self._ln_sigma ** 2 / 2
        else:
            self._ln_sigma = 0.0
            self._ln_mu    = math.log(max(config.latency_mean_ms, 1.0))

    def sample(self) -> float:
        """Вернуть симулированную задержку в мс (всегда > 0)."""
        if self._model == LatencyModelType.FIXED:
            return self._mean

        if self._model == LatencyModelType.NORMAL:
            v = random.gauss(self._mean, self._std)
            return max(v, 0.1)

        # LOGNORMAL
        return math.exp(random.gauss(self._ln_mu, self._ln_sigma))

    def latency_cost_bps(self, latency_ms: float) -> float:
        """Стоимость задержки в bps (ценовой дрейф)."""
        return latency_ms * self.DRIFT_BPS_PER_MS
