from __future__ import annotations
from datetime import datetime, timezone
from .normalization import parse_time


def event_phase(start_time: str | None, status: str | None = None, in_play: bool | None = None,
                settled: bool = False, now: datetime | None = None) -> dict:
    """Return a beginner-friendly lifecycle label for an event/market.

    Explicit exchange in-play/closed flags win. Time is used only as a fallback.
    """
    now = now or datetime.now(timezone.utc)
    st = str(status or "").upper()
    start = parse_time(start_time)

    if settled or st in {"CLOSED", "SETTLED", "GRADED", "VOID", "CANCELLED"}:
        phase, label = "historic", "Finished / historic"
    elif in_play is True:
        phase, label = "live", "LIVE NOW"
    elif in_play is False and st in {"OPEN", "ACTIVE"}:
        if start and start <= now:
            phase, label = "upcoming", "Pre-match / delayed start"
        else:
            phase, label = "upcoming", "Upcoming"
    elif in_play is False and st == "SUSPENDED":
        phase, label = "unknown", "Pre-match / suspended"
    elif start and start > now:
        phase, label = "upcoming", "Upcoming"
    elif start and start <= now and st in {"OPEN", "SUSPENDED", "ACTIVE"}:
        phase, label = "unknown", "Start status unconfirmed"
    elif start and start <= now:
        phase, label = "historic", "Started / historic"
    else:
        phase, label = "unknown", "Time/status unknown"

    seconds_to_start = int((start - now).total_seconds()) if start else None
    return {
        "phase": phase,
        "label": label,
        "start_time": start_time,
        "seconds_to_start": seconds_to_start,
        "status": st or None,
        "in_play": bool(in_play) if in_play is not None else None,
    }
