from datetime import datetime, timezone
from pathlib import Path

from arbscanner.api import API

ROOT = Path(__file__).parents[1]
HTML = ROOT.joinpath("frontend", "index.html")


def _html():
    return HTML.read_text()


def test_0920_dashboard_layout_contract_is_display_only_and_ordered():
    html = _html()
    assert "Activity Monitor" in html
    assert "Venue Accounts" in html
    assert 'class="dashboard-venue-grid0920"' in html
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in html
    assert "Venue accounts & controls" not in html
    assert 'id="dashboardVenueControlCard0917"' not in html
    assert 'id="dashboardFinancialPrimary"' in html and 'hidden aria-hidden="true"' in html
    for label in ("Best Win Today", "Wins Today", "Losses Today", "Win Rate Today"):
        assert label in html
    assert "7-day settled P&amp;L" in html
    assert "7-day P&amp;L by Venue" in html
    assert 'id="trendPnlChart"' in html
    assert 'id="trendVenuePnlChart"' in html
    assert "7-day processing" not in html
    assert "venuePnlChart0920" in html
    assert "['betfair','matchbook','smarkets']" in html

    # Locked body order: activity -> venues -> daily KPI -> portfolios -> charts.
    ids = [
        html.index('id="dashboardLiveActivity"'),
        html.index('id="dashboardAccountContext"'),
        html.index('id="dashboard24hResults"'),
        html.index('class="dashboard-domain-grid"'),
        html.index('id="dashboardSportsTrends"'),
    ]
    assert ids == sorted(ids)


def test_0920_dashboard_venue_metrics_reconcile_open_capital_and_locked_profit(tmp_path):
    api = API(tmp_path / "dashboard-venue.sqlite3")
    oid = api.db.add_opportunity(
        "alpha v beta", "Alpha v Beta", None, "Match Winner", 5.0, 5.0,
        [], [], 0.99, "venue-test", strategy="two-way", sport="Tennis",
        in_play=True, event_status="OPEN",
    )
    simulation = {
        "stakes": [
            {"exchange": "Betfair delayed", "selection": "Alpha", "odds": 1.9, "stake": 60.0},
            {"exchange": "Matchbook", "selection": "Beta", "odds": 2.4, "stake": 40.0},
        ],
        "after_hedge": {"worst_case_pnl": 6.0, "best_case_pnl": 7.0, "balanced": True},
    }
    opened, reason = api.db.open_monitor_position(
        opportunity_id=oid, execution_run_id=None, event_key="alpha v beta",
        market_name="Match Winner", deployed=100.0, expected_profit=5.5,
        stakes_by_exchange={"betfair": 60.0, "matchbook": 40.0},
        normal_stakes_by_exchange={"betfair": 60.0, "matchbook": 40.0},
        outcome_exchange_pnls={"Alpha": {"betfair": 6.0}, "Beta": {"matchbook": 7.0}},
        simulation=simulation, hedge_reserve_pct=20.0, stream="in_play",
    )
    assert opened is True, reason
    result = api.dashboard_overview({})
    metrics = result["venue_metrics"]
    assert list(metrics) == ["betfair", "matchbook", "smarkets"]
    assert round(sum(x["capital_in_play"] for x in metrics.values()), 4) == result["capital_in_play"]
    assert round(sum(x["locked_profit"] for x in metrics.values()), 4) == result["locked_open_profit"]
    assert metrics["betfair"]["capital_in_play"] == 60.0
    assert metrics["matchbook"]["capital_in_play"] == 40.0
    assert metrics["smarkets"]["capital"] is None


def test_0920_seven_day_venue_profit_reconciles_to_existing_daily_total(tmp_path):
    api = API(tmp_path / "dashboard-trends.sqlite3")
    now = datetime.now(timezone.utc).isoformat()
    db = api.db
    with db.lock:
        cur = db.conn.execute(
            """INSERT INTO opportunities(
                detected_at,event_key,event_name,event_start,market_name,edge_pct,expected_roi_pct,
                legs_json,is_demo,status,strategy,sport,section,qualification_status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now, "e1", "Event", now, "Match Winner", 1.0, 1.0, "[]", 0,
             "settled", "two-way", "Tennis", "sports", "qualified"),
        )
        oid = int(cur.lastrowid)
        db.conn.execute(
            """INSERT INTO monitor_positions(
                opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                stakes_by_exchange_json,outcome_exchange_pnls_json,stream,outcome,realized_pnl,realized_by_exchange_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (oid, "e1", "Match Winner", now, now, "SETTLED", 100.0, 3.0,
             '{"betfair":60,"matchbook":40}', "{}", "pre_match", "A", 5.0,
             '{"betfair":3.0,"matchbook":2.0}'),
        )
        db.conn.commit()
    result = api.dashboard_trends({"days": 7})
    today = result["rows"][-1]["sports"]
    assert today["pnl"] == 5.0
    assert today["venues"] == {"betfair": 3.0, "matchbook": 2.0, "smarkets": 0.0}
    assert round(sum(today["venues"].values()), 4) == today["pnl"]


def test_0920_today_kpis_request_local_calendar_day(tmp_path):
    api = API(tmp_path / "dashboard-today.sqlite3")
    result = api.dashboard_results_24h({"period": "today", "timezone_offset_minutes": 0, "timezone_name": "UTC"})
    assert result["ok"] is True
    assert result["period"] == "today"
    assert result["settled_only"] is True
