"""Harici servis kesintilerine karşı dayanıklılık: tekrar deneme + devre kesici.

Bir servis art arda ``failure_threshold`` kez hata verirse devre açılır ve
``cooldown_sec`` boyunca o servise çağrı yapılmaz (``ServiceUnavailable``).
Böylece API kesintisinde sistem güvenli biçimde durur, kredi/kota harcamaz.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, TypeVar

from sepetfy.app.errors import ExternalServiceError, ServiceUnavailable

T = TypeVar("T")
log = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(self, service: str, failure_threshold: int = 5, cooldown_sec: float = 300,
                 clock: Callable[[], float] = time.monotonic):
        self.service = service
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if self._clock() - self._opened_at >= self.cooldown_sec:
                # yarı açık: bir denemeye izin ver
                self._opened_at = None
                self._failures = self.failure_threshold - 1
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold and self._opened_at is None:
                self._opened_at = self._clock()
                log.error("Devre kesici AÇILDI: %s (%d ardışık hata)", self.service, self._failures)

    def call(self, fn: Callable[[], T], *, retries: int = 2, backoff_sec: float = 1.0,
             sleep: Callable[[float], None] = time.sleep) -> T:
        if self.is_open:
            raise ServiceUnavailable(self.service)
        attempt = 0
        while True:
            try:
                result = fn()
            except ExternalServiceError as exc:
                self.record_failure()
                if not exc.retryable or attempt >= retries or self.is_open:
                    raise
                delay = backoff_sec * (2 ** attempt)
                log.warning("%s hatası, %.1fs sonra tekrar denenecek: %s", self.service, delay, exc)
                sleep(delay)
                attempt += 1
            else:
                self.record_success()
                return result


_breakers: dict[str, CircuitBreaker] = {}


def breaker_for(service: str) -> CircuitBreaker:
    if service not in _breakers:
        _breakers[service] = CircuitBreaker(service)
    return _breakers[service]
