from datetime import datetime, timezone
from pathlib import Path

from arbscanner.api import API


def _html():
    return Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()


def test_dashboard_has_live_pipeline_and_seven_day_charts():
    html = _html()
    for token in (
        'id="dashboardLiveActivity"',
        'id="liveDiscovery"',
        'id="liveMatched"',
        'id="liveProcessed"',
        'id="liveOpportunities"',
        'id="liveQualified"',
        'id="liveExecuted"',
        'id="trendPnlChart"',
        'id="trendVenuePnlChart"',
        'id="trendRacingActivityChart"',
        'id="trendRacingRoiChart"',
    ):
        assert token in html
    assert "if(dataContextMode==='sim'&&$('dashboard')?.classList.contains('active'))void pollDashboardLiveActivity()" in html
    assert "Cumulative scanner activity since local midnight" in html


def test_dashboard_trends_aggregate_sports_and_racing(tmp_path):
    api = API(tmp_path / "trends.sqlite3")
    now = datetime.now(timezone.utc).isoformat()
    db = api.db
    with db.lock:
        cur = db.conn.execute(
            """INSERT INTO opportunities(
                detected_at,event_key,event_name,event_start,market_name,edge_pct,expected_roi_pct,
                legs_json,is_demo,status,strategy,sport,section,qualification_status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now, "sports-event", "Sports Event", now, "Match Winner", 2.0, 1.2, "[]", 0,
             "settled", "two-way", "Tennis", "sports", "qualified"),
        )
        oid = int(cur.lastrowid)
        db.conn.execute(
            """INSERT INTO monitor_positions(
                opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                stakes_by_exchange_json,outcome_exchange_pnls_json,stream,outcome,realized_pnl,realized_by_exchange_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (oid, "sports-event", "Match Winner", now, now, "SETTLED", 100.0, 1.2, "{}", "{}",
             "pre_match", "Player A", 2.5, "{}"),
        )
        scan = db.conn.execute(
            """INSERT INTO scan_runs(started_at,finished_at,markets_seen,matches_seen,status_json,scan_kind)
               VALUES(?,?,?,?,?,?)""",
            (now, now, 2, 1, "{}", "price"),
        )
        scan_id = int(scan.lastrowid)
        db.conn.execute(
            """INSERT INTO matched_markets(
                scan_id,observed_at,event_key,event_name,event_start,market_name,match_score,status,
                strategy,sport,section,net_roi_pct,legs_json,source_markets_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (scan_id, now, "race-event", "Track Race", now, "Win", 0.99, "racing_opportunity",
             "multi_runner_win", "Greyhounds", "racing", 1.75, "[]", "[]"),
        )
        db.conn.commit()

    result = api.dashboard_trends({"days": 7})
    assert result["ok"] is True
    assert len(result["rows"]) == 7
    today = result["rows"][-1]
    assert today["sports"]["qualified"] == 1
    assert today["sports"]["executed"] == 1
    assert today["sports"]["settled"] == 1
    assert today["sports"]["pnl"] == 2.5
    assert today["racing"]["matched_races"] == 1
    assert today["racing"]["research_opportunities"] == 1
    assert today["racing"]["best_net_roi_pct"] == 1.75


def test_live_activity_status_is_observability_only(tmp_path):
    api = API(tmp_path / "live-status.sqlite3")
    result = api.live_activity_status({})
    assert result["ok"] is True
    assert "operations" in result
    assert "price_scanner" in result["operations"]
    assert "pipeline" in result["operations"]
    assert api.get_state()["settings"]["live_execution_available"] is False
