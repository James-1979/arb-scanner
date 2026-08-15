from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.replay import replay_analysis


def _add_settled(db: DB, name: str = "Alpha v Beta", sport: str = "Football") -> int:
    legs = [
        {
            "exchange": "Matchbook",
            "selection": "Alpha",
            "odds": 2.2,
            "liquidity": 500.0,
            "commission_pct": 0.0,
            "commission_source": "test",
            "sport": sport,
        },
        {
            "exchange": "Betfair delayed",
            "selection": "Beta",
            "odds": 2.2,
            "liquidity": 500.0,
            "commission_pct": 0.0,
            "commission_source": "test",
            "sport": sport,
        },
    ]
    oid = db.add_opportunity(
        name.lower(), name, "2026-08-09T12:00:00+00:00", "Match Winner", 9.0, 10.0,
        legs, [], 0.99, f"sig-{name}", strategy="two-way", sport=sport,
    )
    db.settle(oid, "Alpha", notes="test result")
    return oid


def test_results_analytics_uses_settled_result_and_tracks_capital(tmp_path: Path):
    db = DB(tmp_path / "analytics.sqlite3")
    _add_settled(db)
    result = replay_analysis(db, 500.0, min_profit=0.0, min_deployed_roi_pct=0.0)
    assert result["counts"]["settled_available"] == 1
    assert result["counts"]["taken"] == 1
    assert result["realized_profit"] > 0
    assert result["ending_capital"] > result["starting_capital"]
    assert result["events"][0]["outcome"] == "Alpha"
    assert result["events"][0]["realized_pnl"] > 0
    assert result["events"][0]["capital_after_result"] == result["ending_capital"]
    assert any(point["kind"] == "settlement" for point in result["series"])


def test_results_analytics_min_profit_rule_can_exclude_history(tmp_path: Path):
    db = DB(tmp_path / "analytics-filter.sqlite3")
    _add_settled(db)
    result = replay_analysis(db, 500.0, min_profit=1000.0, min_deployed_roi_pct=0.0)
    assert result["counts"]["settled_available"] == 1
    assert result["counts"]["taken"] == 0
    assert result["counts"]["skipped_min_profit"] == 1
    assert result["ending_capital"] == 500.0


def test_api_exposes_analytics_and_minimum_profit_setting(tmp_path: Path):
    api = API(tmp_path / "api.sqlite3")
    _add_settled(api.db)
    state = api.save_settings({"config": {"minimum_profit": 1.25, "max_event_exposure_pct": 50}})
    assert state["settings"]["config"]["minimum_profit"] == 1.25
    assert state["settings"]["config"]["max_event_exposure_pct"] == 50.0
    result = api.analytics_replay({"starting_capital": 500, "minimum_profit": 0, "minimum_deployed_roi_pct": 0})
    assert result["ok"] is True
    assert result["result"]["counts"]["settled_available"] == 1
    assert result["comparison"]


def test_frontend_contains_activity_analytics_and_replay_controls():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert ">Analytics</h2>" in html
    assert ">Activity</h1>" in html
    assert ">Jobs</h1>" not in html
    assert 'data-activity-tab="results"' in html
    assert 'data-activity-tab="executions"' in html
    assert 'data-activity-tab="replay"' in html
    assert 'id="analyticsMinProfit"' in html
    assert 'id="analyticsRelease"' in html
    assert 'id="capitalChart"' in html
    assert 'id="storedResultsRows"' in html
    assert 'id="executionRows"' in html
    assert 'id="scenarioResultsRows"' in html
    assert "Opportunities selected by this scenario" in html
