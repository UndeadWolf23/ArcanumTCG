"""Small animation toolkit: easing curves and per-frame value smoothing.

Widgets use `approach()` for frame-rate-independent smoothing (hover glows,
focus rings), and scenes use `Tween` for one-shot choreographed entrances.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable


# -- easing curves (t in [0,1] -> [0,1]) -------------------------------------

def linear(t: float) -> float:
    return t


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


def ease_out_back(t: float) -> float:
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def pulse(time_s: float, speed: float = 2.0) -> float:
    """Gentle 0..1 sine pulse for idle shimmer effects."""
    return 0.5 + 0.5 * math.sin(time_s * speed)


def approach(current: float, target: float, dt: float, speed: float = 12.0) -> float:
    """Exponential smoothing toward target; frame-rate independent."""
    return target + (current - target) * math.exp(-speed * dt)


@dataclass
class Tween:
    """One-shot animated value from `start` to `end` over `duration` seconds."""

    start: float
    end: float
    duration: float
    delay: float = 0.0
    easing: Callable[[float], float] = field(default=ease_out_cubic)
    _elapsed: float = field(default=0.0, init=False)

    def update(self, dt: float) -> float:
        self._elapsed += dt
        return self.value

    @property
    def value(self) -> float:
        t = (self._elapsed - self.delay) / self.duration
        t = min(1.0, max(0.0, t))
        return self.start + (self.end - self.start) * self.easing(t)

    @property
    def done(self) -> bool:
        return self._elapsed >= self.delay + self.duration

    def reset(self) -> None:
        self._elapsed = 0.0
