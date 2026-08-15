from __future__ import annotations

"""Read-only LIVE account provider boundary for ArbScanner 0.9.8.

The contracts in this module deliberately contain no order-placement methods.
Provider-native clients/responses stay behind this boundary and are normalised to
serialisable account snapshots/activity for the core/UI.  This keeps LIVE account
observation independent from execution and service-boundary ready without gRPC.
"""

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

import httpx

from .adapters import BetfairDelayedAdapter, ExchangeError, MatchbookAdapter
from .models import utc_now_iso


class AccountConnectionState(str, Enum):
    NOT_CONFIGURED = "not_configured"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTH_FAILED = "auth_failed"
    API_NOT_AUTHORISED = "api_not_authorised"
    DISCONNECTED = "disconnected"
    STALE = "stale"
    ERROR = "error"


class AccountDataQuality(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class AccountActivityType(str, Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    SETTLEMENT = "SETTLEMENT"
    COMMISSION = "COMMISSION"
    ADJUSTMENT = "ADJUSTMENT"
    OTHER = "OTHER"


class NormalizedAccountError(str, Enum):
    AUTH_FAILED = "AUTH_FAILED"
    API_NOT_AUTHORISED = "API_NOT_AUTHORISED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    EXCHANGE_UNAVAILABLE = "EXCHANGE_UNAVAILABLE"
    BAD_RESPONSE = "BAD_RESPONSE"
    CONFIG_MISSING = "CONFIG_MISSING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AccountSnapshot:
    snapshot_id: str
    account_id: str
    provider_id: str
    venue_id: str
    currency: str | None
    balance: float | None
    available_balance: float | None
    reserved_balance: float | None
    exposure: float | None
    credit: float | None = None
    source_timestamp: str | None = None
    received_at: str = field(default_factory=utc_now_iso)
    is_stale: bool = False
    connection_state: AccountConnectionState = AccountConnectionState.CONNECTED
    data_quality: AccountDataQuality = AccountDataQuality.PARTIAL
    balance_semantics: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    provider_account_ref: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["connection_state"] = self.connection_state.value
        out["data_quality"] = self.data_quality.value
        return out


@dataclass(frozen=True)
class AccountActivity:
    provider_id: str
    venue_id: str
    activity_id: str
    timestamp: str
    activity_type: AccountActivityType
    amount: float
    currency: str | None = None
    balance_after: float | None = None
    reference: str | None = None
    description: str | None = None
    provider_native_type: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["activity_type"] = self.activity_type.value
        return out


class AccountProvider:
    """Read-only account contract.  No execution methods exist by design."""

    provider_id = "base"
    venue_id = "base"
    display_name = "Base"
    account_history_capability = "none"
    account_metric_support = {"deposits": False, "withdrawals": False, "trading_pnl": False, "commission": False}

    async def connect(self) -> dict[str, Any]:
        return await self.health()

    async def disconnect(self) -> dict[str, Any]:
        return {"ok": True, "provider_id": self.provider_id, "connection_state": AccountConnectionState.DISCONNECTED.value}

    async def health(self) -> dict[str, Any]:
        raise NotImplementedError

    async def get_account_snapshot(self) -> AccountSnapshot:
        raise NotImplementedError

    async def get_account_activity(self, *, from_utc: str | None = None, to_utc: str | None = None,
                                   limit: int = 1000) -> list[AccountActivity]:
        return []

    async def refresh_session(self) -> dict[str, Any]:
        return await self.health()


def normalize_account_error(exc: Exception) -> tuple[NormalizedAccountError, AccountConnectionState, str]:
    text = str(exc or "Unknown account provider error")
    low = text.lower()
    if "not configured" in low or "configured" in low and "not" in low:
        return NormalizedAccountError.CONFIG_MISSING, AccountConnectionState.NOT_CONFIGURED, text
    if "401" in low or "unauthor" in low or "session" in low and "expired" in low:
        return NormalizedAccountError.AUTH_FAILED, AccountConnectionState.AUTH_FAILED, text
    if "403" in low or "not authorised" in low or "not authorized" in low or "permission" in low:
        return NormalizedAccountError.API_NOT_AUTHORISED, AccountConnectionState.API_NOT_AUTHORISED, text
    if "429" in low or "rate limit" in low:
        return NormalizedAccountError.RATE_LIMITED, AccountConnectionState.ERROR, text
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)) or "timeout" in low:
        return NormalizedAccountError.TIMEOUT, AccountConnectionState.ERROR, text
    if isinstance(exc, httpx.NetworkError) or "network" in low or "connect" in low:
        return NormalizedAccountError.NETWORK_ERROR, AccountConnectionState.DISCONNECTED, text
    if "invalid json" in low or "bad response" in low:
        return NormalizedAccountError.BAD_RESPONSE, AccountConnectionState.ERROR, text
    return NormalizedAccountError.UNKNOWN, AccountConnectionState.ERROR, text


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_iso(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except Exception:
        return text


class BetfairAccountProvider(AccountProvider):
    provider_id = "betfair"
    venue_id = "betfair"
    display_name = "Betfair"
    account_history_capability = "partial"
    account_metric_support = {"deposits": True, "withdrawals": True, "trading_pnl": True, "commission": True}

    def __init__(self, *, app_key: str | None, session_token: str | None):
        self.adapter = BetfairDelayedAdapter(app_key=app_key, session_token=session_token)

    def _configured(self) -> bool:
        return bool(self.adapter.app_key and self.adapter.session_token)

    async def health(self) -> dict[str, Any]:
        if not self._configured():
            return {"ok": False, "provider_id": self.provider_id, "connection_state": AccountConnectionState.NOT_CONFIGURED.value,
                    "error_code": NormalizedAccountError.CONFIG_MISSING.value}
        started = time.perf_counter()
        try:
            await self.adapter._account_rpc("getAccountDetails")
            return {"ok": True, "provider_id": self.provider_id, "connection_state": AccountConnectionState.CONNECTED.value,
                    "latency_ms": int((time.perf_counter() - started) * 1000)}
        except Exception as exc:
            code, state, message = normalize_account_error(exc)
            return {"ok": False, "provider_id": self.provider_id, "connection_state": state.value,
                    "error_code": code.value, "message": message, "latency_ms": int((time.perf_counter() - started) * 1000)}

    async def get_account_snapshot(self) -> AccountSnapshot:
        if not self._configured():
            raise ExchangeError("Betfair delayed app key/session token not configured")
        raw = await self.adapter.account_balance()
        available = _float(raw.get("available"))
        exposure = _float(raw.get("exposure"))
        details = ((raw.get("raw") or {}).get("details") or {}) if isinstance(raw.get("raw"), dict) else {}
        account_ref = str(details.get("accountId") or details.get("accountId") or "") or None
        received = str(raw.get("captured_at") or utc_now_iso())
        # getAccountFunds supplies available-to-bet and current exposure, not an
        # unambiguous total-equity field.  Keep balance null rather than inventing it.
        return AccountSnapshot(
            snapshot_id=f"betfair:{received}", account_id="betfair:primary", provider_id=self.provider_id,
            venue_id=self.venue_id, currency=(str(raw.get("currency") or "").upper() or None),
            balance=None, available_balance=available, reserved_balance=None, exposure=exposure,
            source_timestamp=None, received_at=received, is_stale=False,
            connection_state=AccountConnectionState.CONNECTED, data_quality=AccountDataQuality.PARTIAL,
            balance_semantics="Betfair availableToBetBalance + exposure; total account equity not fabricated",
            provider_account_ref=account_ref,
            provider_metadata={"retained_commission": _float(raw.get("retained_commission")), "latency_ms": raw.get("latency_ms")},
        )

    @staticmethod
    def _classify_statement(item: Mapping[str, Any]) -> AccountActivityType:
        raw_type = str(item.get("itemClass") or item.get("itemClassData") or item.get("description") or "").upper()
        if "DEPOSIT" in raw_type:
            return AccountActivityType.DEPOSIT
        if "WITHDRAW" in raw_type:
            return AccountActivityType.WITHDRAWAL
        if "COMMISSION" in raw_type or "FEE" in raw_type:
            return AccountActivityType.COMMISSION
        if any(x in raw_type for x in ("EXCHANGE", "SETTLE", "BET", "MARKET")):
            return AccountActivityType.SETTLEMENT
        if any(x in raw_type for x in ("ADJUST", "TRANSFER", "PAYMENT")):
            return AccountActivityType.ADJUSTMENT
        return AccountActivityType.OTHER

    async def get_account_activity(self, *, from_utc: str | None = None, to_utc: str | None = None,
                                   limit: int = 1000) -> list[AccountActivity]:
        if not self._configured():
            raise ExchangeError("Betfair delayed app key/session token not configured")
        out: list[AccountActivity] = []
        offset = 0
        page_size = 100
        while len(out) < max(1, min(int(limit), 5000)):
            params: dict[str, Any] = {"fromRecord": offset, "recordCount": page_size, "includeItem": "ALL"}
            if from_utc or to_utc:
                params["itemDateRange"] = {}
                if from_utc:
                    params["itemDateRange"]["from"] = str(from_utc)
                if to_utc:
                    params["itemDateRange"]["to"] = str(to_utc)
            report = await self.adapter._account_rpc("getAccountStatement", params)
            rows = report.get("accountStatement") if isinstance(report, dict) else []
            rows = rows if isinstance(rows, list) else []
            for idx, item in enumerate(rows):
                if not isinstance(item, dict):
                    continue
                ts = _safe_iso(item.get("itemDate")) or utc_now_iso()
                amount = _float(item.get("amount"))
                if amount is None:
                    continue
                ref = str(item.get("refId") or item.get("reference") or "") or None
                native = str(item.get("itemClass") or "") or None
                activity_id = ref or f"betfair:{ts}:{offset+idx}:{amount:.8f}"
                out.append(AccountActivity(
                    provider_id=self.provider_id, venue_id=self.venue_id, activity_id=activity_id,
                    timestamp=ts, activity_type=self._classify_statement(item), amount=amount,
                    currency=None, balance_after=_float(item.get("balance")), reference=ref,
                    description=str(item.get("itemClassData") or item.get("legacyData") or "") or None,
                    provider_native_type=native,
                    provider_metadata={"item_class": native},
                ))
                if len(out) >= limit:
                    break
            more = bool(report.get("moreAvailable")) if isinstance(report, dict) else False
            if not more or not rows or len(out) >= limit:
                break
            offset += len(rows)
        return out


class MatchbookAccountProvider(AccountProvider):
    provider_id = "matchbook"
    venue_id = "matchbook"
    display_name = "Matchbook"
    account_history_capability = "partial"
    account_metric_support = {"deposits": False, "withdrawals": False, "trading_pnl": True, "commission": True}

    def __init__(self, *, session_token: str | None, username: str | None, password: str | None):
        self.adapter = MatchbookAdapter(session_token=session_token, username=username, password=password)

    def _configured(self) -> bool:
        return bool(self.adapter.session_token or (self.adapter.username and self.adapter.password))

    async def health(self) -> dict[str, Any]:
        if not self._configured():
            return {"ok": False, "provider_id": self.provider_id, "connection_state": AccountConnectionState.NOT_CONFIGURED.value,
                    "error_code": NormalizedAccountError.CONFIG_MISSING.value}
        started = time.perf_counter()
        try:
            await self.adapter.account_balance()
            return {"ok": True, "provider_id": self.provider_id, "connection_state": AccountConnectionState.CONNECTED.value,
                    "latency_ms": int((time.perf_counter() - started) * 1000)}
        except Exception as exc:
            code, state, message = normalize_account_error(exc)
            return {"ok": False, "provider_id": self.provider_id, "connection_state": state.value,
                    "error_code": code.value, "message": message, "latency_ms": int((time.perf_counter() - started) * 1000)}

    async def get_account_snapshot(self) -> AccountSnapshot:
        if not self._configured():
            raise ExchangeError("Matchbook username/password or session token not configured")
        raw = await self.adapter.account_balance()
        available = _float(raw.get("available"))
        exposure = _float(raw.get("exposure"))
        received = str(raw.get("captured_at") or utc_now_iso())
        # Matchbook's balance endpoint exposes the logged-in wallet balance.  We
        # preserve that as both available and balance only when it is explicitly
        # present; no reserved/equity field is invented.
        return AccountSnapshot(
            snapshot_id=f"matchbook:{received}", account_id="matchbook:primary", provider_id=self.provider_id,
            venue_id=self.venue_id, currency=(str(raw.get("currency") or "").upper() or None),
            balance=available, available_balance=available, reserved_balance=None, exposure=exposure,
            received_at=received, is_stale=False, connection_state=AccountConnectionState.CONNECTED,
            data_quality=AccountDataQuality.PARTIAL,
            balance_semantics="Matchbook account/balance wallet value; unsupported reserve fields remain null",
            provider_metadata={"latency_ms": raw.get("latency_ms")},
        )

    @staticmethod
    def _classify_transaction(native_type: str) -> AccountActivityType:
        kind = str(native_type or "").strip().lower()
        if kind == "commission":
            return AccountActivityType.COMMISSION
        if kind == "payout":
            return AccountActivityType.SETTLEMENT
        if kind in {"manual", "bonus"}:
            return AccountActivityType.ADJUSTMENT
        # Matchbook documents 'transfer' but does not guarantee it is an external
        # deposit/withdrawal.  Keep it OTHER rather than corrupting capital-added KPIs.
        return AccountActivityType.OTHER

    async def _ensure_session(self) -> None:
        if not self.adapter.session_token:
            await self.adapter.login()

    async def get_account_activity(self, *, from_utc: str | None = None, to_utc: str | None = None,
                                   limit: int = 1000) -> list[AccountActivity]:
        if not self._configured():
            raise ExchangeError("Matchbook username/password or session token not configured")
        await self._ensure_session()
        headers = {"Accept": "application/json", "User-Agent": "ArbScanner-PoC/0.9.36"}
        if self.adapter.session_token:
            headers["session-token"] = self.adapter.session_token
        out: list[AccountActivity] = []
        offset = 0
        page_size = min(100, max(20, int(limit)))
        async with httpx.AsyncClient(timeout=20) as client:
            while len(out) < max(1, min(int(limit), 5000)):
                params: dict[str, Any] = {"offset": offset, "per-page": page_size}
                if from_utc:
                    params["after"] = str(from_utc)
                if to_utc:
                    params["before"] = str(to_utc)
                started = time.perf_counter()
                r = await client.get(f"{self.adapter.EDGE_BASE}/reports/v1/transactions", params=params, headers=headers)
                if r.status_code == 401 and self.adapter.username and self.adapter.password:
                    await self.adapter.login()
                    headers["session-token"] = self.adapter.session_token or ""
                    r = await client.get(f"{self.adapter.EDGE_BASE}/reports/v1/transactions", params=params, headers=headers)
                if r.status_code >= 400:
                    raise ExchangeError(f"Matchbook account transactions failed ({r.status_code})")
                try:
                    data = r.json()
                except Exception as exc:
                    raise ExchangeError(f"Matchbook account transactions returned invalid JSON: {exc}") from exc
                rows = data.get("transactions") if isinstance(data, dict) else data
                if not isinstance(rows, list):
                    rows = []
                for idx, item in enumerate(rows):
                    if not isinstance(item, dict):
                        continue
                    native = str(item.get("transaction-type") or item.get("transaction_type") or item.get("type") or "")
                    amount = _float(item.get("amount") or item.get("value"))
                    if amount is None:
                        continue
                    ts = _safe_iso(item.get("created-at") or item.get("created_at") or item.get("timestamp") or item.get("date")) or utc_now_iso()
                    ref = str(item.get("id") or item.get("transaction-id") or item.get("transaction_id") or item.get("reference") or "") or None
                    activity_id = ref or f"matchbook:{ts}:{offset+idx}:{amount:.8f}"
                    out.append(AccountActivity(
                        provider_id=self.provider_id, venue_id=self.venue_id, activity_id=activity_id,
                        timestamp=ts, activity_type=self._classify_transaction(native), amount=amount,
                        currency=(str(item.get("currency") or "").upper() or None),
                        balance_after=_float(item.get("balance") or item.get("balance-after") or item.get("balance_after")),
                        reference=ref, description=str(item.get("description") or item.get("comment") or "") or None,
                        provider_native_type=native or None,
                        provider_metadata={"latency_ms": int((time.perf_counter() - started) * 1000)},
                    ))
                    if len(out) >= limit:
                        break
                if not rows or len(rows) < page_size or len(out) >= limit:
                    break
                offset += len(rows)
        return out
