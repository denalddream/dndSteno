"""Small formatting helpers shared by the CLI and the GUI."""

from __future__ import annotations

import math

FLOOR_DB = -60.0
QUIET_DB = -30.0  # speech peaking below this is too quiet for reliable recognition


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def peak_db(peak: float, floor: float = FLOOR_DB) -> float:
    """Peak amplitude (0..1) in dBFS, clamped to `floor`."""
    return max(20 * math.log10(peak), floor) if peak > 0 else floor
