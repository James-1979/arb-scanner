from __future__ import annotations

"""Provider runtime foundation introduced in ArbScanner 0.9.0.

This module intentionally contains no Smarkets network integration and no
real order-placement path.  It owns runtime configuration, provider construction,
session/health metadata and LIVE eligibility checks while keeping provider-native
objects behind the adapter boundary.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping

from .modes import FeedEntitlement, MarketDataTransport, TradingAccess, live_feed_eligible
from .venues import BETFAIR, MATCHBOOK, SMARKETS, ProviderRegistry, ProviderSpec


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProviderSessionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    AUTHENTICATED = "AUTHENTICATED"
    EXPIRING = "EXPIRING"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProviderRuntimeProfile:
    provider_id: str
    enabled: bool = True
    feed_entitlement: FeedEntitlement = FeedEntitlement.UNKNOWN
    market_data_transport: MarketDataTransport = MarketDataTransport.POLL
    trading_access: TradingAccess = TradingAccess.READ_ONLY
    execution_enabled: bool = False
    credential_profile: str = "default"
    currency: str = "GBP"
    commission_profile: str = "configured"
    stale_quote_limit_seconds: float = 10.0
    request_timeout_seconds: float = 20.0
    rate_limit_per_minute: int | None = None
    pre_match_enabled: bool = True
    in_play_enabled: bool = True
    racing_enabled: bool = True
    fallback_transport: MarketDataTransport | None = None
    fallback_pre_match_allowed: bool = False
    fallback_in_play_allowed: bool = False
    api_state: str = "available"
    account_data_capability: str = "unavailable"
    account_history_capability: str = "none"
    orders_read_capability: bool = False
    orders_write_capability: bool = False
    account_refresh_seconds: float = 30.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("feed_entitlement", "market_data_transport", "trading_access", "fallback_transport"):
            value = out.get(key)
            if isinstance(value, Enum):
                out[key] = value.value
        return out


@dataclass
class ProviderSession:
    provider_id: str
    state: ProviderSessionState = ProviderSessionState.DISCONNECTED
    authenticated_at: str | None = None
    expires_at: str | None = None
    last_keepalive_at: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["state"] = self.state.value
        return out


@dataclass
class ProviderRuntimeStatus:
    provider_id: str
    registered: bool = True
    enabled: bool = True
    authenticated: bool = False
    market_data_connected: bool = False
    trading_connected: bool = False
    account_connected: bool = False
    settlement_connected: bool = False
    quote_age_ms: int | None = None
    latency_ms: int | None = None
    rate_limit_remaining: int | None = None
    clock_offset_ms: int | None = None
    session_state: ProviderSessionState = ProviderSessionState.DISCONNECTED
    last_success_at: str | None = None
    last_error: str | None = None
    degraded_reason: str | None = None
    requested_feed_entitlement: str = "unknown"
    effective_feed_entitlement: str = "unknown"
    feed_reason: str | None = None
    feed_generation: int = 0
    account_connection_state: str = "not_configured"
    account_last_success_at: str | None = None
    account_history_last_success_at: str | None = None
    account_history_connection_state: str = "not_configured"
    account_history_last_error: str | None = None
    account_history_last_error_type: str | None = None
    request_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    rolling_latency_ms: float | None = None
    last_failure_at: str | None = None
    last_error_type: str | None = None

    @property
    def healthy_for_market_data(self) -> bool:
        return bool(self.enabled and self.market_data_connected and not self.degraded_reason)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["session_state"] = self.session_state.value
        out["healthy_for_market_data"] = self.healthy_for_market_data
        return out


AdapterFactory = Callable[[dict[str, Any], Any], Any]
AccountProviderFactory = Callable[[dict[str, Any], Any], Any]


class ProviderRuntimeRegistry:
    """Authoritative registry for provider metadata, runtime profile and factories.

    The registry deliberately returns ordinary serialisable manifests to core/UI
    code. Provider SDK/native objects stay inside adapter instances and never form
    part of the cross-component contract, keeping the boundary RPC-ready without
    introducing gRPC/Protobuf in 0.9.0.
    """

    def __init__(self, provider_registry: ProviderRegistry | None = None):
        self.providers = provider_registry or ProviderRegistry()
        self._profiles: dict[str, ProviderRuntimeProfile] = {}
        self._factories: dict[str, AdapterFactory] = {}
        self._account_factories: dict[str, AccountProviderFactory] = {}
        self._sessions: dict[str, ProviderSession] = {}
        self._statuses: dict[str, ProviderRuntimeStatus] = {}

    def register_provider(self, spec: ProviderSpec, *, profile: ProviderRuntimeProfile | None = None,
                          adapter_factory: AdapterFactory | None = None,
                          account_provider_factory: AccountProviderFactory | None = None) -> None:
        self.providers.register(spec)
        pid = spec.provider_id.lower()
        if profile is not None:
            self._profiles[pid] = profile
        if adapter_factory is not None:
            self._factories[pid] = adapter_factory
        if account_provider_factory is not None:
            self._account_factories[pid] = account_provider_factory
        self._sessions.setdefault(pid, ProviderSession(pid))
        self._statuses.setdefault(pid, ProviderRuntimeStatus(pid))

    def set_profile(self, profile: ProviderRuntimeProfile) -> None:
        self._profiles[profile.provider_id.lower()] = profile
        self._statuses.setdefault(profile.provider_id.lower(), ProviderRuntimeStatus(profile.provider_id.lower()))

    def profile(self, provider_id: str) -> ProviderRuntimeProfile | None:
        return self._profiles.get(str(provider_id or "").lower())

    def runtime_status(self, provider_id: str) -> ProviderRuntimeStatus:
        pid = str(provider_id or "").lower()
        return self._statuses.setdefault(pid, ProviderRuntimeStatus(pid, registered=self.providers.get(pid) is not None))

    def session(self, provider_id: str) -> ProviderSession:
        pid = str(provider_id or "").lower()
        return self._sessions.setdefault(pid, ProviderSession(pid))

    def enabled_provider_ids(self) -> list[str]:
        out = []
        for spec in self.providers.all():
            profile = self.profile(spec.provider_id) or ProviderRuntimeProfile(spec.provider_id)
            status = self.runtime_status(spec.provider_id)
            if profile.enabled and status.enabled:
                out.append(spec.provider_id)
        return out

    def set_runtime_enabled(self, provider_id: str, enabled: bool) -> ProviderRuntimeStatus:
        status = self.runtime_status(provider_id)
        status.enabled = bool(enabled)
        if not enabled:
            status.market_data_connected = False
            status.degraded_reason = "disabled"
        elif status.degraded_reason == "disabled":
            status.degraded_reason = None
        return status

    def build_market_data_adapters(self, config: dict[str, Any], secrets: Any) -> list[Any]:
        adapters: list[Any] = []
        for spec in self.providers.all():
            pid = spec.provider_id.lower()
            profile = self.profile(pid)
            if profile is not None and not profile.enabled:
                continue
            if not self.runtime_status(pid).enabled:
                continue
            factory = self._factories.get(pid)
            if factory is None:
                continue
            adapter = factory(config, secrets)
            if adapter is not None:
                adapters.append(adapter)
        return adapters

    def build_account_providers(self, config: dict[str, Any], secrets: Any, provider_ids: Iterable[str] | None = None) -> dict[str, Any]:
        """Build requested read-only account providers only; execution providers are never returned here."""
        requested = None if provider_ids is None else {str(x).lower() for x in provider_ids}
        out: dict[str, Any] = {}
        for spec in self.providers.all():
            if requested is not None and spec.provider_id.lower() not in requested:
                continue
            pid = spec.provider_id.lower()
            profile = self.profile(pid) or ProviderRuntimeProfile(pid)
            status = self.runtime_status(pid)
            if not profile.enabled or not status.enabled:
                continue
            factory = self._account_factories.get(pid)
            if factory is None:
                continue
            provider = factory(config, secrets)
            if provider is not None:
                out[pid] = provider
        return out

    def update_account_health(self, provider_id: str, *, ok: bool, connection_state: str,
                              latency_ms: int | None = None, error: str | None = None,
                              error_type: str | None = None, history: bool = False) -> ProviderRuntimeStatus:
        status = self.runtime_status(provider_id)
        now = _utc_now()
        status.request_count += 1
        resolved_state = str(connection_state or ("connected" if ok else "error"))
        if history:
            status.account_history_connection_state = resolved_state
        else:
            status.account_connection_state = resolved_state
        if latency_ms is not None:
            status.latency_ms = int(latency_ms)
            status.rolling_latency_ms = float(latency_ms) if status.rolling_latency_ms is None else round(status.rolling_latency_ms * 0.8 + float(latency_ms) * 0.2, 2)
        if ok:
            if not history:
                status.account_connected = True
                status.authenticated = True
                status.session_state = ProviderSessionState.AUTHENTICATED
                session = self.session(provider_id)
                session.state = ProviderSessionState.AUTHENTICATED
                session.authenticated_at = session.authenticated_at or now
                session.last_error = None
            status.success_count += 1
            status.consecutive_failures = 0
            if history:
                status.account_history_last_success_at = now
                status.account_history_last_error = None
                status.account_history_last_error_type = None
            else:
                status.account_last_success_at = now
                status.last_error = None
                status.last_error_type = None
            status.last_success_at = now
        else:
            if not history:
                status.account_connected = False
                if str(error_type or "") in {"AUTH_FAILED", "API_NOT_AUTHORISED", "SESSION_EXPIRED"}:
                    status.authenticated = False
                    status.session_state = ProviderSessionState.FAILED
                    session = self.session(provider_id)
                    session.state = ProviderSessionState.FAILED
                    session.last_error = error
            status.failure_count += 1
            status.consecutive_failures += 1
            status.last_failure_at = now
            if history:
                status.account_history_last_error = error
                status.account_history_last_error_type = error_type
            else:
                status.last_error = error
                status.last_error_type = error_type
        return status

    def update_market_health(self, provider_id: str, *, ok: bool, latency_ms: int | None = None,
                             quote_age_ms: int | None = None, error: str | None = None,
                             requested_feed_entitlement: str | None = None, effective_feed_entitlement: str | None = None,
                             feed_reason: str | None = None, feed_generation: int | None = None) -> ProviderRuntimeStatus:
        status = self.runtime_status(provider_id)
        # Runtime enablement is controlled by set_runtime_enabled(). Do not let a
        # health update silently re-enable a provider that the operator/runtime
        # has disabled. The static profile is evaluated separately by callers.
        status.market_data_connected = bool(ok) if status.enabled else False
        status.latency_ms = latency_ms
        status.quote_age_ms = quote_age_ms
        status.last_success_at = _utc_now() if ok else status.last_success_at
        status.last_error = error if not ok else None
        status.degraded_reason = None if ok else (error or "market data unavailable")
        if requested_feed_entitlement is not None:
            status.requested_feed_entitlement = str(requested_feed_entitlement or "unknown").lower()
        if effective_feed_entitlement is not None:
            status.effective_feed_entitlement = str(effective_feed_entitlement or "unknown").lower()
        if feed_reason is not None:
            status.feed_reason = str(feed_reason or "") or None
        if feed_generation is not None:
            status.feed_generation = max(status.feed_generation, int(feed_generation or 0))
        return status

    def live_eligibility(self, provider_id: str, *, global_live_unlocked: bool, stream: str = "pre_match",
                         unresolved_unknown_orders: int = 0, reconciliation_clean: bool = False,
                         account_readable: bool | None = None, commission_known: bool = True,
                         stake_limits_configured: bool = True) -> dict[str, Any]:
        pid = str(provider_id or "").lower()
        profile = self.profile(pid) or ProviderRuntimeProfile(pid, enabled=False)
        status = self.runtime_status(pid)
        checks = {
            "global_live_unlocked": bool(global_live_unlocked),
            "provider_enabled": bool(profile.enabled),
            "execution_enabled": bool(profile.execution_enabled),
            "trading_access": profile.trading_access == TradingAccess.TRADING,
            "feed_live_eligible": live_feed_eligible(profile.feed_entitlement),
            "feed_healthy": status.healthy_for_market_data,
            "account_readable": bool(status.account_connected if account_readable is None else account_readable),
            "commission_known": bool(commission_known),
            "stake_limits_configured": bool(stake_limits_configured),
            "reconciliation_clean": bool(reconciliation_clean),
            "no_unknown_orders": int(unresolved_unknown_orders or 0) == 0,
            "stream_enabled": bool(profile.racing_enabled if stream == "racing" else profile.in_play_enabled if stream == "in_play" else profile.pre_match_enabled),
        }
        return {"provider_id": pid, "eligible": all(checks.values()), "checks": checks}

    def manifest(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for spec in self.providers.all():
            pid = spec.provider_id.lower()
            payload[pid] = {
                **spec.as_dict(),
                "runtime_profile": (self.profile(pid) or ProviderRuntimeProfile(pid)).as_dict(),
                "runtime_status": self.runtime_status(pid).as_dict(),
                "session": self.session(pid).as_dict(),
                "adapter_registered": pid in self._factories,
                "account_provider_registered": pid in self._account_factories,
            }
        return payload


def default_provider_runtime_registry() -> ProviderRuntimeRegistry:
    """Create the 0.9.0 runtime with current Betfair/Matchbook adapters only.

    Lazy imports avoid making provider SDK/transport classes part of the service
    contract and keep circular imports out of the canonical model layer.
    """
    registry = ProviderRuntimeRegistry(ProviderRegistry([]))

    def build_matchbook(config: dict[str, Any], secrets: Any):
        if not config.get("matchbook_enabled", True):
            return None
        from .adapters import MatchbookAdapter
        from .sports import enabled_sports_from_config
        return MatchbookAdapter(
            session_token=secrets.get("matchbook_session_token"),
            username=config.get("matchbook_username") or secrets.get("matchbook_username") or None,
            password=secrets.get("matchbook_password"),
            commission_pct=float(config.get("matchbook_commission_pct", 2.0)),
            enabled_sports=enabled_sports_from_config(config),
            live_lookback_hours=int(config.get("live_lookback_hours", 8) or 8),
        )

    def build_betfair(config: dict[str, Any], secrets: Any):
        if not config.get("betfair_enabled", True):
            return None
        from .adapters import BetfairDelayedAdapter
        from .sports import enabled_sports_from_config
        requested = str(config.get("betfair_feed_entitlement", "delayed") or "delayed").strip().lower()
        if requested not in {"delayed", "live"}:
            requested = "delayed"
        app_key = secrets.get("betfair_live_app_key") if requested == "live" else secrets.get("betfair_app_key")
        return BetfairDelayedAdapter(
            app_key=app_key,
            session_token=secrets.get("betfair_session_token"),
            commission_pct=float(config.get("betfair_commission_pct", 2.0)),
            enabled_sports=enabled_sports_from_config(config),
            live_lookback_hours=int(config.get("live_lookback_hours", 8) or 8),
            requested_feed_entitlement=requested,
        )

    def build_matchbook_account(config: dict[str, Any], secrets: Any):
        from .account_providers import MatchbookAccountProvider
        return MatchbookAccountProvider(
            session_token=secrets.get("matchbook_session_token"),
            username=config.get("matchbook_username") or secrets.get("matchbook_username") or None,
            password=secrets.get("matchbook_password"),
        )

    def build_betfair_account(config: dict[str, Any], secrets: Any):
        from .account_providers import BetfairAccountProvider
        return BetfairAccountProvider(
            app_key=secrets.get("betfair_app_key"),
            session_token=secrets.get("betfair_session_token"),
        )

    registry.register_provider(MATCHBOOK, profile=ProviderRuntimeProfile(
        provider_id="matchbook", enabled=True, feed_entitlement=FeedEntitlement.LIVE,
        market_data_transport=MarketDataTransport.POLL, trading_access=TradingAccess.READ_ONLY,
        execution_enabled=False, credential_profile="default", rate_limit_per_minute=None,
        fallback_transport=None, api_state="available", account_data_capability="available",
        account_history_capability="partial", orders_read_capability=False, orders_write_capability=False,
    ), adapter_factory=build_matchbook, account_provider_factory=build_matchbook_account)
    registry.register_provider(BETFAIR, profile=ProviderRuntimeProfile(
        provider_id="betfair", enabled=True, feed_entitlement=FeedEntitlement.DELAYED,
        market_data_transport=MarketDataTransport.POLL, trading_access=TradingAccess.READ_ONLY,
        execution_enabled=False, credential_profile="delayed", rate_limit_per_minute=None,
        fallback_transport=None, api_state="available", account_data_capability="available",
        account_history_capability="partial", orders_read_capability=False, orders_write_capability=False,
    ), adapter_factory=build_betfair, account_provider_factory=build_betfair_account)
    # Smarkets is the only approved-next venue exposed in the normal runtime.
    registry.register_provider(SMARKETS, profile=ProviderRuntimeProfile(
        provider_id="smarkets", enabled=True, feed_entitlement=FeedEntitlement.UNKNOWN,
        market_data_transport=MarketDataTransport.POLL, trading_access=TradingAccess.READ_ONLY,
        execution_enabled=False, credential_profile="default", api_state="awaiting_api_access",
        account_data_capability="unavailable", account_history_capability="none", orders_read_capability=False,
        orders_write_capability=False, rate_limit_per_minute=1200,
        metadata={
            "awaiting_api_access": True,
            "http_api_base": "https://api.smarkets.com",
            "session_route": "/v3/sessions/",
            "events_route": "/v3/events/",
            "markets_route": "/v3/events/{event_ids}/markets/",
            "quotes_route": "/v3/markets/{market_ids}/quotes/",
            "accounts_route": "/v3/accounts/",
            "approved_not_activated": True,
        },
    ))
    return registry
