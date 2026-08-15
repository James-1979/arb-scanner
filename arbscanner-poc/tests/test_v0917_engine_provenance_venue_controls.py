from __future__ import annotations

from pathlib import Path

import pytest
from types import SimpleNamespace

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.models import Leg
from arbscanner.strategy_engines import ENGINE_LIFECYCLES, EngineRuntime, MarketEvidence, effective_lifecycle

ROOT = Path(__file__).resolve().parents[1]


def make_api(tmp_path, monkeypatch) -> API:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return API(tmp_path / "arbscanner.sqlite3")


def test_0917_venue_control_defaults_are_independent(tmp_path):
    db = DB(tmp_path / "controls.sqlite3")
    rows = {x["provider_id"]: x for x in db.venue_controls()}
    assert set(rows) >= {"betfair", "matchbook", "smarkets"}
    assert rows["betfair"]["sim_feed_enabled"] is True
    assert rows["betfair"]["live_feed_enabled"] is False
    assert rows["betfair"]["live_account_enabled"] is True
    assert rows["betfair"]["sim_account_enabled"] is True
    assert rows["betfair"]["live_execution_enabled"] is False
    assert rows["matchbook"]["sim_feed_enabled"] is True
    assert rows["matchbook"]["live_feed_enabled"] is False
    assert rows["smarkets"]["sim_feed_enabled"] is False
    assert rows["smarkets"]["live_feed_enabled"] is False
    assert rows["smarkets"]["live_account_enabled"] is False
    assert rows["smarkets"]["sim_account_enabled"] is False
    assert rows["smarkets"]["live_execution_enabled"] is False


def test_account_nickname_is_metadata_and_feed_toggle_preserves_it(tmp_path):
    db = DB(tmp_path / "venue-metadata.sqlite3")
    row = db.update_venue_control("betfair", account_nickname="Primary BF")
    assert row["account_nickname"] == "Primary BF"
    row = db.update_venue_control("betfair", sim_feed_enabled=False)
    assert row["sim_feed_enabled"] is False
    assert row["account_nickname"] == "Primary BF"
    row = db.update_venue_control("betfair", sim_feed_enabled=True)
    assert row["account_nickname"] == "Primary BF"


def test_engine_nickname_is_metadata_not_configuration(tmp_path):
    db = DB(tmp_path / "engine-meta.sqlite3")
    db.ensure_default_engines()
    iid = "SPORTS_BASELINE_ARB_PRIMARY"
    before = db.engine_active_config(iid)
    updated = db.engine_update_metadata(iid, nickname="Base", description="Description", notes="Notes")
    after = db.engine_active_config(iid)
    assert updated["nickname"] == "Base"
    assert after["config_version"] == before["config_version"]
    assert after["config_hash"] == before["config_hash"]


def test_engine_sim_and_live_enablement_are_independently_selectable(tmp_path):
    db = DB(tmp_path / "engine-modes.sqlite3")
    db.ensure_default_engines()
    iid = "SPORTS_BASELINE_ARB_PRIMARY"
    initial = db.engine_instance(iid)
    assert initial["sim_enabled"] is True and initial["live_enabled"] is False
    both = db.engine_set_mode_enablement(iid, "live", True)
    assert both["sim_enabled"] is True and both["live_enabled"] is True
    live_only = db.engine_set_mode_enablement(iid, "sim", False)
    assert live_only["sim_enabled"] is False and live_only["live_enabled"] is True
    off = db.engine_set_mode_enablement(iid, "live", False)
    assert off["sim_enabled"] is False and off["live_enabled"] is False


def test_monitor_timing_is_not_an_active_engine_mode(tmp_path):
    assert "MONITOR_TIMING" not in ENGINE_LIFECYCLES
    assert effective_lifecycle("MONITOR_TIMING") == ("DISABLED", "INVALID_REQUESTED_LIFECYCLE")
    db = DB(tmp_path / "legacy-monitor_timing.sqlite3")
    db.ensure_default_engines()
    with pytest.raises(ValueError, match="Invalid engine lifecycle"):
        db.engine_set_lifecycle("SPORTS_BASELINE_ARB_PRIMARY", "MONITOR_TIMING")
    assert set(db.engine_performance("SPORTS_BASELINE_ARB_PRIMARY")) == {"sim", "live"}


def _sports_evidence(one_venue: bool = False) -> MarketEvidence:
    def leg(exchange, provider, selection, odds, market):
        return Leg(exchange, selection, odds, 100.0, 2.0, market_id=market, selection_id=f"{market}-{selection}", provider_id=provider, venue_id=provider)
    candidates = {
        "A": [leg("Betfair delayed", "betfair", "A", 2.2, "bf-a")],
        "B": [leg("Betfair delayed", "betfair", "B", 2.2, "bf-b")],
    }
    if not one_venue:
        candidates["A"].append(leg("Matchbook", "matchbook", "A", 2.15, "mb-a"))
        candidates["B"].append(leg("Matchbook", "matchbook", "B", 2.15, "mb-b"))
    market = SimpleNamespace(
        canonical_event_id="evt", event_key="evt", canonical_market_id="mkt", display_market="Match Winner",
        display_event="A v B", start_time="2026-08-13T20:00:00+00:00", section="sports", sport="Tennis",
        competition="Test", strategy="two-way", status="OPEN", in_play=False, canonical_market_type="Match Winner",
    )
    return MarketEvidence.from_candidates(market, candidates, feed_generation="g1", observed_at="2026-08-13T19:00:00+00:00")


def test_missing_required_venue_is_local_rejection_and_engine_stays_enabled(tmp_path):
    db = DB(tmp_path / "missing-venue.sqlite3")
    db.ensure_default_engines()
    runtime = EngineRuntime(db)
    results = runtime.evaluate(_sports_evidence(one_venue=True), instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"])
    assert len(results) == 1
    assert results[0].decision is None
    assert results[0].selected_legs == ()
    row = db.engine_instance("SPORTS_BASELINE_ARB_PRIMARY")
    assert row["effective_lifecycle"] == "SIM"
    assert row["effective_reason"] == "SIM_ENABLED"
    assert row["sim_enabled"] is True
    assert row["errors"] == 0


def test_engine_recovers_automatically_after_single_venue_evidence(tmp_path):
    db = DB(tmp_path / "recover-venue.sqlite3")
    db.ensure_default_engines()
    runtime = EngineRuntime(db)
    iid = "SPORTS_BASELINE_ARB_PRIMARY"
    runtime.evaluate(_sports_evidence(one_venue=True), instance_ids=[iid])
    # Simulate the stale state persisted by 0.9.36 before this fix; routing must
    # admit it once and heal it without operator intervention.
    db.engine_set_effective(iid, "DISABLED", "INSUFFICIENT_COMPATIBLE_VENUE_FEEDS")
    valid = runtime.evaluate(_sports_evidence(one_venue=False), instance_ids=[iid])
    assert valid, "valid evidence must be evaluated after a local rejection"
    row = db.engine_instance(iid)
    assert row["sim_enabled"] is True
    assert row["effective_lifecycle"] == "SIM"
    assert row["effective_reason"] == "SIM_ENABLED"


def test_opportunity_and_monitor_position_keep_engine_provenance(tmp_path):
    db = DB(tmp_path / "provenance.sqlite3")
    db.ensure_default_engines()
    db.reset_monitor_wallets({"betfair": 100.0, "matchbook": 100.0})
    legs = [
        {"exchange": "Betfair delayed", "provider_id": "betfair", "venue_id": "betfair", "selection": "A", "odds": 2.1, "liquidity": 100},
        {"exchange": "Matchbook", "provider_id": "matchbook", "venue_id": "matchbook", "selection": "B", "odds": 2.1, "liquidity": 100},
    ]
    oid = db.add_opportunity(
        "evt", "A v B", "2026-08-13T20:00:00+00:00", "Match Winner", 1.0, 1.0, legs, [], 1.0, "prov",
        sport="Tennis", engine_instance_id="SPORTS_BASELINE_ARB_PRIMARY", engine_type="SPORTS_BASELINE_ARB",
        engine_version="1.0.0", engine_config_version=1,
    )
    ok, reason = db.open_monitor_position(
        opportunity_id=oid, execution_run_id=None, event_key="evt", market_name="Match Winner", deployed=20.0,
        expected_profit=1.0, stakes_by_exchange={"betfair": 10.0, "matchbook": 10.0},
        outcome_exchange_pnls={"A": {"betfair": 11.0, "matchbook": -10.0}, "B": {"betfair": -10.0, "matchbook": 11.0}},
        simulation={"after_hedge": {"balanced": True}}, stream="pre_match",
    )
    assert ok, reason
    row = dict(db.conn.execute("SELECT * FROM monitor_positions WHERE opportunity_id=?", (oid,)).fetchone())
    assert row["mode"] == "sim"
    assert row["engine_instance_id"] == "SPORTS_BASELINE_ARB_PRIMARY"
    assert row["engine_type"] == "SPORTS_BASELINE_ARB"


def test_scaled_entry_monitor_position_is_attributed_to_superbet(tmp_path):
    db = DB(tmp_path / "scaled.sqlite3")
    db.ensure_default_engines()
    db.reset_monitor_wallets({"betfair": 100.0, "matchbook": 100.0})
    oid = db.add_opportunity("evt2", "C v D", "2026-08-13T20:00:00+00:00", "Match Winner", 1, 1, [], [], 1, "scaled", sport="Tennis")
    ok, reason = db.open_monitor_position(
        opportunity_id=oid, execution_run_id=None, event_key="evt2", market_name="Match Winner", deployed=20,
        expected_profit=1, stakes_by_exchange={"betfair": 10, "matchbook": 10},
        outcome_exchange_pnls={"C": {"betfair": 1, "matchbook": 0}}, simulation={"scaled_entry": {"is_scaled_entry": True}},
    )
    assert ok, reason
    row = dict(db.conn.execute("SELECT engine_instance_id,engine_type FROM monitor_positions WHERE opportunity_id=?", (oid,)).fetchone())
    assert row["engine_instance_id"] == "SPORTS_SUPERBET_ARB_PRIMARY"
    assert row["engine_type"] == "SPORTS_SUPERBET_ARB"


def test_dashboard_exposes_separate_sim_live_accounts_and_smarkets_waiting(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    api.db.upsert_live_account_snapshot({"account_id": "bf-main", "provider_id": "betfair", "venue_id": "betfair", "currency": "GBP", "available_balance": 123.0, "reserved_balance": 0.0, "exposure": 5.0, "equity": 128.0, "captured_at": "2026-08-13T19:00:00+00:00", "source": "exchange_api"})
    feeds = {x["key"]: x for x in api._operational_status()["feeds"]}
    assert feeds["smarkets"]["state"] == "awaiting_api_access"
    assert feeds["smarkets"]["sim_feed_enabled"] is False
    assert feeds["smarkets"]["live_feed_enabled"] is False
    assert feeds["smarkets"]["live_execution_enabled"] is False
    assert feeds["betfair"]["sim_account"] is not feeds["betfair"]["live_account"]
    assert feeds["betfair"]["live_account"]["available"] == 123.0
    assert feeds["betfair"]["live_execution_effective"] is False


def test_feed_disable_is_non_destructive_and_does_not_disable_other_venues(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    before = {x["provider_id"]: x for x in api.db.venue_controls()}
    result = api.update_venue_control({"provider_id": "matchbook", "sim_feed_enabled": False})
    assert result["ok"] is True
    after = {x["provider_id"]: x for x in api.db.venue_controls()}
    assert after["matchbook"]["sim_feed_enabled"] is False
    assert after["matchbook"]["account_nickname"] == before["matchbook"]["account_nickname"]
    assert after["betfair"]["sim_feed_enabled"] is True
    assert after["smarkets"]["sim_feed_enabled"] is False


def test_racing_monitor_rows_are_engine_attributed(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    api.db.set_setting("racing_discovery_latest", {
        "observed_at": "2026-08-13T19:00:00+00:00",
        "summary": {"total": 1, "matched": 0, "unmatched": 1, "rejected": 0, "by_exchange": {"Betfair delayed": 1}},
        "rows": [{"exchange": "Betfair delayed", "market_id": "r1", "race_track": "Romford", "event_name": "Romford", "event_start": "2026-08-13T20:00:00+00:00", "match_status": "unmatched", "runner_count": 6}],
    })
    row = api.racing_monitor({})["rows"][0]
    assert row["engine_instance_id"] == "GREYHOUNDS_BASELINE_ARB_PRIMARY"
    assert row["engine_nickname"] == "Greyhounds Base"
    assert row["venue_ids"] == ["betfair"]
    assert row["account"] == "Main Betfair"


def test_activity_replay_execution_rows_carry_engine_venue_account_and_sim_mode(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    legs = [
        {"exchange": "Betfair delayed", "provider_id": "betfair", "venue_id": "betfair", "selection": "A", "odds": 2.1, "liquidity": 100},
        {"exchange": "Matchbook", "provider_id": "matchbook", "venue_id": "matchbook", "selection": "B", "odds": 2.1, "liquidity": 100},
    ]
    oid = api.db.add_opportunity("evt3", "E v F", "2026-08-13T20:00:00+00:00", "Match Winner", 1, 1, legs, [], 1, "replay-prov", sport="Tennis", engine_instance_id="SPORTS_BASELINE_ARB_PRIMARY", engine_type="SPORTS_BASELINE_ARB", engine_version="1.0.0", engine_config_version=1)
    api.db.add_execution_run(oid, "sim", "modeled_monitor", "MONITOR_MISSED", deployed=10, expected_profit=1, captured_profit=0, details={})
    out = api.activity_analytics({"mode": "sim", "include_results": False, "include_metrics": False, "include_all_time": False})
    row = out["executions"][0]
    assert row["engine_nickname"] == "Baseline"
    assert set(row["venue_ids"]) == {"betfair", "matchbook"}
    assert row["account"] == "Main Betfair + Main Matchbook"
    assert row["mode"] == "sim"
    assert "monitor_timing" not in out["execution_counts"]


def test_0917_ui_has_provenance_filters_venue_controls_and_no_monitor_timing_engine_mode():
    html = (ROOT / "frontend" / "index.html").read_text()
    for token in (
        'id="monitorEngine0917"', 'id="monitorVenue0917"', 'id="monitorAccount0917"',
        'id="positionResultsEngine0917"', 'id="positionResultsVenue0917"', 'id="positionResultsAccount0917"',
        'id="timelineReplayEngine0917"', 'id="timelineReplayVenue0917"', 'id="timelineReplayAccount0917"',
        'id="dashboardExchangeAccounts"', "SIM feed", "LIVE feed", "Greyhounds Base",
    ):
        assert token in html
    assert "SIM/MonitorTiming metrics" not in html
    assert "ACTIVATE MONITOR_TIMING" not in html
    assert "engine_set_mode_enablement" in html
    assert "setEngineMode0917" in html
    assert "> SIM enabled</label>" in html
    assert "> LIVE evaluation requested</label>" in html
    assert 'id="timelineReplayMode0917"' not in html
    assert 'id="monitorMode0917"' not in html
    assert 'id="positionResultsMode0917"' not in html
    assert "mode=String(typeof dataContextMode==='string'?dataContextMode:'sim').toLowerCase()" in html


def test_0916_to_0917_upgrade_preserves_archive_prune_and_engine_metadata(tmp_path):
    from arbscanner.archive import default_archive_root, save_runtime_gate_report
    path = tmp_path / "upgrade.sqlite3"
    db = DB(path)
    db.ensure_default_engines()
    db.engine_update_metadata("SPORTS_BASELINE_ARB_PRIMARY", nickname="My Baseline", notes="keep")
    cfg = dict(db.get_setting("config", {}) or {})
    cfg.update({
        "matched_market_archive_enabled": True,
        "matched_market_archive_runtime_gate_required": True,
        "matched_market_archive_required_before_prune": False,
        "sentinel_0917_upgrade": "keep",
    })
    db.set_setting("config", cfg)
    root = default_archive_root(path)
    root.mkdir(parents=True, exist_ok=True)
    sentinel = root / "0917-upgrade-sentinel.bin"
    sentinel.write_bytes(b"archive-survives-0917")
    save_runtime_gate_report(root, {"ok": True, "status": "PASS", "gate_protocol_version": 1, "archive_schema_version": 1, "hour_utc": "2026-08-13T17:00:00+00:00"})
    db.conn.close()

    upgraded = DB(path)
    upgraded.ensure_default_engines()
    after = upgraded.get_setting("config", {})
    assert after["matched_market_archive_enabled"] is True
    assert after["matched_market_archive_runtime_gate_required"] is True
    assert after["matched_market_archive_required_before_prune"] is False
    assert after["sentinel_0917_upgrade"] == "keep"
    assert sentinel.read_bytes() == b"archive-survives-0917"
    engine = upgraded.engine_instance("SPORTS_BASELINE_ARB_PRIMARY")
    assert engine["nickname"] == "My Baseline"
    assert engine["notes"] == "keep"
