from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from .venues import provider_id_for_name, venue_identity_for_name


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_utc_iso(value: Any) -> str | None:
    """Return an explicit UTC ISO-8601 timestamp for exchange-supplied times.

    Exchange adapters can receive ISO strings with or without an offset, or
    epoch seconds/milliseconds.  Internally we always carry an explicit UTC
    offset so Python and browser date parsing cannot interpret the same value
    in different time zones.  A timezone-less ISO value retains the historical
    ArbScanner convention of being treated as UTC; the original source value is
    preserved separately by the adapter for diagnostics.
    """
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            raw = float(value)
            # Accept Unix milliseconds as well as seconds.
            if abs(raw) > 100000000000.0:
                raw /= 1000.0
            dt = datetime.fromtimestamp(raw, tz=timezone.utc)
        else:
            text = str(value).strip()
            if not text:
                return None
            numeric = text.replace(".", "", 1).lstrip("+-")
            if numeric.isdigit():
                raw = float(text)
                if abs(raw) > 100000000000.0:
                    raw /= 1000.0
                dt = datetime.fromtimestamp(raw, tz=timezone.utc)
            else:
                if text.endswith(("Z", "z")):
                    text = text[:-1] + "+00:00"
                dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def source_time_is_naive(value: Any) -> bool:
    """Whether an exchange timestamp string omits an explicit UTC offset."""
    if value is None or isinstance(value, (int, float, datetime)):
        return False
    text = str(value).strip()
    if not text or text.replace(".", "", 1).lstrip("+-").isdigit():
        return False
    if text.endswith(("Z", "z")):
        return False
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return False
    return dt.tzinfo is None




@dataclass(frozen=True)
class DepthLevel:
    side: str
    level: int
    odds: float
    available_size: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Quote:
    exchange: str
    event_id: str
    market_id: str
    event_name: str
    market_name: str
    selection_id: str
    selection: str
    odds: float
    liquidity: float
    captured_at: str
    start_time: str | None = None
    commission_pct: float = 0.0
    commission_source: str = "configured"
    source_latency_ms: int = 0
    market_type: str = "match odds"
    strategy: str = "1x2"
    sport: str = "Unknown"
    in_play: bool | None = None
    market_status: str | None = None
    raw: dict[str, Any] | None = None
    section: str = "sports"
    trap_number: int | None = None
    canonical_selection_key: str | None = None
    runner_status: str | None = None
    venue_id: str | None = None
    provider_id: str | None = None
    underlying_venue_id: str | None = None
    canonical_event_id: str | None = None
    canonical_market_id: str | None = None
    canonical_selection_id: str | None = None
    currency: str = "GBP"
    side: str = "BACK"
    executable_capacity: float | None = None
    fee_model: str = "commission"
    displayed_odds: float | None = None
    executable_odds: float | None = None
    capacity_source: str = "exchange_liquidity"
    feed_entitlement: str = "unknown"
    market_data_transport: str = "unknown"
    source_timestamp: str | None = None
    timestamp_quality: str = "unknown"
    quote_age_ms: int | None = None
    source_state_version: str | None = None
    depth_levels: tuple[DepthLevel, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        identity = venue_identity_for_name(self.exchange)
        if not self.venue_id:
            object.__setattr__(self, "venue_id", identity.venue_id)
        if not self.provider_id:
            object.__setattr__(self, "provider_id", identity.provider_id)
        if self.executable_capacity is None:
            object.__setattr__(self, "executable_capacity", float(self.liquidity))
        if self.displayed_odds is None:
            object.__setattr__(self, "displayed_odds", float(self.odds))
        if self.executable_odds is None:
            object.__setattr__(self, "executable_odds", float(self.odds))
        # 0.9.0: market-data provenance is independent from SIM/LIVE economics.
        # Legacy adapters do not yet populate transport metadata directly, so infer
        # the known current entitlement conservatively at the canonical boundary.
        if self.feed_entitlement == "unknown":
            inferred = "delayed" if "betfair" in str(self.exchange).lower() and "delayed" in str(self.exchange).lower() else "live" if self.provider_id == "matchbook" else "unknown"
            object.__setattr__(self, "feed_entitlement", inferred)
        if self.market_data_transport == "unknown" and self.provider_id in {"betfair", "matchbook"}:
            object.__setattr__(self, "market_data_transport", "poll")
        # 0.9.3: never manufacture a provider source timestamp from local receipt
        # time. Local receipt remains useful, but its basis must be explicit.
        quality = str(self.timestamp_quality or "unknown").strip().upper()
        if quality == "UNKNOWN":
            quality = "PROVIDER_SOURCE" if self.source_timestamp else "LOCAL_RECEIPT"
        if quality not in {"PROVIDER_SOURCE", "LOCAL_RECEIPT", "ESTIMATED", "UNKNOWN"}:
            quality = "UNKNOWN"
        object.__setattr__(self, "timestamp_quality", quality)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExchangeMarket:
    exchange: str
    event_id: str
    market_id: str
    event_name: str
    market_name: str
    start_time: str | None
    quotes: list[Quote] = field(default_factory=list)
    status: str = "OPEN"
    market_type: str = "match odds"
    strategy: str = "1x2"
    sport: str = "Unknown"
    in_play: bool | None = None
    raw: dict[str, Any] | None = None
    section: str = "sports"
    race_track: str | None = None
    race_number: int | None = None
    venue_id: str | None = None
    provider_id: str | None = None
    underlying_venue_id: str | None = None
    canonical_event_id: str | None = None
    canonical_market_id: str | None = None
    feed_entitlement: str = "unknown"
    market_data_transport: str = "unknown"
    source_timestamp: str | None = None
    timestamp_quality: str = "unknown"
    source_state_version: str | None = None

    def __post_init__(self) -> None:
        identity = venue_identity_for_name(self.exchange)
        self.venue_id = self.venue_id or identity.venue_id
        self.provider_id = self.provider_id or identity.provider_id
        if self.quotes:
            if self.feed_entitlement == "unknown":
                self.feed_entitlement = str(self.quotes[0].feed_entitlement or "unknown")
            if self.market_data_transport == "unknown":
                self.market_data_transport = str(self.quotes[0].market_data_transport or "unknown")
            if self.source_timestamp is None:
                self.source_timestamp = self.quotes[0].source_timestamp
            if str(self.timestamp_quality or "unknown").lower() == "unknown":
                self.timestamp_quality = str(self.quotes[0].timestamp_quality or "UNKNOWN")


@dataclass
class Leg:
    exchange: str
    selection: str
    odds: float
    liquidity: float
    commission_pct: float = 0.0
    commission_source: str = "configured"
    event_id: str | None = None
    market_id: str | None = None
    selection_id: str | None = None
    captured_at: str | None = None
    source_latency_ms: int = 0
    market_type: str = "match odds"
    strategy: str = "1x2"
    sport: str = "Unknown"
    in_play: bool | None = None
    market_status: str | None = None
    section: str = "sports"
    trap_number: int | None = None
    canonical_selection_key: str | None = None
    runner_status: str | None = None
    venue_id: str | None = None
    provider_id: str | None = None
    underlying_venue_id: str | None = None
    canonical_event_id: str | None = None
    canonical_market_id: str | None = None
    canonical_selection_id: str | None = None
    currency: str = "GBP"
    side: str = "BACK"
    executable_capacity: float | None = None
    fee_model: str = "commission"
    displayed_odds: float | None = None
    executable_odds: float | None = None
    capacity_source: str = "exchange_liquidity"
    feed_entitlement: str = "unknown"
    market_data_transport: str = "unknown"
    source_timestamp: str | None = None
    timestamp_quality: str = "unknown"
    quote_age_ms: int | None = None
    source_state_version: str | None = None
    depth_levels: tuple[DepthLevel, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        identity = venue_identity_for_name(self.exchange)
        self.venue_id = self.venue_id or identity.venue_id
        self.provider_id = self.provider_id or identity.provider_id
        if self.executable_capacity is None:
            self.executable_capacity = float(self.liquidity)
        if self.displayed_odds is None:
            self.displayed_odds = float(self.odds)
        if self.executable_odds is None:
            self.executable_odds = float(self.odds)
        quality = str(self.timestamp_quality or "unknown").strip().upper()
        if quality == "UNKNOWN":
            quality = "PROVIDER_SOURCE" if self.source_timestamp else "LOCAL_RECEIPT"
        if quality not in {"PROVIDER_SOURCE", "LOCAL_RECEIPT", "ESTIMATED", "UNKNOWN"}:
            quality = "UNKNOWN"
        self.timestamp_quality = quality

    @property
    def resolved_provider_id(self) -> str:
        return self.provider_id or provider_id_for_name(self.exchange)

    @property
    def resolved_venue_id(self) -> str:
        return self.venue_id or venue_identity_for_name(self.exchange).venue_id


@dataclass
class Scenario:
    name: str
    bankroll: float
    max_bankroll_pct: float = 100.0
    max_event_exposure_pct: float = 100.0


@dataclass
class MarketMatch:
    event_key: str
    market_key: str
    display_event: str
    display_market: str
    start_time: str | None
    markets: list[ExchangeMarket]
    match_score: float
    market_type: str = "match odds"
    strategy: str = "1x2"
    sport: str = "Unknown"
    in_play: bool | None = None
    status: str | None = None
    section: str = "sports"
    race_track: str | None = None
    race_number: int | None = None
    runner_count: int | None = None
    canonical_event_id: str | None = None
    canonical_market_id: str | None = None
