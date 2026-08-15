from __future__ import annotations

"""Canonical runtime/economic mode contracts for ArbScanner 0.9.0.

The application exposes only SIM and LIVE as economic operating modes. Historical
MONITOR/WATCH/PAPER labels remain readable through explicit compatibility mapping, but new economic/runtime state uses only the canonical values below.
"""

from enum import Enum


class ExecutionMode(str, Enum):
    SIM = "sim"
    LIVE = "live"


class FeedEntitlement(str, Enum):
    DELAYED = "delayed"
    LIVE = "live"
    REPLAY = "replay"
    UNKNOWN = "unknown"


class MarketDataTransport(str, Enum):
    STREAM = "stream"
    POLL = "poll"
    PUSH = "push"
    REPLAY = "replay"
    UNKNOWN = "unknown"


class TradingAccess(str, Enum):
    READ_ONLY = "read_only"
    TRADING = "trading"
    NONE = "none"


LEGACY_SIM_ALIASES = {
    "sim", "monitor", "watch", "paper", "simulate", "simulation", "find", "observe", "research"
}
LIVE_ALIASES = {"live", "real"}


def canonical_execution_mode(value: object, *, default: ExecutionMode = ExecutionMode.SIM) -> ExecutionMode:
    raw = str(value or "").strip().lower()
    if raw in LEGACY_SIM_ALIASES:
        return ExecutionMode.SIM
    if raw in LIVE_ALIASES:
        return ExecutionMode.LIVE
    return default


def canonical_mode_value(value: object, *, default: ExecutionMode = ExecutionMode.SIM) -> str:
    return canonical_execution_mode(value, default=default).value


def storage_mode_for_legacy_sim(value: object) -> str:
    """Compatibility label for existing SIM-era tables that historically use MONITOR.

    New LIVE persistence never calls this helper. It exists only so 0.9.0 can
    preserve historical SIM data without a destructive table rewrite.
    """
    return "live" if canonical_execution_mode(value) == ExecutionMode.LIVE else "monitor"


def live_feed_eligible(entitlement: object) -> bool:
    if isinstance(entitlement, FeedEntitlement):
        return entitlement == FeedEntitlement.LIVE
    try:
        return FeedEntitlement(str(entitlement).strip().lower()) == FeedEntitlement.LIVE
    except ValueError:
        return False
