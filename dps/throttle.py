"""Обмеження частоти: не частіше 2 запитів на секунду до сервера ДПС."""

from __future__ import annotations

import time
from typing import Callable

MAX_ALLOWED_RPS = 2.0


class RateLimiter:
    def __init__(
        self,
        rate_per_sec: float = MAX_ALLOWED_RPS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("Частота має бути додатною")
        if rate_per_sec > MAX_ALLOWED_RPS:
            raise ValueError(f"Частота {rate_per_sec} запитів/с перевищує дозволену ({MAX_ALLOWED_RPS})")
        self._min_interval = 1.0 / rate_per_sec
        self._clock = clock
        self._sleep = sleep
        self._next_allowed = 0.0

    def wait(self) -> None:
        now = self._clock()
        if now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self._min_interval
