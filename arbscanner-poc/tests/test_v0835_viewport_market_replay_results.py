from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _add_market_observation(db, scan_id: int, event_key: str, *, roi: float = 2.0):
    db.add_matched_market(
        scan_id, event_key, f"{event_key} Event", None, "Match Winner", 0.99,
        roi, roi, 0.0, roi, 20.0, 0.4, None, "recommended", "ok", [], [],
        strategy="two-way", sport="Tennis", in_play=False, event_status="OPEN", section="sports",
    )


def test_v0835_release_and_locked_ui_contract():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert 'id="timelineReplayAccounts"' not in HTML
    assert 'id="positionResultsBreakEven"' not in HTML
    for token in (
        'id="positionResultsHedged"',
        'Emergency hedges:',
        'Weekly market heatmap',
        'Scan observations',
        'Unique markets',
        'Return on deployed',
        "call('market_heatmap'",
        'marketHeatmapCache0835',
        'Largest settlements stay labelled',
        'timeline-return-marker${cls}${selected}${labeled}',
        'fitAnalyticsViewport0835',
        'updateActivePositionsNavCount(Number(r.bets_in_play',
    ):
        assert token in HTML


def test_hourly_rollup_counts_observations_but_deduplicates_market_identity(tmp_path):
    api = API(tmp_path / "heatmap.sqlite3")
    sid = api.db.start_scan()
    for _ in range(4):
        _add_market_observation(api.db, sid, "same-market", roi=2.5)

    now = datetime.now(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    result = api.market_heatmap({
        "from_utc": start.isoformat(), "to_utc": end.isoformat(),
        "scope": "sports", "phase": "pre_match", "sport": "Tennis",
        "timezone_offset_minutes": 0, "timezone_name": "UTC",
    })
    assert result["ok"] is True
    bucket = result["hours"][now.hour]
    assert bucket["observations"] == 4
    assert bucket["unique_markets"] == 1
    assert bucket["net_positive"] == 1
    assert result["metrics"] == ["observations", "qualified", "executed", "pnl", "roi_pct", "deployed", "available_depth", "avg_executable_stake", "liquidity_capable", "liquidity_rejection_rate_pct"]


def test_lazy_heatmap_backfill_does_not_double_count_next_live_observation(tmp_path):
    api = API(tmp_path / "backfill.sqlite3")
    db = api.db
    sid = db.start_scan()
    now = datetime.now(timezone.utc)
    observed = now.replace(microsecond=0).isoformat()
    # Simulate a pre-v0.9.0 historical row: insert matched history without the new rollup tables.
    with db.lock:
        db.conn.execute(
            """INSERT INTO matched_markets(scan_id,observed_at,event_key,event_name,market_name,match_score,
               theoretical_edge_pct,gross_roi_pct,commission_impact_pct,net_roi_pct,diagnostic_deployed,diagnostic_profit,
               status,reason,legs_json,source_markets_json,strategy,sport,section,in_play,event_status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, observed, "legacy-key", "Legacy", "Match Winner", .99, 2, 2, 0, 2, 20, .4,
             "recommended", "ok", "[]", "[]", "two-way", "Tennis", "sports", 0, "OPEN"),
        )
        db.conn.commit()
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    first = api.market_heatmap({"from_utc": start.isoformat(), "to_utc": end.isoformat(), "timezone_offset_minutes": 0})
    assert first["hours"][now.hour]["unique_markets"] == 1

    _add_market_observation(db, sid, "legacy-key", roi=2.0)
    second = api.market_heatmap({"from_utc": start.isoformat(), "to_utc": end.isoformat(), "timezone_offset_minutes": 0})
    bucket = second["hours"][now.hour]
    assert bucket["observations"] == 2
    assert bucket["unique_markets"] == 1
    assert bucket["net_positive"] == 1


def test_market_conversion_uses_one_canonical_opportunity_cohort(tmp_path):
    api = API(tmp_path / "cohort.sqlite3")
    db = api.db
    sid = db.start_scan()
    for _ in range(5):
        _add_market_observation(db, sid, "cohort-event", roi=3.0)
    now = datetime.now(timezone.utc)
    legs = [
        {"exchange": "Betfair delayed", "selection": "A", "odds": 2.1, "liquidity": 100},
        {"exchange": "Matchbook", "selection": "B", "odds": 2.1, "liquidity": 100},
    ]
    oid = db.add_opportunity(
        "cohort-event", "Cohort Event", now.isoformat(), "Match Winner", 3.0, 3.0,
        legs, [], .99, "cohort-sig", strategy="two-way", sport="Tennis", in_play=False, section="sports",
    )
    run_id = db.add_execution_run(oid, "monitor", "normal", "OPEN", deployed=20, expected_profit=.5)
    simulation = {"stakes": [], "after_hedge": {"balanced": True, "worst_case_pnl": .5}}
    with db.lock:
        db.conn.execute(
            """INSERT INTO monitor_positions(opportunity_id,execution_run_id,event_key,market_name,opened_at,status,
               deployed,expected_profit,stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (oid, run_id, "cohort-event", "Match Winner", now.isoformat(), "OPEN", 20, .5, "{}", "{}", json.dumps(simulation), "pre_match"),
        )
        db.conn.commit()
    start = now - timedelta(minutes=5)
    end = now + timedelta(minutes=5)
    result = api.market_analysis({"from_utc": start.isoformat(), "to_utc": end.isoformat(), "scope": "sports", "sport": "Tennis"})
    row = next(x for x in result["rows"] if x["market_name"] == "Match Winner")
    assert row["observations"] == 5
    assert row["unique_markets"] == 1
    assert row["qualified"] == 1
    assert row["attempts"] == 1
    assert row["executed"] == 1
    assert row["execution_conversion_pct"] == pytest.approx(100.0)


def test_results_hedge_contract_counts_positions_not_legs():
    # The final renderer uses canonical role audit for both the filter and headline count.
    assert "positionHasHedge0835" in HTML
    assert "rows.filter(positionHasHedge0835).length" in HTML
    assert "f.role==='balancing'||f.role==='emergency_hedge'" in HTML
    assert "Hedged / recovered" in HTML
