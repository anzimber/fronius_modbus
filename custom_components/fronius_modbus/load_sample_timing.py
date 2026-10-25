"""Validate host-side Modbus request windows for the derived Fronius house load.

These timestamps surround TCP requests; they are *not* device acquisition times.
All limits are well below the 30-second polling interval, while allowing normal
serial inverter + meter reads to take longer than 500 ms each.
"""

from __future__ import annotations

import math
from typing import Any

# One Modbus request may normally take >0.5 s. A multi-second stall must fail.
MAX_LOAD_REQUEST_SECONDS = 2.0
# The complete inverter + primary-meter acquisition must remain close in time.
MAX_LOAD_SNAPSHOT_SECONDS = 3.0


def load_window_issue(window: Any, now: float) -> str | None:
    """Return why a host request window cannot be used, or None if valid."""
    if not isinstance(window, (tuple, list)) or len(window) != 2:
        return "missing request timestamps"
    start, end = window
    if (
        not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in (start, end, now))
        or start > end
        or end > now
    ):
        return "invalid request timestamps"
    if end - start > MAX_LOAD_REQUEST_SECONDS:
        return "Modbus response too slow"
    if now - start > MAX_LOAD_SNAPSHOT_SECONDS:
        return "outdated request window"
    return None


def load_pair_issue(inverter_window: tuple[float, float], meter_window: tuple[float, float]) -> str | None:
    """Reject requests that are individually quick but too far apart in time."""
    pair_span = max(inverter_window[1], meter_window[1]) - min(inverter_window[0], meter_window[0])
    if pair_span > MAX_LOAD_SNAPSHOT_SECONDS:
        return "inverter/meter requests too far apart"
    return None
