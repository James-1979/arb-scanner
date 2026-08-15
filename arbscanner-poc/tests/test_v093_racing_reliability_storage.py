from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.models import Leg, Quote
from arbscanner.scanner import Scanner

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _add_racing_observation(db: DB, scan_id: int, *, status="racing_monitor", reason="qualified", revision="rev-a", odds=2.0):
    return db.add_matched_market(
        scan_id=scan_id,
        event_key="race-1",
        event_name="Test Track 18:00",
        event_start=None,
        market_name="Win",
        match_score=0.99,
        theoretical_edge_pct=1.5,
        gross_roi_pct=1.3,
        commission_impact_pct=0.2,
        net_roi_pct=1.1,
        diagnostic_deployed=100.0,
        diagnostic_profit=1.1,
        limited_by="liquidity",
        status=status,
        reason=reason,
        legs=[{"exchange": "Betfair delayed", "selection": "1", "side": "BACK", "odds": odds, "liquidity": 20.0}],
        source_markets=[],
        strategy="multi_runner_win",
        quality={"quality_band": "Usable"},
        sport="Greyhounds",
        section="racing",
        in_play=False,
        runner_count=1,
        liquidity_capable=True,
        max_executable_stake=100.0,
        book_revision=revision,
        quote_oldest_age_ms=120,
        quote_newest_age_ms=80,
        quote_receipt_spread_ms=40,
        timestamp_quality="LOCAL_RECEIPT",
        book_complete=True,
    )


def test_v093_identity_and_ui_contract():
    assert __version__ == "0.9.36"
    assert "ArbScanner PoC 0.9.36" in HTML
    assert "optionalNumericConfig" in HTML
    assert 'id="racingRetryCooldown"' in HTML
    assert 'id="racingMaxAttempts"' in HTML
    for field in (
        "raceFunnelObserved", "raceFunnelComplete", "raceFunnelPositive", "raceFunnelLiquidity",
        "raceFunnelQualified", "raceFunnelAttempts", "raceFunnelOpened", "raceFunnelMissed", "raceConfigHealth",
    ):
        assert f'id="{field}"' in HTML


def test_racing_config_patch_does_not_zero_unloaded_fields(tmp_path):
    api = API(tmp_path / "config.sqlite3")
    cfg = api.db.get_setting("config", {}) or {}
    cfg.update({
        "racing_monitor_betfair_starting_balance": 321.0,
        "racing_monitor_matchbook_starting_balance": 654.0,
        "racing_execution_max_stake": 17.0,
        "racing_execution_max_slippage_pct": 0.7,
        "racing_execution_max_unhedged_exposure": 11.0,
    })
    api.db.set_setting("config", cfg)
    result = api.save_settings({"config": {"beginner_mode": False}})
    assert result.get("ok", True) is True
    saved = api.db.get_setting("config", {})
    assert saved["racing_monitor_betfair_starting_balance"] == 321.0
    assert saved["racing_monitor_matchbook_starting_balance"] == 654.0
    assert saved["racing_execution_max_stake"] == 17.0
    assert saved["racing_execution_max_slippage_pct"] == 0.7
    assert saved["racing_execution_max_unhedged_exposure"] == 11.0

    # Explicit zero is a real operator value; blank is rejected and cannot mutate.
    assert api.save_settings({"config": {"racing_execution_max_stake": 0}}).get("ok", True) is True
    before = dict(api.db.get_setting("config", {}))
    failed = api.save_settings({"config": {"racing_execution_max_stake": ""}})
    assert failed["ok"] is False
    assert api.db.get_setting("config", {}) == before


def test_quote_timestamp_quality_never_fakes_provider_source_time():
    q = Quote(
        exchange="Betfair delayed", event_id="e", market_id="m", event_name="E", market_name="Win",
        selection_id="1", selection="One", odds=2.0, liquidity=10.0,
        captured_at="2026-08-12T12:00:00+00:00", feed_entitlement="delayed", market_data_transport="poll",
    )
    assert q.source_timestamp is None
    assert q.timestamp_quality == "LOCAL_RECEIPT"
    q2 = Quote(
        exchange="Matchbook", event_id="e", market_id="m2", event_name="E", market_name="Win",
        selection_id="1", selection="One", odds=2.0, liquidity=10.0,
        captured_at="2026-08-12T12:00:00.100000+00:00", source_timestamp="2026-08-12T12:00:00+00:00",
    )
    assert q2.timestamp_quality == "PROVIDER_SOURCE"

    evidence = Scanner._timing_evidence([
        Leg(exchange="Betfair delayed", selection="One", odds=2.0, liquidity=10.0,
            captured_at="2026-08-12T12:00:00+00:00", timestamp_quality="LOCAL_RECEIPT"),
        Leg(exchange="Matchbook", selection="Two", odds=2.0, liquidity=10.0,
            captured_at="2026-08-12T12:00:00.096000+00:00", timestamp_quality="LOCAL_RECEIPT"),
    ])
    assert evidence["quote_receipt_spread_ms"] == 96
    assert evidence["source_timestamp_spread_ms"] is None
    assert evidence["timestamp_quality"] == "LOCAL_RECEIPT"


def test_bounded_current_state_suppresses_repetitive_verbose_rows(tmp_path):
    db = DB(tmp_path / "bounded.sqlite3")
    scan = db.start_scan(scan_kind="price")
    first = _add_racing_observation(db, scan)
    second = _add_racing_observation(db, scan)
    third = _add_racing_observation(db, scan)
    assert first["verbose_written"] is True
    assert second["verbose_written"] is False
    assert third["verbose_written"] is False
    assert db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0] == 1
    latest = db.conn.execute("SELECT observation_count FROM matched_market_latest").fetchone()[0]
    assert latest == 3

    changed = _add_racing_observation(db, scan, status="below_threshold", reason="ROI moved", odds=2.1)
    assert changed["verbose_written"] is True
    assert db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0] == 2
    breakdown = db.qualification_breakdown_for_scan(scan)
    assert breakdown["racing_monitor"] == 3
    assert breakdown["below_threshold"] == 1


def test_racing_retry_requires_new_book_and_transient_resolved_miss(tmp_path):
    db = DB(tmp_path / "retry.sqlite3")
    assert db.racing_retry_gate("race-1", "Win", "Greyhounds", "rev-a", cooldown_seconds=0, max_attempts=3)["allowed"] is True
    oid = db.add_opportunity("race-1", "Race", None, "Win", 1.5, 1.1, [], [], 0.99, "sig-a",
                             sport="Greyhounds", section="racing", book_revision="rev-a")
    db.set_opportunity_qualification(oid, "racing_qualified", "qualified")
    db.add_execution_run(oid, mode="sim", execution_type="modeled_racing_monitor", state="MONITOR_MISSED",
                         details={"first_failure_reason": "PRICE_MOVED", "live_order_placement": False})
    same = db.racing_retry_gate("race-1", "Win", "Greyhounds", "rev-a", cooldown_seconds=0, max_attempts=3)
    assert same["allowed"] is False and same["code"] == "UNCHANGED_BOOK"
    changed = db.racing_retry_gate("race-1", "Win", "Greyhounds", "rev-b", cooldown_seconds=0, max_attempts=3)
    assert changed["allowed"] is True and changed["code"] == "REARMED"


def test_retention_finalises_legacy_hour_before_pruning_and_preserves_analytics(tmp_path):
    db = DB(tmp_path / "retention.sqlite3")
    scan = db.start_scan(scan_kind="price")
    _add_racing_observation(db, scan)
    _add_racing_observation(db, scan, status="below_threshold", reason="ROI moved", odds=2.1)
    old = (datetime.now(timezone.utc) - timedelta(days=4)).replace(minute=10, second=0, microsecond=0)
    old_hour = old.replace(minute=0).isoformat()
    db.conn.execute("UPDATE matched_markets SET observed_at=?", (old.isoformat(),))
    # Simulate a pre-0.9.3 legacy hour: only verbose raw evidence exists.
    db.conn.execute("DELETE FROM matched_market_history_state")
    db.conn.execute("DELETE FROM market_hourly_rollup_state")
    db.conn.execute("DELETE FROM liquidity_opportunity_rollup_state")
    db.conn.execute("DELETE FROM market_hourly_rollups")
    db.conn.execute("DELETE FROM market_hourly_seen")
    db.conn.execute("DELETE FROM matched_market_reason_hourly_rollups")
    db.conn.execute("DELETE FROM liquidity_opportunity_hourly_rollups")
    db.conn.execute("DELETE FROM racing_funnel_hourly_rollups")
    db.conn.commit()

    start = (old - timedelta(hours=1)).isoformat(); finish = (old + timedelta(hours=2)).isoformat()
    before = db.market_analysis_between(start, finish)
    assert before["rows"][0]["observations"] == 2
    maintenance = db.matched_market_storage_maintenance(retention_hours=48, batch_size=100)
    assert maintenance["deleted"] == 2
    assert db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0] == 0
    assert db.conn.execute("SELECT 1 FROM matched_market_history_state WHERE hour_utc=?", (old_hour,)).fetchone()
    after = db.market_analysis_between(start, finish)
    for key in ("observations", "unique_markets", "raw_positive", "net_positive"):
        assert after["rows"][0][key] == before["rows"][0][key]
    assert after["reasons"]
    health = db.matched_market_storage_health(retention_hours=48)
    assert health["eligible_rows"] == 0
    assert health["rows_deleted"] >= 2

def test_matched_market_pruning_creates_reusable_pages_for_future_diagnostics(tmp_path):
    db = DB(tmp_path / "page-reuse.sqlite3")
    scan = db.start_scan(scan_kind="price")
    # Keep one bounded latest-state key but deliberately create enough material
    # diagnostic changes to allocate real SQLite pages.
    for i in range(220):
        _add_racing_observation(
            db, scan,
            status="racing_monitor" if i % 2 == 0 else "below_threshold",
            reason=f"state-{i}", revision=f"rev-{i}", odds=2.0 + (i * 0.0001),
        )
    peak_pages = int(db.conn.execute("PRAGMA page_count").fetchone()[0])
    old = (datetime.now(timezone.utc) - timedelta(days=4)).replace(minute=10, second=0, microsecond=0)
    db.conn.execute("UPDATE matched_markets SET observed_at=?", (old.isoformat(),))
    # Treat the hour as legacy raw history so maintenance must finalise it first.
    db.conn.execute("DELETE FROM matched_market_history_state")
    db.conn.execute("DELETE FROM market_hourly_rollup_state")
    db.conn.execute("DELETE FROM liquidity_opportunity_rollup_state")
    db.conn.commit()
    result = db.matched_market_storage_maintenance(retention_hours=48, batch_size=1000)
    assert result["deleted"] == 220
    freed = int(db.conn.execute("PRAGMA freelist_count").fetchone()[0])
    assert freed > 0

    # Recreate roughly the same volume of verbose evidence. SQLite should consume
    # the freed pages rather than requiring proportional file growth.
    for i in range(220, 440):
        _add_racing_observation(
            db, scan,
            status="racing_monitor" if i % 2 == 0 else "below_threshold",
            reason=f"state-{i}", revision=f"rev-{i}", odds=2.0 + (i * 0.0001),
        )
    after_pages = int(db.conn.execute("PRAGMA page_count").fetchone()[0])
    assert after_pages <= peak_pages + 8
