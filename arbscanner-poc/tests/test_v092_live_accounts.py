from __future__ import annotations

import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone

import pytest

from arbscanner import __version__
from arbscanner.account_providers import (
    AccountActivity,
    AccountActivityType,
    AccountConnectionState,
    AccountDataQuality,
    AccountProvider,
    AccountSnapshot,
    MatchbookAccountProvider,
)
from arbscanner.api import API
from arbscanner.provider_runtime import default_provider_runtime_registry

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


class FakeAccountProvider(AccountProvider):
    provider_id = "betfair"
    venue_id = "betfair"
    display_name = "Betfair"
    account_history_capability = "partial"
    account_metric_support = {"deposits": True, "withdrawals": True, "trading_pnl": True, "commission": True}

    def __init__(self):
        self.fail = False
        self.snapshot_calls = 0
        self.activity_calls = 0

    async def health(self):
        return {"ok": not self.fail, "provider_id": self.provider_id,
                "connection_state": "connected" if not self.fail else "error"}

    async def get_account_snapshot(self):
        self.snapshot_calls += 1
        if self.fail:
            raise RuntimeError("network failure")
        return AccountSnapshot(
            snapshot_id=f"fake-{self.snapshot_calls}", account_id="betfair:primary",
            provider_id="betfair", venue_id="betfair", currency="GBP",
            balance=1000.0, available_balance=925.0, reserved_balance=None,
            exposure=75.0, received_at=datetime.now(timezone.utc).isoformat(),
            connection_state=AccountConnectionState.CONNECTED,
            data_quality=AccountDataQuality.PARTIAL,
            balance_semantics="fake read-only test snapshot",
            provider_metadata={"latency_ms": 12, "session_token": "SHOULD_NOT_LEAK"},
        )

    async def get_account_activity(self, *, from_utc=None, to_utc=None, limit=1000):
        self.activity_calls += 1
        if self.fail:
            raise RuntimeError("history network failure")
        return [
            AccountActivity("betfair", "betfair", "dep-1", "2026-08-12T10:00:00+00:00", AccountActivityType.DEPOSIT, 100.0, "GBP"),
            AccountActivity("betfair", "betfair", "set-1", "2026-08-12T11:00:00+00:00", AccountActivityType.SETTLEMENT, 12.5, "GBP"),
            AccountActivity("betfair", "betfair", "fee-1", "2026-08-12T11:01:00+00:00", AccountActivityType.COMMISSION, -0.5, "GBP"),
        ]


def _install_fake(api: API, fake: FakeAccountProvider) -> None:
    # Replace the Betfair account factory but preserve the 0.9.3 runtime profile.
    api.provider_runtime._account_factories["betfair"] = lambda config, secrets: fake
    api.provider_runtime.set_runtime_enabled("matchbook", False)
    api.live_providers._account_providers = {}


def test_v092_identity_accounts_route_and_live_lock():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert 'data-tab="accounts"' in HTML
    assert 'id="accounts" class="page accounts-page"' in HTML
    assert "accounts_page" in HTML
    assert "LIVE order placement remains structurally locked" in HTML


def test_account_provider_contract_has_no_execution_methods():
    forbidden = {"place_order", "replace_order", "update_order", "cancel_order", "cancel_all", "submit_order"}
    assert not forbidden.intersection(set(dir(AccountProvider)))


def test_default_registry_provisions_pending_venues_without_account_factories():
    reg = default_provider_runtime_registry()
    manifest = reg.manifest()
    assert set(manifest) == {"betfair", "matchbook", "smarkets"}
    assert manifest["betfair"]["account_provider_registered"] is True
    assert manifest["matchbook"]["account_provider_registered"] is True
    assert manifest["smarkets"]["account_provider_registered"] is False
    assert manifest["smarkets"]["runtime_profile"]["api_state"] == "awaiting_api_access"
    assert all(manifest[x]["runtime_profile"]["orders_write_capability"] is False for x in manifest)


def test_live_snapshot_is_physically_isolated_from_sim_and_secrets_are_redacted(tmp_path):
    api = API(tmp_path / "accounts.sqlite3")
    fake = FakeAccountProvider()
    _install_fake(api, fake)
    sim_snapshots_before = api.db.conn.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0]
    state = asyncio.run(api.live_providers.account_state({**api.db.get_setting("config", {}), "account_refresh_seconds": 30}, refresh=True))
    bf = state["accounts"]["betfair"]
    assert bf["available"] == pytest.approx(925.0)
    assert bf["mode"] == "live"
    assert bf["order_placement_enabled"] is False
    payload = json.dumps(state)
    assert "SHOULD_NOT_LEAK" not in payload
    assert api.db.conn.execute("SELECT COUNT(*) FROM live_account_snapshots").fetchone()[0] == 1
    assert api.db.conn.execute("SELECT COUNT(*) FROM live_accounts").fetchone()[0] == 1
    assert api.db.conn.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0] == sim_snapshots_before
    assert api.db.conn.execute("SELECT COUNT(*) FROM sim_account_adjustments").fetchone()[0] == 0
    api.db.conn.close()


def test_failed_live_read_retains_last_valid_snapshot_and_marks_stale(tmp_path):
    api = API(tmp_path / "stale.sqlite3")
    fake = FakeAccountProvider()
    _install_fake(api, fake)
    cfg = {**api.db.get_setting("config", {}), "account_refresh_seconds": 5, "account_balance_stale_seconds": 90}
    first = asyncio.run(api.live_providers.account_state(cfg, refresh=True))
    assert first["accounts"]["betfair"]["available"] == pytest.approx(925.0)
    fake.fail = True
    second = asyncio.run(api.live_providers.account_state(cfg, refresh=True))
    bf = second["accounts"]["betfair"]
    assert bf["available"] == pytest.approx(925.0)
    assert bf["freshness"] == "STALE"
    assert bf["connection_state"] in {"disconnected", "error"}
    assert bf["available"] != 0
    calls = fake.snapshot_calls
    third = asyncio.run(api.live_providers.account_state(cfg, refresh=False))
    assert fake.snapshot_calls == calls  # failure state is cached until provider runtime cadence permits a retry
    assert third["accounts"]["betfair"]["freshness"] == "STALE"
    api.db.conn.close()


def test_pending_providers_do_not_create_network_clients(tmp_path):
    api = API(tmp_path / "pending.sqlite3")
    state = asyncio.run(api.live_providers.account_state(api.db.get_setting("config", {}), refresh=True))
    assert state["accounts"]["smarkets"]["integration_pending"] is True
    assert state["accounts"]["smarkets"]["available"] is None
    assert "smarkets" not in api.live_providers._account_providers
    api.db.conn.close()


def test_matchbook_transfer_is_not_inferred_as_deposit_or_withdrawal():
    assert MatchbookAccountProvider._classify_transaction("transfer") == AccountActivityType.OTHER
    assert MatchbookAccountProvider._classify_transaction("commission") == AccountActivityType.COMMISSION
    assert MatchbookAccountProvider._classify_transaction("payout") == AccountActivityType.SETTLEMENT
    assert MatchbookAccountProvider.account_metric_support["deposits"] is False
    assert MatchbookAccountProvider.account_metric_support["trading_pnl"] is True


def test_accounts_page_live_period_metrics_are_read_only_and_conservative(tmp_path):
    api = API(tmp_path / "page.sqlite3")
    fake = FakeAccountProvider()
    _install_fake(api, fake)
    result = api.accounts_page({"mode": "live", "period": "ALL", "refresh": True, "timezone_offset_minutes": -60})
    assert result["ok"] is True
    assert result["mode"] == "live"
    assert result["read_only"] is True
    assert result["live_order_placement"] is False
    assert result["current"]["available"] == pytest.approx(925.0)
    row = next(x for x in result["providers"] if x["provider_id"] == "betfair")
    assert row["period"]["deposited"] == pytest.approx(100.0)
    assert row["period"]["trading_pnl"] == pytest.approx(12.5)
    assert row["period"]["commission"] == pytest.approx(0.5)
    # One account snapshot is not enough to invent a period balance reconciliation.
    assert row["period"]["net_account_change"] is None
    assert result["reconciliation"]["status"] == "UNAVAILABLE"
    assert fake.snapshot_calls == 1
    assert fake.activity_calls == 1
    api.db.conn.close()


def test_runtime_refresh_cadence_prevents_ui_polling_from_hammering_provider(tmp_path):
    api = API(tmp_path / "cadence.sqlite3")
    fake = FakeAccountProvider()
    _install_fake(api, fake)
    cfg = {**api.db.get_setting("config", {}), "account_refresh_seconds": 60}
    asyncio.run(api.live_providers.account_state(cfg, refresh=False))
    asyncio.run(api.live_providers.account_state(cfg, refresh=False))
    assert fake.snapshot_calls == 1
    api.db.conn.close()





def test_failed_account_history_retains_rows_as_stale_and_respects_history_cache(tmp_path):
    api = API(tmp_path / "history-failure.sqlite3")
    fake = FakeAccountProvider()
    _install_fake(api, fake)
    cfg = {**api.db.get_setting("config", {}), "account_history_cache_seconds": 120}
    first = asyncio.run(api.live_providers.refresh_account_activity(cfg, from_utc=None, to_utc=None, refresh=True))
    assert first["betfair"]["available"] is True
    fake.fail = True
    failed = asyncio.run(api.live_providers.refresh_account_activity(cfg, from_utc=None, to_utc=None, refresh=True))
    assert failed["betfair"]["available"] is True
    assert failed["betfair"]["stale"] is True
    calls = fake.activity_calls
    cached = asyncio.run(api.live_providers.refresh_account_activity(cfg, from_utc=None, to_utc=None, refresh=False))
    assert fake.activity_calls == calls
    assert cached["betfair"]["stale"] is True
    assert cached["betfair"]["error"]
    api.db.conn.close()

def test_account_history_cache_is_period_start_based_not_exact_now_timestamp(tmp_path):
    api = API(tmp_path / "history-cache.sqlite3")
    fake = FakeAccountProvider()
    _install_fake(api, fake)
    cfg = {**api.db.get_setting("config", {}), "account_history_cache_seconds": 120}
    asyncio.run(api.live_providers.refresh_account_activity(cfg, from_utc="2026-08-12T00:00:00+00:00",
                                                            to_utc="2026-08-12T14:00:00+00:00", refresh=False))
    asyncio.run(api.live_providers.refresh_account_activity(cfg, from_utc="2026-08-12T00:00:00+00:00",
                                                            to_utc="2026-08-12T14:00:30+00:00", refresh=False))
    assert fake.activity_calls == 1
    api.db.conn.close()

def test_provider_client_is_reused_across_manual_refresh_and_can_be_invalidated(tmp_path):
    api = API(tmp_path / "provider-session.sqlite3")
    created = []
    def factory(config, secrets):
        fake = FakeAccountProvider()
        created.append(fake)
        return fake
    api.provider_runtime._account_factories["betfair"] = factory
    api.provider_runtime.set_runtime_enabled("matchbook", False)
    api.live_providers._account_providers = {}
    cfg = {**api.db.get_setting("config", {}), "account_refresh_seconds": 30}
    asyncio.run(api.live_providers.account_state(cfg, refresh=True))
    asyncio.run(api.live_providers.account_state(cfg, refresh=True))
    assert len(created) == 1
    api.live_providers.invalidate_account_providers("betfair")
    asyncio.run(api.live_providers.account_state(cfg, refresh=True))
    assert len(created) == 2
    api.db.conn.close()


def test_global_account_refresh_interval_controls_runtime_cache(tmp_path):
    api = API(tmp_path / "refresh-config.sqlite3")
    fake = FakeAccountProvider()
    _install_fake(api, fake)
    cfg = {**api.db.get_setting("config", {}), "account_refresh_seconds": 60}
    asyncio.run(api.live_providers.account_state(cfg, refresh=True))
    assert fake.snapshot_calls == 1
    old = "2026-08-12T14:00:00+00:00"
    with api.db.lock:
        api.db.conn.execute("UPDATE live_account_snapshots SET received_at=? WHERE provider_id='betfair'", (old,))
        api.db.conn.commit()
    # A very old snapshot is refreshed regardless of configured interval. Now make it 45 seconds old.
    from datetime import timedelta
    recent = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
    with api.db.lock:
        api.db.conn.execute("UPDATE live_account_snapshots SET received_at=? WHERE provider_id='betfair'", (recent,))
        api.db.conn.commit()
    asyncio.run(api.live_providers.account_state(cfg, refresh=False))
    assert fake.snapshot_calls == 1
    cfg["account_refresh_seconds"] = 30
    asyncio.run(api.live_providers.account_state(cfg, refresh=False))
    assert fake.snapshot_calls == 2
    api.db.conn.close()

def test_live_reads_do_not_unlock_execution(tmp_path):
    api = API(tmp_path / "lock.sqlite3")
    fake = FakeAccountProvider()
    _install_fake(api, fake)
    asyncio.run(api.live_providers.account_state(api.db.get_setting("config", {}), refresh=True))
    gate = api.live_preflight({"stream": "pre_match"})
    assert gate["eligible"] is False
    assert gate["global_live_unlocked"] is False
    assert gate["allow_new_positions"] is False
    assert api.set_operating_mode({"mode": "live"})["ok"] is False
    api.db.conn.close()



def test_account_history_health_is_independent_from_snapshot_health():
    reg = default_provider_runtime_registry()
    reg.update_account_health("betfair", ok=True, connection_state="connected", latency_ms=10)
    reg.update_account_health("betfair", ok=False, connection_state="error", latency_ms=20,
                              error="history unavailable", error_type="NETWORK_ERROR", history=True)
    status = reg.runtime_status("betfair")
    assert status.account_connected is True
    assert status.account_connection_state == "connected"
    assert status.account_history_connection_state == "error"
    assert status.account_history_last_error == "history unavailable"
    assert status.last_error is None


def test_live_diagnostics_redact_configured_secret_values(tmp_path):
    api = API(tmp_path / "redact.sqlite3")
    api.secrets.set("betfair_app_key", "TOPSECRET-APP-KEY")
    message = api.live_providers._redact_message("provider rejected TOPSECRET-APP-KEY")
    assert "TOPSECRET-APP-KEY" not in message
    assert "[REDACTED]" in message
    api.db.conn.close()

def test_accounts_frontend_is_registry_driven_not_provider_branching():
    start = HTML.index("function accountProviderCard092")
    end = HTML.index("function renderAccountsPage092", start)
    fn = HTML[start:end].lower()
    assert "provider_id==='betfair'" not in fn
    assert 'provider_id==="betfair"' not in fn
    assert "provider_id==='matchbook'" not in fn
    assert 'provider_id==="matchbook"' not in fn
    assert "api_state" not in fn or "pending" in fn
    assert "canonical sim virtual venue account" in fn
    assert "sim controls" not in fn
    assert "live controls" not in fn
    assert "actual live provider/account state only" in fn
    assert "refreshVisibleLiveAccounts092" in HTML
