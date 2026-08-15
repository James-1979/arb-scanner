"""Strict read-only LIVE provider boundary for ArbScanner 0.9.8.

0.9.8 may observe real account state but contains no LIVE order-placement path.
Account clients are provider-owned, registry-driven and physically isolated from
SIM wallets/positions/settlements.  Pending providers never perform network I/O.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import time
from typing import Any

from .account_providers import (
    AccountConnectionState,
    AccountDataQuality,
    AccountSnapshot,
    normalize_account_error,
)
from .contracts import SERVICE_BOUNDARY_MANIFEST


def _sensitive_key(key: str) -> bool:
    k = str(key or "").lower().replace("-", "_")
    return any(part in k for part in ("password", "secret", "session_token", "access_token", "refresh_token", "app_key", "api_key", "certificate_key"))


def _sanitize(value):
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items() if not _sensitive_key(str(k))}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, tuple):
        return [_sanitize(v) for v in value]
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


class LiveProviderRegistry:
    """Read-only LIVE account aggregator backed by the provider runtime registry."""

    def __init__(self, runtime_registry=None, db=None, secrets=None):
        self.runtime_registry = runtime_registry
        self.db = db
        self.secrets = secrets
        self._account_providers: dict[str, Any] = {}
        self._activity_refresh_at: dict[str, float] = {}
        self.capabilities = {
            "balances": True,
            "account_health": True,
            "account_activity": True,
            "market_feed": False,
            "positions": False,
            "orders": False,
            "executions": False,
            "settlements": False,
            "performance": False,
            "replay": False,
            "order_placement": False,
            "reconciliation": True,
            "provider_health": True,
        }

    def _redact_message(self, value: Any) -> str:
        """Remove configured credential values from backend-safe diagnostics."""
        text = str(value or "")
        if not text or self.secrets is None:
            return text
        candidates: list[str] = []
        for key in ("matchbook_username", "matchbook_password", "matchbook_session_token", "betfair_app_key", "betfair_live_app_key", "betfair_session_token"):
            try:
                secret = self.secrets.get(key)
            except Exception:
                secret = None
            if secret:
                candidates.append(str(secret))
        if self.runtime_registry is not None and hasattr(self.secrets, "provider_credentials"):
            for pid in self._provider_ids(include_disabled=True):
                profile = self.runtime_registry.profile(pid)
                profile_name = str(getattr(profile, "credential_profile", "default") or "default")
                try:
                    scoped = self.secrets.provider_credentials(pid, profile_name) or {}
                except Exception:
                    scoped = {}
                candidates.extend(str(v) for v in scoped.values() if v)
        for secret in sorted(set(candidates), key=len, reverse=True):
            if len(secret) >= 3:
                text = text.replace(secret, "[REDACTED]")
        return text

    def _provider_ids(self, *, include_disabled: bool = False) -> list[str]:
        if self.runtime_registry is None:
            return []
        if include_disabled:
            return sorted({str(x.provider_id).lower() for x in self.runtime_registry.providers.all()})
        return sorted({str(x).lower() for x in self.runtime_registry.enabled_provider_ids()})

    def _spec_profile_runtime(self, pid: str):
        spec = self.runtime_registry.providers.get(pid) if self.runtime_registry is not None else None
        profile = self.runtime_registry.profile(pid) if self.runtime_registry is not None else None
        runtime = self.runtime_registry.runtime_status(pid) if self.runtime_registry is not None else None
        return spec, profile, runtime

    def _pending_account(self, provider_id: str, currency: str, *, error: str | None = None) -> dict:
        pid = str(provider_id or "unknown").lower()
        spec, profile, runtime = self._spec_profile_runtime(pid)
        name = spec.venue.venue_name if spec is not None else pid.replace("_", " ").title()
        api_state = str(getattr(profile, "api_state", "unavailable") or "unavailable")
        pending = api_state in {"pending_api", "awaiting_api_access"}
        connection = "not_attempted" if pending else "not_configured"
        return {
            "exchange": pid,
            "provider_id": pid,
            "venue_id": spec.venue.venue_id if spec is not None else pid,
            "display_name": name,
            "mode": "live",
            "storage_mode": "live",
            "currency": None,
            "source": "live_account_provider",
            "balance": None,
            "available": None,
            "reserved": None,
            "exposure": None,
            "credit": None,
            "equity": None,
            "equity_estimate": None,
            "freshness": "UNAVAILABLE",
            "is_stale": False,
            "last_updated": None,
            "latency_ms": getattr(runtime, "latency_ms", None),
            "connection_state": connection,
            "data_quality": "unavailable",
            "api_state": api_state,
            "account_data_capability": getattr(profile, "account_data_capability", "unavailable"),
            "account_history_capability": getattr(profile, "account_history_capability", "none"),
            "market_data_quality": getattr(getattr(profile, "feed_entitlement", None), "value", str(getattr(profile, "feed_entitlement", "unknown"))).lower(),
            "market_execution_eligible": False,
            "order_placement_enabled": False,
            "integration_pending": pending,
            "runtime_status": runtime.as_dict() if runtime is not None else {},
            "runtime_profile": profile.as_dict() if profile is not None else {},
            "balance_semantics": None,
            "provider_account_ref": None,
            "error": error or ("Awaiting API access" if pending else "LIVE account data unavailable"),
        }

    def _snapshot_to_account(self, snapshot: dict, *, currency: str, profile=None, runtime=None, stale_after: float = 90.0) -> dict:
        pid = str(snapshot.get("provider_id") or "unknown").lower()
        spec = self.runtime_registry.providers.get(pid) if self.runtime_registry is not None else None
        name = spec.venue.venue_name if spec is not None else pid.replace("_", " ").title()
        received_at = snapshot.get("received_at")
        ts = _parse_ts(received_at)
        age = None if ts is None else max(0.0, time.time() - ts)
        stale = bool(snapshot.get("is_stale")) or (age is not None and age > max(1.0, float(stale_after)))
        connection_state = str(snapshot.get("connection_state") or "connected")
        runtime_state = str(getattr(runtime, "account_connection_state", "") or "") if runtime is not None else ""
        runtime_failed = bool(runtime is not None and getattr(runtime, "last_failure_at", None) and not getattr(runtime, "account_connected", False)
                              and runtime_state not in {"", "connected", "not_configured"})
        if runtime_failed:
            stale = True
            connection_state = runtime_state
        elif stale and connection_state == "connected":
            connection_state = "stale"
        return {
            "exchange": pid,
            "provider_id": pid,
            "venue_id": str(snapshot.get("venue_id") or pid),
            "display_name": name,
            "mode": "live",
            "storage_mode": "live",
            "currency": snapshot.get("currency") or None,
            "source": "live_account_provider",
            "balance": snapshot.get("balance"),
            "available": snapshot.get("available_balance"),
            "reserved": snapshot.get("reserved_balance"),
            "exposure": snapshot.get("exposure"),
            "credit": snapshot.get("credit"),
            "equity": snapshot.get("balance"),
            "equity_estimate": snapshot.get("balance"),
            "freshness": "STALE" if stale else "CURRENT",
            "is_stale": stale,
            "last_updated": received_at,
            "quote_age_seconds": None if age is None else round(age, 3),
            "latency_ms": ((snapshot.get("provider_metadata") or {}).get("latency_ms") if isinstance(snapshot.get("provider_metadata"), dict) else None) or getattr(runtime, "latency_ms", None),
            "connection_state": connection_state,
            "data_quality": str(snapshot.get("data_quality") or "partial"),
            "api_state": str(getattr(profile, "api_state", "available") or "available"),
            "account_data_capability": getattr(profile, "account_data_capability", "available"),
            "account_history_capability": getattr(profile, "account_history_capability", "none"),
            "market_data_quality": getattr(getattr(profile, "feed_entitlement", None), "value", str(getattr(profile, "feed_entitlement", "unknown"))).lower(),
            "market_execution_eligible": False,
            "order_placement_enabled": False,
            "integration_pending": False,
            "runtime_status": runtime.as_dict() if runtime is not None else {},
            "runtime_profile": profile.as_dict() if profile is not None else {},
            "balance_semantics": snapshot.get("balance_semantics"),
            "provider_account_ref": snapshot.get("provider_account_ref"),
            "error_code": snapshot.get("error_code"),
            "error": snapshot.get("error_message"),
        }

    def invalidate_account_providers(self, provider_id: str | None = None) -> None:
        """Drop cached read-only provider clients after credential/config changes."""
        if provider_id is None:
            self._account_providers = {}
            self._activity_refresh_at = {}
            return
        pid = str(provider_id or "").lower()
        self._account_providers.pop(pid, None)
        for key in list(self._activity_refresh_at):
            if key.startswith(pid + ":"):
                self._activity_refresh_at.pop(key, None)

    def _ensure_account_providers(self, config: dict, *, rebuild: bool = False) -> dict[str, Any]:
        if rebuild:
            self._account_providers = {}
        if self.runtime_registry is None or self.secrets is None:
            return self._account_providers
        missing = [pid for pid in self._provider_ids() if pid not in self._account_providers]
        built = self.runtime_registry.build_account_providers(config, self.secrets, provider_ids=missing)
        for pid, provider in built.items():
            self._account_providers[pid] = provider
        return self._account_providers

    async def account_state(self, config: dict, *, refresh: bool = False, context: str = "view") -> dict:
        """Return LIVE account state, retaining the last valid snapshot on failures."""
        currency = str(config.get("account_currency", "GBP") or "GBP").upper()
        stale_after = max(1.0, float(config.get("account_balance_stale_seconds", 90) or 90))
        providers = self._ensure_account_providers(config, rebuild=False)
        cached = self.db.latest_live_account_snapshots() if self.db is not None else {}
        accounts: dict[str, dict] = {}
        now = time.time()
        controls = {x["provider_id"]: x for x in (self.db.venue_controls() if self.db is not None else [])}

        for pid in self._provider_ids():
            spec, profile, runtime = self._spec_profile_runtime(pid)
            api_state = str(getattr(profile, "api_state", "available") or "available")
            control = controls.get(pid) or {}
            # Provider capability comes before operator account eligibility. A venue
            # whose API is not active must remain visibly AWAITING API ACCESS rather
            # than being misreported as merely disabled.
            if api_state in {"pending_api", "awaiting_api_access"} or pid not in providers:
                accounts[pid] = self._pending_account(pid, currency)
                continue
            if not bool(control.get("live_account_enabled", True)):
                row = self._pending_account(pid, currency, error="LIVE account access disabled")
                row.update({"integration_pending": False, "connection_state": "disabled", "api_state": api_state})
                accounts[pid] = row
                continue

            prior = cached.get(pid)
            prior_ts = _parse_ts((prior or {}).get("received_at"))
            last_failure_ts = _parse_ts(getattr(runtime, "last_failure_at", None)) if runtime is not None else None
            last_attempt_ts = max([x for x in (prior_ts, last_failure_ts) if x is not None], default=None)
            refresh_seconds = max(5.0, float((getattr(profile, "metadata", {}) or {}).get("account_refresh_seconds") or config.get("account_refresh_seconds", 30) or getattr(profile, "account_refresh_seconds", 30) or 30))
            should_fetch = bool(refresh or prior is None or last_attempt_ts is None or (now - last_attempt_ts) >= refresh_seconds)
            if not should_fetch and prior is not None:
                accounts[pid] = self._snapshot_to_account(prior, currency=currency, profile=profile, runtime=runtime, stale_after=stale_after)
                continue

            provider = providers[pid]
            started = time.perf_counter()
            try:
                snap_obj = await provider.get_account_snapshot()
                snapshot = snap_obj.as_dict()
                latency = int((time.perf_counter() - started) * 1000)
                meta = _sanitize(dict(snapshot.get("provider_metadata") or {}))
                meta.setdefault("latency_ms", latency)
                snapshot["provider_metadata"] = meta
                if self.db is not None:
                    self.db.upsert_live_account_snapshot(snapshot)
                    self.db.record_live_account_audit(provider_id=pid, event_type="account_snapshot_refreshed", status="OK",
                                                      latency_ms=latency, details={"context": context, "data_quality": snapshot.get("data_quality")})
                if self.runtime_registry is not None:
                    self.runtime_registry.update_account_health(pid, ok=True, connection_state="connected", latency_ms=latency)
                accounts[pid] = self._snapshot_to_account(snapshot, currency=currency, profile=profile,
                                                          runtime=self.runtime_registry.runtime_status(pid), stale_after=stale_after)
            except Exception as exc:
                latency = int((time.perf_counter() - started) * 1000)
                code, state, message = normalize_account_error(exc)
                message = self._redact_message(message)
                if self.runtime_registry is not None:
                    self.runtime_registry.update_account_health(pid, ok=False, connection_state=state.value, latency_ms=latency,
                                                                error=message, error_type=code.value)
                if self.db is not None:
                    self.db.record_live_account_audit(provider_id=pid, event_type="account_snapshot_failed", status="ERROR",
                                                      latency_ms=latency, error_type=code.value, message=message,
                                                      details={"context": context})
                if prior is not None:
                    stale_snapshot = dict(prior)
                    stale_snapshot["is_stale"] = True
                    stale_snapshot["connection_state"] = state.value
                    stale_snapshot["error_code"] = code.value
                    stale_snapshot["error_message"] = message
                    accounts[pid] = self._snapshot_to_account(stale_snapshot, currency=currency, profile=profile,
                                                              runtime=self.runtime_registry.runtime_status(pid), stale_after=stale_after)
                else:
                    accounts[pid] = self._pending_account(pid, currency, error=message)
                    accounts[pid].update({"integration_pending": False, "connection_state": state.value,
                                          "api_state": api_state, "error_code": code.value})

        unknown_orders = int(self.db.unresolved_live_order_count() if self.db is not None else 0)
        connected = [a for a in accounts.values() if a.get("connection_state") == "connected" and not a.get("is_stale")]
        return {
            "accounts": accounts,
            "reconciliation": {
                "status": "UNAVAILABLE" if unknown_orders == 0 else "BLOCKED_UNKNOWN_ORDERS",
                "delta": None,
                "tolerance": None,
                "unknown_orders": unknown_orders,
            },
            "captured_at": _utc_now(),
            "provider": "live_account_provider",
            "connected_providers": len(connected),
            "isolated_from_sim": True,
        }

    async def refresh_account_activity(self, config: dict, *, from_utc: str | None, to_utc: str | None,
                                       refresh: bool = False, context: str = "accounts") -> dict:
        providers = self._ensure_account_providers(config, rebuild=False)
        cache_seconds = max(30.0, float(config.get("account_history_cache_seconds", 120) or 120))
        result: dict[str, dict] = {}
        now = time.time()
        controls = {x["provider_id"]: x for x in (self.db.venue_controls() if self.db is not None else [])}
        for pid in self._provider_ids():
            _spec, profile, _runtime = self._spec_profile_runtime(pid)
            api_state = str(getattr(profile, "api_state", "available") or "available")
            capability = str(getattr(profile, "account_history_capability", "none") or "none")
            if not bool((controls.get(pid) or {}).get("live_account_enabled", True)):
                result[pid] = {"available": False, "pending": False, "capability": capability, "metric_support": {"deposits": False, "withdrawals": False, "trading_pnl": False, "commission": False}, "error": "LIVE account access disabled"}
                continue
            if api_state in {"pending_api", "awaiting_api_access"}:
                result[pid] = {"available": False, "pending": True, "capability": "none", "metric_support": {"deposits": False, "withdrawals": False, "trading_pnl": False, "commission": False}, "error": "Awaiting API access"}
                continue
            provider = providers.get(pid)
            if provider is None or capability == "none":
                result[pid] = {"available": False, "pending": False, "capability": capability, "metric_support": dict(getattr(provider, "account_metric_support", {}) or {}), "error": "Unavailable from provider"}
                continue
            key = f"{pid}:{from_utc or 'all'}"  # End-time advances with now; TTL, not exact to_utc, owns provider-history cadence.
            if not refresh and (now - self._activity_refresh_at.get(key, 0.0)) < cache_seconds:
                rows = self.db.live_account_activity(provider_id=pid, from_utc=from_utc, to_utc=to_utc) if self.db is not None else []
                runtime = self.runtime_registry.runtime_status(pid) if self.runtime_registry is not None else None
                history_state = str(getattr(runtime, "account_history_connection_state", "connected") or "connected")
                history_failed = bool(getattr(runtime, "account_history_last_error", None)) and history_state != "connected"
                result[pid] = {"available": bool(rows) if history_failed else True, "stale": bool(history_failed), "capability": capability,
                               "metric_support": dict(getattr(provider, "account_metric_support", {}) or {}), "rows": rows, "cached": True,
                               **({"error": getattr(runtime, "account_history_last_error", None), "error_code": getattr(runtime, "account_history_last_error_type", None)} if history_failed else {})}
                continue
            started = time.perf_counter()
            try:
                activities = await provider.get_account_activity(from_utc=from_utc, to_utc=to_utc, limit=5000)
                inserted = 0
                if self.db is not None:
                    for item in activities:
                        inserted += int(self.db.record_live_account_activity(_sanitize(item.as_dict())))
                latency = int((time.perf_counter() - started) * 1000)
                self._activity_refresh_at[key] = now
                if self.runtime_registry is not None:
                    self.runtime_registry.update_account_health(pid, ok=True, connection_state="connected", latency_ms=latency, history=True)
                if self.db is not None:
                    self.db.record_live_account_audit(provider_id=pid, event_type="account_history_refreshed", status="OK", latency_ms=latency,
                                                      details={"context": context, "rows_received": len(activities), "rows_inserted": inserted})
                    rows = self.db.live_account_activity(provider_id=pid, from_utc=from_utc, to_utc=to_utc)
                else:
                    rows = [_sanitize(x.as_dict()) for x in activities]
                result[pid] = {"available": True, "stale": False, "capability": capability, "metric_support": dict(getattr(provider, "account_metric_support", {}) or {}), "rows": rows, "cached": False}
            except Exception as exc:
                latency = int((time.perf_counter() - started) * 1000)
                self._activity_refresh_at[key] = now
                code, state, message = normalize_account_error(exc)
                message = self._redact_message(message)
                if self.runtime_registry is not None:
                    self.runtime_registry.update_account_health(pid, ok=False, connection_state=state.value, latency_ms=latency,
                                                                error=message, error_type=code.value, history=True)
                if self.db is not None:
                    self.db.record_live_account_audit(provider_id=pid, event_type="account_history_failed", status="ERROR", latency_ms=latency,
                                                      error_type=code.value, message=message, details={"context": context})
                    rows = self.db.live_account_activity(provider_id=pid, from_utc=from_utc, to_utc=to_utc)
                else:
                    rows = []
                result[pid] = {"available": bool(rows), "stale": bool(rows), "capability": capability, "metric_support": dict(getattr(provider, "account_metric_support", {}) or {}), "rows": rows,
                               "error": message, "error_code": code.value}
        return result

    def view(self, page: str) -> dict:
        page = str(page or "unknown")
        return {
            "ok": True, "mode": "live", "page": page, "available": False, "integration_pending": True,
            "capabilities": dict(self.capabilities), "rows": [],
            "message": "LIVE execution integration pending. Shared provider-derived market observations are available separately; no SIM economic/execution data is used as a fallback.",
            "provider": "live_account_provider", "isolated_from_sim": True,
        }

    def preflight(self, stream: str = "pre_match") -> dict:
        unknown_orders = int(self.db.unresolved_live_order_count() if self.db is not None else 0)
        provider_checks = {}
        for provider_id in self._provider_ids():
            if self.runtime_registry is None:
                continue
            status = self.runtime_registry.runtime_status(provider_id)
            provider_checks[provider_id] = self.runtime_registry.live_eligibility(
                provider_id, global_live_unlocked=False, stream=str(stream or "pre_match"),
                unresolved_unknown_orders=unknown_orders, reconciliation_clean=False,
                account_readable=bool(status.account_connected), commission_known=True, stake_limits_configured=False,
            )
        return {
            "ok": True, "mode": "live", "eligible": False, "global_live_unlocked": False,
            "stream": str(stream or "pre_match"), "unresolved_unknown_orders": unknown_orders,
            "restart_reconciliation_required": True, "allow_new_positions": False,
            "manage_existing_exposure": True, "providers": provider_checks,
            "reason": "LIVE execution remains structurally locked in 0.9.36; read-only account connectivity does not enable orders.",
            "isolated_from_sim": True,
        }

    def manifest(self) -> dict:
        providers = self.runtime_registry.manifest() if self.runtime_registry is not None else {}
        unknown_orders = int(self.db.unresolved_live_order_count() if self.db is not None else 0)
        persistence = self.db.live_persistence_counts() if self.db is not None else {}
        return {
            "mode": "live", "provider": "live_account_provider", "isolated_from_sim": True,
            "capabilities": dict(self.capabilities), "providers": providers, "live_persistence": persistence,
            "unresolved_unknown_orders": unknown_orders, "service_boundary": dict(SERVICE_BOUNDARY_MANIFEST),
            "preflight": self.preflight(),
            "future_integration": {
                "market_feed": "Use provider runtime feeds with explicit LIVE entitlement and provenance.",
                "orders": "Execution remains a separate provider boundary behind durable journal/idempotency/safety gates.",
                "settlements": "Implement provider-reported LIVE settlement adapters separately.",
            },
        }
