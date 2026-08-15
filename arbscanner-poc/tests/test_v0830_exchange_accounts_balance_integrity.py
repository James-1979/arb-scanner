from datetime import datetime, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _open_two_way(api: API):
    db = api.db
    now = datetime.now(timezone.utc).isoformat()
    legs = [
        {"exchange": "Betfair delayed", "selection": "A", "odds": 2.1, "liquidity": 100},
        {"exchange": "Matchbook", "selection": "B", "odds": 2.1, "liquidity": 100},
    ]
    oid = db.add_opportunity(
        "account-event", "Account Event", now, "Match Winner", 1.0, 1.0,
        legs, [], 1.0, "account-sig", strategy="two-way", sport="Tennis",
    )
    ok, reason = db.open_monitor_position(
        opportunity_id=oid, execution_run_id=None, event_key="account-event",
        market_name="Match Winner", deployed=20.0, expected_profit=1.0,
        stakes_by_exchange={"betfair": 10.0, "matchbook": 10.0},
        outcome_exchange_pnls={
            "A": {"betfair": 11.0, "matchbook": -10.0},
            "B": {"betfair": -10.0, "matchbook": 11.0},
        },
        simulation={"after_hedge": {"balanced": True, "worst_case_pnl": 1.0}},
        stream="pre_match",
    )
    assert ok is True and reason is None
    return oid


def test_v0830_release_account_ui_and_live_safety_boundary():
    assert __version__ == "0.9.36"
    for token in (
        '<title>ArbScanner PoC 0.9.36</title>',
        'id="dashboardAccountContext"',
        'id="dashboardExchangeAccounts"',
        'id="adminExchangeAccounts"',
        'id="adminAccountIntegrity"',
        'id="globalDataModeSim"',
        'id="globalDataModeLive"',
        'id="adminAccountModeBadge"',
        'assets/betfair-mark.svg',
        'assets/matchbook-mark.svg',
        'LIVE account configuration.',
        'central LIVE order placement remains locked.',
        'id="adminAccountIntegrity"',
        'accountBasisSections',
    ):
        assert token in HTML
    assert callable(getattr(API, 'account_timeline', None))


def test_monitor_account_model_separates_exchange_accounts_and_allocations(tmp_path):
    api = API(tmp_path / "accounts.sqlite3")
    result = api.account_overview({"mode": "monitor"})
    assert result["ok"] is True
    assert result["mode"] == "sim"
    assert result["live_order_placement"] is False
    assert result["reconciliation"]["status"] == "RECONCILED"
    for key in ("betfair", "matchbook"):
        account = result["accounts"][key]
        assert account["source"] == "virtual_ledger"
        assert account["currency"] == "GBP"
        assert account["available"] == 750.0
        assert account["reserved"] == 0.0
        assert account["equity"] == 750.0
        assert [x["stream"] for x in account["allocations"]] == ["pre_match", "in_play", "racing"]
        assert sum(x["equity"] for x in account["allocations"]) == account["equity"]
        assert account["order_placement_enabled"] is False


def test_position_reservation_and_settlement_reconcile_to_account_ledger(tmp_path):
    api = API(tmp_path / "reserve.sqlite3")
    oid = _open_two_way(api)

    opened = api.account_overview({"mode": "monitor"})
    assert opened["accounts"]["betfair"]["available"] == 740.0
    assert opened["accounts"]["betfair"]["reserved"] == 10.0
    assert opened["accounts"]["matchbook"]["available"] == 740.0
    assert opened["accounts"]["matchbook"]["reserved"] == 10.0
    assert opened["accounts"]["betfair"]["equity"] == 750.0
    assert api.account_integrity_report({})["status"] == "RECONCILED"

    contexts = [x["context"] for x in api.db.account_snapshot_history(mode="monitor")]
    assert "startup" in contexts
    assert "execution_reserve_before" in contexts
    assert "execution_reserve_after" in contexts

    settled = api.db.settle_monitor_position(oid, "A")
    assert settled["ok"] is True
    assert settled["realized_pnl"] == 1.0
    after = api.account_overview({"mode": "monitor"})
    assert after["accounts"]["betfair"]["reserved"] == 0.0
    assert after["accounts"]["matchbook"]["reserved"] == 0.0
    assert after["accounts"]["betfair"]["equity"] == 761.0
    assert after["accounts"]["matchbook"]["equity"] == 740.0
    assert api.account_integrity_report({})["status"] == "RECONCILED"
    contexts = [x["context"] for x in api.db.account_snapshot_history(mode="monitor")]
    assert "settlement_before_release" in contexts
    assert "settlement_after_release" in contexts


def test_account_timeline_exposes_only_canonical_exchange_level_checkpoints(tmp_path):
    api = API(tmp_path / "timeline.sqlite3")
    _open_two_way(api)
    result = api.account_timeline({"mode": "monitor", "limit": 100})
    assert result["ok"] is True
    assert result["live_execution_allowed"] is False
    assert result["rows"]
    assert all(row["stream"] is None for row in result["rows"])
    assert {row["exchange"] for row in result["rows"]} == {"betfair", "matchbook"}


def test_monitor_and_live_snapshot_datasets_are_isolated(tmp_path):
    api = API(tmp_path / "isolation.sqlite3")
    api.db.record_account_snapshot(
        mode="live", exchange="betfair", currency="EUR", source="exchange_api",
        available=123.0, reserved=0.0, exposure=2.0, equity=125.0,
        context="test_live",
    )
    monitor = api.db.account_snapshot_history(mode="monitor")
    live = api.db.account_snapshot_history(mode="live")
    assert monitor and all(x["mode"] == "sim" for x in monitor)
    assert len(live) == 1
    assert live[0]["mode"] == "live"
    assert live[0]["currency"] == "EUR"
    assert live[0]["source"] == "exchange_api"


def test_account_currency_and_safety_settings_validate(tmp_path):
    api = API(tmp_path / "settings.sqlite3")
    saved = api.save_settings({"config": {
        "account_currency": "eur",
        "account_balance_stale_seconds": 45,
        "account_reconciliation_tolerance": 0.02,
    }})
    cfg = saved["settings"]["config"]
    assert cfg["account_currency"] == "EUR"
    assert cfg["account_balance_stale_seconds"] == 45
    assert cfg["account_reconciliation_tolerance"] == 0.02
    saved = api.save_settings({"config": {"account_currency": "££"}})
    assert saved["settings"]["config"]["account_currency"] == "GBP"


def test_trading_data_reset_includes_account_audit_tables():
    from arbscanner.db import DB
    assert "account_snapshots" in DB.TRADING_DATA_TABLES
    assert "balance_reconciliations" in DB.TRADING_DATA_TABLES


def test_open_position_persists_currency_at_execution_time(tmp_path):
    api = API(tmp_path / "position_currency.sqlite3")
    api.save_settings({"config": {"account_currency": "EUR"}})
    oid = _open_two_way(api)
    row = next(x for x in api.dashboard_overview({})["rows"] if x["opportunity_id"] == oid)
    assert row["currency"] == "EUR"
    with api.db.lock:
        stored = api.db.conn.execute("SELECT currency FROM monitor_positions WHERE opportunity_id=?", (oid,)).fetchone()
    assert stored[0] == "EUR"
