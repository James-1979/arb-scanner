from __future__ import annotations

"""Serializable service-boundary contracts for ArbScanner 0.9.0.

The 0.9.0 runtime remains in-process. These helpers deliberately keep canonical
provider messages composed only of scalar/list/dict data so the same boundaries
can later be transported by RPC (including gRPC/Protobuf) without changing core
trading semantics. No gRPC/Protobuf dependency is introduced here.
"""

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import json
from typing import Any, Mapping


def to_contract_payload(value: Any) -> Any:
    """Convert a canonical contract to JSON-safe data and reject live objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return to_contract_payload(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): to_contract_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_contract_payload(v) for v in value]
    if callable(value):
        raise TypeError("Callable/provider-native objects cannot cross the provider service boundary")
    raise TypeError(f"Non-serialisable provider contract value: {type(value).__name__}")


def assert_contract_serializable(value: Any) -> dict | list | str | int | float | bool | None:
    payload = to_contract_payload(value)
    json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return payload


@dataclass(frozen=True)
class ProviderHealthContract:
    provider_id: str
    registered: bool
    enabled: bool
    authenticated: bool
    market_data_connected: bool
    trading_connected: bool
    account_connected: bool
    settlement_connected: bool
    feed_entitlement: str
    market_data_transport: str
    quote_age_ms: int | None = None
    latency_ms: int | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    degraded_reason: str | None = None


@dataclass(frozen=True)
class AccountSnapshotContract:
    provider_id: str
    venue_id: str
    mode: str
    currency: str
    available: float | None
    reserved: float | None
    exposure: float | None
    equity: float | None
    captured_at: str | None
    source: str


@dataclass(frozen=True)
class OrderReconciliationContract:
    client_order_id: str
    provider_id: str
    venue_id: str
    state: str
    external_order_id: str | None = None
    requested_stake: float | None = None
    executed_stake: float | None = None
    average_odds: float | None = None
    reconciled_at: str | None = None
    reason: str | None = None
    provider_metadata: Mapping[str, Any] | None = None


SERVICE_BOUNDARY_MANIFEST = {
    "ready": True,
    "transport": "in_process",
    "grpc_enabled": False,
    "protobuf_enabled": False,
    "rules": [
        "canonical serialisable request/response data only",
        "provider sessions and transport clients remain provider-owned",
        "database handles are not provider contract arguments",
        "provider-native SDK objects do not leak into scanner/core contracts",
        "future RPC transport must preserve these canonical semantics",
    ],
}
