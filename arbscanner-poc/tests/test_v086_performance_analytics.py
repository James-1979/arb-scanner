from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API

ROOT = Path(__file__).parents[1]
HTML = ROOT.joinpath("frontend", "index.html").read_text()


def _insert_position(api, *, day_offset, deployed, realized, expected, stream="pre_match"):
    now = datetime.now(timezone.utc)
    opened = now - timedelta(days=day_offset, hours=3)
    settled = opened + timedelta(hours=2)
    oid = api.db.add_opportunity(
        event_key=f"event-{day_offset}-{stream}",
        event_name=f"Event {day_offset}",
        event_start=opened.isoformat(),
        market_name="Match Odds",
        edge_pct=1.0,
        expected_roi_pct=1.0,
        legs=[],
        source_markets=[],
        match_score=1.0,
        signature=f"sig-{day_offset}-{stream}",
        sport="Football",
    )
    with api.db.lock:
        api.db.conn.execute(
            """INSERT INTO monitor_positions(
                opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream,outcome,realized_pnl,realized_by_exchange_json
            ) VALUES(?,?,?,?,?,'SETTLED',?,?,?,?,?,?,?,?,?)""",
            (
                oid,
                f"event-{day_offset}-{stream}",
                "Match Odds",
                opened.isoformat(),
                settled.isoformat(),
                float(deployed),
                float(expected),
                "{}",
                "{}",
                "{}",
                stream,
                "Home",
                float(realized),
                "{}",
            ),
        )
        api.db.conn.commit()
    return oid


def test_analytics_navigation_and_planned_sections_are_present():
    assert 'data-tab="analytics"' in HTML
    assert 'id="analytics" class="page"' in HTML
    assert 'data-analytics-tab="performance"' in HTML
    assert 'data-nav-child="analytics" data-analytics-tab="execution"' not in HTML
    assert 'data-tab="sports-engines" data-nav-child="sports"' in HTML
    assert 'data-tab="racing-engines" data-nav-child="racing"' in HTML
    assert 'data-tab="sports-execution" data-nav-child="sports"' not in HTML
    assert 'data-tab="racing-execution" data-nav-child="racing"' not in HTML
    assert 'data-analytics-tab="market"' in HTML
    assert 'data-analytics-tab="scenarios"' in HTML
    assert 'data-analytics-pane="scenarios"' in HTML
    assert '<h2 style="margin:0">Historical Scenario</h2>' in HTML
    assert 'id="replayPeriod"' in HTML


def test_performance_ui_focuses_on_decision_metrics_and_drilldowns():
    for text in (
        "Net P&amp;L",
        "Portfolio ROI",
        "Period deployed",
        "Return on deployed",
        "Current capital",
        "Current exposure",
        "Peak exposure",
        "Average utilisation",
        "Captured edge",
        "Positions executed",
        "Financial &amp; capital trend",
        "Capital &amp; exposure",
        "Venue capital &amp; performance",
        "Market breakdown",
        "Venue pair performance",
        "Performance funnel",
        "Recovery cost",
    ):
        assert text in HTML
    assert 'data-performance-basis="actual"' in HTML
    assert 'data-performance-basis="simulated"' in HTML
    assert "LIVE remains locked" in HTML
    assert 'id="performanceSport"' in HTML
    assert 'id="performanceVenue"' in HTML
    assert 'id="performanceVenuePair"' in HTML
    assert 'class="performance-deeper-0924"' in HTML
    assert 'id="performanceVenueEconomicsBody"' in HTML


def test_performance_analytics_actual_and_expected_are_separate(tmp_path):
    api = API(tmp_path / "performance.sqlite3")
    # Ensure current virtual wallet openings exist. Default is £500 per stream.
    overview = api.dashboard_overview({})
    assert overview["starting_bankroll"] == 1500.0

    _insert_position(api, day_offset=2, deployed=100, realized=10, expected=12)
    _insert_position(api, day_offset=1, deployed=80, realized=-2, expected=5)

    actual = api.performance_analytics({
        "period": "7d",
        "scope": "sports",
        "stream": "all",
        "basis": "actual",
        "timezone_offset_minutes": 0,
    })
    expected = api.performance_analytics({
        "period": "7d",
        "scope": "sports",
        "stream": "all",
        "basis": "simulated",
        "timezone_offset_minutes": 0,
    })

    assert actual["ok"] is True
    assert actual["summary"]["period_profit"] == 8.0
    assert actual["summary"]["current_capital"] == 1008.0
    assert actual["summary"]["deployed_turnover"] == 180.0
    assert actual["summary"]["settled_bets"] == 2
    assert len(actual["rows"]) == 7
    assert actual["summary"]["captured_edge_pct"] == round((8.0 / 17.0) * 100.0, 4)
    assert [x["label"] for x in actual["performance"]["domains"]] == ["Sports", "Racing"]
    assert actual["performance"]["markets"][0]["market"] == "Match Odds"
    assert "funnel" in actual["performance"]
    assert actual["performance"]["metric_definitions"]["captured_edge"].startswith("Realised settled P&L")

    assert expected["summary"]["period_profit"] == 17.0
    assert expected["summary"]["current_capital"] == 1017.0
    assert "expected profit" in expected["basis_note"].lower()
    assert "virtual Monitor stakes" in actual["basis_note"]


def test_greyhound_performance_uses_isolated_racing_monitor_stream(tmp_path):
    api = API(tmp_path / "racing-performance.sqlite3")
    api.dashboard_overview({})
    result = api.performance_analytics({"period": "7d", "scope": "racing", "basis": "actual"})
    assert result["ok"] is True
    assert result["research_only"] is False
    assert result["summary"]["current_capital"] == 500.0
    assert result["summary"]["settled_bets"] == 0


def test_v085_recovery_guards_still_exist():
    assert 'id="settingsAdvancedContent"' in HTML
    assert 'prepareInformationArchitecture();' in HTML
    assert 'id="preMinRoi"' in HTML
    assert 'id="ipMinRoi"' in HTML
    assert 'id="bfEnabled"' in HTML
    assert 'id="mbEnabled"' in HTML
