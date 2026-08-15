from __future__ import annotations

"""Venue/provider-neutral trading contracts introduced in ArbScanner 0.8.43.

The contracts in this module deliberately sit above Betfair/Matchbook-specific
transport details. Existing exchange adapters and paper execution keep their
behaviour; they expose enough metadata to let future bookmaker/broker providers
join without forcing exchange lifecycle semantics into core models.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping
from datetime import datetime, timezone
import uuid


class VenueType(str, Enum):
    EXCHANGE = "EXCHANGE"
    BOOKMAKER = "BOOKMAKER"
    BROKER = "BROKER"


@dataclass(frozen=True)
class ProviderCapabilities:
    market_discovery: bool = False
    streaming_prices: bool = False
    polling_prices: bool = True
    back_orders: bool = True
    lay_orders: bool = False
    order_cancellation: bool = False
    partial_fills: bool = False
    fill_or_kill: bool = False
    price_constraints: bool = True
    minimum_stake: bool = True
    maximum_stake: bool = True
    executable_capacity: bool = True
    commission: bool = False
    fees: bool = False
    account_balance: bool = False
    reserved_balance: bool = False
    order_status: bool = False
    settlement: bool = False
    client_order_reference: bool = False
    heartbeat: bool = False
    account_reconciliation: bool = False
    in_play: bool = False
    pre_match: bool = True
    racing: bool = False

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class VenueIdentity:
    venue_id: str
    venue_name: str
    venue_type: VenueType
    provider_id: str
    underlying_venue_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["venue_type"] = self.venue_type.value
        return payload


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    venue: VenueIdentity
    capabilities: ProviderCapabilities

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "venue": self.venue.as_dict(),
            "capabilities": self.capabilities.as_dict(),
        }


@dataclass(frozen=True)
class CanonicalMarketIdentity:
    canonical_event_id: str
    canonical_market_id: str
    canonical_selection_id: str | None = None
    provider_event_id: str | None = None
    provider_market_id: str | None = None
    provider_selection_id: str | None = None


@dataclass(frozen=True)
class VenueQuote:
    venue_id: str
    provider_id: str
    canonical_event_id: str
    canonical_market_id: str
    canonical_selection_id: str
    selection: str
    side: str
    odds: float
    executable_capacity: float | None
    currency: str
    captured_at: str
    displayed_odds: float | None = None
    executable_odds: float | None = None
    capacity_source: str = "provider"
    source_timestamp: str | None = None
    feed_entitlement: str = "unknown"
    market_data_transport: str = "unknown"
    quote_age_ms: int | None = None
    source_state_version: str | None = None
    commission_pct: float = 0.0
    fee_model: str = "commission"
    underlying_venue_id: str | None = None
    provider_market_id: str | None = None
    provider_selection_id: str | None = None
    price_valid_until: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VenuePositionLeg:
    venue_id: str
    provider_id: str
    selection: str
    side: str
    requested_odds: float
    requested_stake: float
    currency: str = "GBP"
    accepted_odds: float | None = None
    executed_stake: float | None = None
    commission: float = 0.0
    fees: float = 0.0
    order_reference: str | None = None
    fill_state: str | None = None
    settlement_state: str | None = None
    underlying_venue_id: str | None = None
    canonical_event_id: str | None = None
    canonical_market_id: str | None = None
    canonical_selection_id: str | None = None
    economic_exposure: Mapping[str, float] = field(default_factory=dict)
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VenueAccount:
    account_id: str
    provider_id: str
    venue_id: str
    currency: str
    mode: str
    balance: float
    reserved_capital: float = 0.0
    underlying_venue_id: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderIntent:
    venue_id: str
    provider_id: str
    selection: str
    side: str
    stake: float
    target_odds: float
    position_id: str | int | None = None
    leg_id: str | int | None = None
    minimum_acceptable_odds: float | None = None
    expires_at: str | None = None
    fill_or_kill: bool = False
    underlying_venue_id: str | None = None
    attempt_id: str | int | None = None
    client_order_id: str | None = None
    mode: str = "sim"
    canonical_event_id: str | None = None
    canonical_market_id: str | None = None
    canonical_selection_id: str | None = None
    created_at: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


def new_order_intent(*, venue_id: str, provider_id: str, selection: str, side: str,
                     stake: float, target_odds: float, position_id: str | int,
                     leg_id: str | int, attempt_id: str | int = 1, mode: str = "sim",
                     **kwargs: Any) -> OrderIntent:
    """Create an immutable, externally-safe order identity before any I/O.

    LIVE adapters added after 0.9.0 must receive an ``OrderIntent`` created by
    this boundary (or an equivalent caller-supplied immutable ID) before they may
    persist/transmit an external request.
    """
    created_at = str(kwargs.pop("created_at", None) or datetime.now(timezone.utc).isoformat())
    client_order_id = str(kwargs.pop("client_order_id", None) or
                          f"ARB-{position_id}-{leg_id}-{attempt_id}-{uuid.uuid4().hex[:10]}")
    return OrderIntent(
        venue_id=venue_id, provider_id=provider_id, selection=selection, side=side,
        stake=float(stake), target_odds=float(target_odds), position_id=position_id,
        leg_id=leg_id, attempt_id=attempt_id, client_order_id=client_order_id,
        mode=mode, created_at=created_at, **kwargs,
    )


class ProviderExecutionStatus(str, Enum):
    NOT_SUBMITTED = "NOT_SUBMITTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    # Compatibility aliases for 0.8.x provider adapters.
    PARTIALLY_EXECUTED = "PARTIAL"
    FULLY_EXECUTED = "FILLED"
    CANCELLED = "CANCELLED"
    PRICE_CHANGED = "PRICE_CHANGED"
    INSUFFICIENT_CAPACITY = "INSUFFICIENT_CAPACITY"
    EXTERNAL_ERROR = "EXTERNAL_ERROR"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ProviderExecutionResult:
    status: ProviderExecutionStatus
    requested_stake: float
    executed_stake: float = 0.0
    average_odds: float | None = None
    external_order_id: str | None = None
    reason: str | None = None
    provider_timestamp: str | None = None
    fills: tuple[Mapping[str, Any], ...] = ()
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


class SettlementStatus(str, Enum):
    WON = "WON"
    LOST = "LOST"
    VOID = "VOID"
    PARTIAL = "PARTIAL"
    PENDING = "PENDING"
    CORRECTED = "CORRECTED"


@dataclass(frozen=True)
class CanonicalSettlement:
    status: SettlementStatus
    settled_at: str | None = None
    gross_return: float | None = None
    net_pnl: float | None = None
    provider_reference: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


_EXCHANGE_CAPABILITIES = ProviderCapabilities(
    market_discovery=True,
    polling_prices=True,
    back_orders=True,
    lay_orders=True,
    order_cancellation=True,
    partial_fills=True,
    fill_or_kill=True,
    price_constraints=True,
    minimum_stake=True,
    maximum_stake=True,
    executable_capacity=True,
    commission=True,
    account_balance=True,
    reserved_balance=True,
    order_status=True,
    settlement=True,
    client_order_reference=True,
    account_reconciliation=True,
    in_play=True,
    pre_match=True,
    racing=True,
)


BETFAIR = ProviderSpec(
    provider_id="betfair",
    venue=VenueIdentity("betfair", "Betfair", VenueType.EXCHANGE, "betfair"),
    capabilities=_EXCHANGE_CAPABILITIES,
)
MATCHBOOK = ProviderSpec(
    provider_id="matchbook",
    venue=VenueIdentity("matchbook", "Matchbook", VenueType.EXCHANGE, "matchbook"),
    capabilities=_EXCHANGE_CAPABILITIES,
)

# Smarkets is a first-class staged exchange provider in 0.9.16. The provider
# identity/capability shape is available before API activation; runtime I/O stays
# disabled until a reviewed adapter and credentials are explicitly configured.
SMARKETS = ProviderSpec(
    provider_id="smarkets",
    venue=VenueIdentity("smarkets", "Smarkets", VenueType.EXCHANGE, "smarkets"),
    capabilities=ProviderCapabilities(
        market_discovery=True, polling_prices=True, streaming_prices=True, back_orders=True, lay_orders=True,
        order_cancellation=True, partial_fills=True, fill_or_kill=True, executable_capacity=True,
        commission=True, account_balance=True, order_status=True, settlement=True, in_play=True, pre_match=True, racing=True,
    ),
)
# Compatibility alias retained for pre-0.9.16 provider-shape tests.
SMARKETS_SHAPE = SMARKETS

# BETDAQ remains an architecture-only staged provider shape.
BETDAQ_SHAPE = ProviderSpec(
    provider_id="betdaq",
    venue=VenueIdentity("betdaq", "BETDAQ", VenueType.EXCHANGE, "betdaq"),
    capabilities=ProviderCapabilities(
        market_discovery=True, polling_prices=True, streaming_prices=True, back_orders=True, lay_orders=True,
        order_cancellation=True, partial_fills=True, fill_or_kill=True, executable_capacity=True, commission=True,
        account_balance=True, order_status=True, settlement=True, heartbeat=True, in_play=True, pre_match=True, racing=True,
    ),
)


class ProviderRegistry:
    def __init__(self, providers: list[ProviderSpec] | None = None):
        self._providers: dict[str, ProviderSpec] = {}
        for provider in ([BETFAIR, MATCHBOOK] if providers is None else providers):
            self.register(provider)

    def register(self, provider: ProviderSpec) -> None:
        self._providers[str(provider.provider_id).strip().lower()] = provider

    def get(self, provider_id: str) -> ProviderSpec | None:
        return self._providers.get(str(provider_id or "").strip().lower())

    def all(self) -> list[ProviderSpec]:
        return list(self._providers.values())

    def manifest(self) -> dict[str, dict[str, Any]]:
        return {p.provider_id: p.as_dict() for p in self.all()}


DEFAULT_PROVIDER_REGISTRY = ProviderRegistry()


def provider_id_for_name(name: str | None) -> str:
    value = str(name or "").strip().lower()
    if "betfair" in value:
        return "betfair"
    if "matchbook" in value:
        return "matchbook"
    if "smarkets" in value:
        return "smarkets"
    if "betdaq" in value:
        return "betdaq"
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_") or "unknown"


def venue_identity_for_name(name: str | None) -> VenueIdentity:
    provider_id = provider_id_for_name(name)
    spec = DEFAULT_PROVIDER_REGISTRY.get(provider_id)
    if spec:
        return spec.venue
    display = str(name or "Unknown venue").strip() or "Unknown venue"
    # Unknown legacy names remain representable without assuming Exchange.
    return VenueIdentity(provider_id, display, VenueType.EXCHANGE, provider_id)


def provider_capabilities(provider_id: str) -> ProviderCapabilities:
    spec = DEFAULT_PROVIDER_REGISTRY.get(provider_id)
    return spec.capabilities if spec else ProviderCapabilities()
