from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0834_release_ui_contract():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    # Dashboard is automatic: old operator layout controls and bottom Performance block are gone.
    for token in ('id="displayProfile"', 'id="dashboardProfileAuto"', 'id="dashboardFit"', 'id="dashboardFull"', 'id="performanceCard"'):
        assert token not in HTML
    for token in (
        'database_compaction_status', 'compact_database', 'id="dbCompactBtn"',
        'Current SIM accounts', 'Current market budget', 'id="replayTimeBasis"',
        'ACTUAL SIM PERFORMANCE', 'Scenario selection funnel',
        'normal deployable', 'ring-fenced hedge reserve',
    ):
        assert token in HTML


def test_generic_admin_save_cannot_change_feed_enable_flags(tmp_path):
    api = API(tmp_path / "feeds.sqlite3")
    assert api.set_feed_enabled({"exchange": "betfair", "mode": "sim", "enabled": False})["ok"]
    assert api.set_feed_enabled({"exchange": "matchbook", "mode": "sim", "enabled": True})["ok"]

    # Simulate stale checkbox values arriving in a generic Admin save. They must be ignored.
    api.save_settings({"config": {
        "scan_interval_seconds": 47,
        "betfair_enabled": True,
        "matchbook_enabled": False,
    }})
    cfg = api.db.get_setting("config", {})
    assert cfg["betfair_enabled"] is False
    assert cfg["matchbook_enabled"] is True
    assert int(cfg["scan_interval_seconds"]) == 47

    feeds = {x["key"]: x for x in api._operational_status()["feeds"]}
    assert feeds["betfair"]["state"] == "disabled"
    assert feeds["betfair"]["message"] == "Feed disabled in both SIM and LIVE"


def test_market_budget_reports_ring_fenced_reserve_and_normal_deployable(tmp_path):
    api = API(tmp_path / "reserve.sqlite3")
    result = api.sim_portfolio_budget_update({
        "targets": {
            "pre_match": {"betfair": 300.0, "matchbook": 200.0},
            "in_play": {"betfair": 200.0, "matchbook": 300.0},
            "racing": {"betfair": 250.0, "matchbook": 250.0},
        },
        "hedge_reserve_amounts": {"pre_match": 100.0, "in_play": 50.0, "racing": 0.0},
    })
    assert result["ok"] is True
    rows = {x["stream"]: x for x in api.sim_portfolio_budget_overview({})["rows"]}
    pre = rows["pre_match"]
    assert pre["total_allocation"] == pytest.approx(500.0)
    assert pre["hedge_reserve_amount"] == pytest.approx(100.0)
    assert pre["normal_deployable"] == pytest.approx(400.0)
    assert pre["total_allocation"] == pytest.approx(pre["hedge_reserve_amount"] + pre["normal_deployable"])

    sources = api.scenario_capital_sources({})
    budget = sources["budgets"]["pre_match"]
    assert budget["hedge_reserve"] == pytest.approx(100.0)
    assert budget["betfair_normal_deployable"] == pytest.approx(240.0)
    assert budget["matchbook_normal_deployable"] == pytest.approx(160.0)
    assert budget["normal_deployable_allocation"] == pytest.approx(400.0)


def test_replay_account_timeline_exposes_first_checkpoint_without_fabricating_opening(tmp_path):
    api = API(tmp_path / "timeline.sqlite3")
    # Remove startup audit noise so the test controls the beginning of account history.
    with api.db.lock:
        api.db.conn.execute("DELETE FROM account_snapshots")
        api.db.conn.commit()
    first = "2026-08-11T20:00:00+00:00"
    api.db.record_account_snapshot(mode="monitor", exchange="betfair", currency="GBP", source="virtual_ledger",
                                   available=100, captured_at=first, context="test")
    api.db.record_account_snapshot(mode="monitor", exchange="matchbook", currency="GBP", source="virtual_ledger",
                                   available=200, captured_at=first, context="test")
    result = api.account_timeline({
        "mode": "monitor", "from_utc": "2026-08-11T12:00:00+00:00", "to_utc": "2026-08-11T21:00:00+00:00"
    })
    assert result["opening"] == {}
    assert result["first_available"]["betfair"]["captured_at"] == first
    assert result["first_available"]["matchbook"]["captured_at"] == first


def test_performance_exposes_period_end_capital_for_historical_comparison(tmp_path):
    api = API(tmp_path / "perf.sqlite3")
    result = api.performance_analytics({
        "period": "custom", "from_utc": "2026-08-10T00:00:00+00:00", "to_utc": "2026-08-11T00:00:00+00:00",
        "scope": "sports", "stream": "all", "basis": "actual",
    })
    assert result["ok"] is True
    summary = result["summary"]
    assert summary["period_end_capital"] == pytest.approx(summary["period_start_capital"] + summary["period_profit"])


def test_compaction_api_pauses_backs_up_compacts_and_resumes(tmp_path, monkeypatch):
    api = API(tmp_path / "compact.sqlite3")
    calls = []
    monkeypatch.setattr(api.service, "pause", lambda: calls.append("pause") or {"ok": True, "was_loaded": True})
    monkeypatch.setattr(api.service, "resume", lambda: calls.append("resume") or {"ok": True})
    monkeypatch.setattr("arbscanner.api.shutil.disk_usage", lambda _p: SimpleNamespace(total=50_000_000_000, used=0, free=50_000_000_000))
    monkeypatch.setattr(api.db, "database_integrity_check", lambda: {"ok": True, "rows": ["ok"]})
    backups = []
    monkeypatch.setattr(api.db, "backup_to", lambda path: backups.append(str(path)))
    monkeypatch.setattr(api.db, "compact_database", lambda: {
        "ok": True, "before_bytes": 10_000, "after_bytes": 4_000, "reclaimed_bytes": 6_000,
        "integrity_before": "ok", "integrity_after": "ok",
    })

    result = api.compact_database({})
    assert result["ok"] is True
    assert calls == ["pause", "resume"]
    assert backups and "pre-vacuum" in backups[0]
    assert result["reclaimed_bytes"] == 6_000


def test_compaction_refuses_while_legacy_cleanup_remains(tmp_path, monkeypatch):
    api = API(tmp_path / "notready.sqlite3")
    monkeypatch.setattr(api.db, "snapshot_storage_health", lambda: {"legacy_rows_remaining_estimate": 123})
    result = api.compact_database({})
    assert result["ok"] is False
    assert "123" in result["message"]
