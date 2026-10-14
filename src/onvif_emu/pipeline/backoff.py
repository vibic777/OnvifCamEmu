"""Exponential backoff with full jitter (AWS architecture blog).

delay = random.uniform(0, min(cap, base * 2^attempt))

Full jitter avoids synchronized reconnect storms when many channels drop at once.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(slots=True)
class FullJitterBackoff:
    base: float = 1.0
    cap: float = 30.0
    attempt: int = 0

    def next_delay(self) -> float:
        exp = min(self.cap, self.base * (2 ** self.attempt))
        delay = random.uniform(0.0, exp) if exp > 0 else 0.0
        self.attempt += 1
        return delay

    def reset(self) -> None:
        self.attempt = 0

    def peek_max(self) -> float:
        return min(self.cap, self.base * (2 ** self.attempt))
