from dataclasses import asdict
from pathlib import Path

from arbscanner.api import API
from arbscanner.models import Leg


def _opportunity(api: API) -> int:
    legs = [
        Leg("Betfair delayed", "Trent Rockets", 1.47, 1000.0, 6.0, event_id="bf-e", market_id="bf-m", selection_id="tr"),
        Leg("Matchbook", "Southern Brave", 3.35, 200.0, 2.0, event_id="mb-e", market_id="mb-m", selection_id="sb"),
    ]
    return api.db.add_opportunity(
        "trent rockets v southern brave", "Trent Rockets v Southern Brave",
        "2026-08-10T17:30:00+00:00", "Match Winner", 2.122, 0.2081,
        [asdict(x) for x in legs], [], .98, "v0728-hedge",
    )


def test_activity_analytics_surfaces_emergency_hedge_actions(tmp_path: Path):
    api = API(tmp_path / "hedge.sqlite3")
    oid = _opportunity(api)
    api.db.add_execution_run(
        oid,
        mode="monitor",
        execution_type="modeled_inplay_monitor",
        state="MONITOR_OPEN",
        deployed=103.36107,
        expected_profit=.2081,
        captured_profit=-3.3822,
        details={
            "monitor_position_opened": True,
            "monitor_stream": "in_play",
            "execution_result": {
                "state": "HEDGED",
                "events": [
                    {"state": "LEG_FAILED", "exchange": "Betfair delayed", "selection": "Trent Rockets"},
                    {"state": "LEG_FILLED", "exchange": "Matchbook", "selection": "Southern Brave", "stake": 30.4979, "odds": 3.3433, "fraction": 1.0},
                    {"state": "EMERGENCY_HEDGE", "exposure_spread": 100.0078, "limit": 25.0},
                    {"state": "HEDGING"},
                    {"state": "HEDGED"},
                ],
                "fills": [
                    {"exchange": "Matchbook", "selection": "Southern Brave", "stake": 30.4979, "odds": 3.3433, "is_hedge": False},
                    {"exchange": "Betfair delayed", "selection": "Trent Rockets", "stake": 72.86317, "odds": 1.3959, "is_hedge": True},
                ],
            },
        },
        started_at="2026-08-10T18:44:47+00:00",
    )
    result = api.activity_analytics({"mode": "monitor", "limit": 100})
    diag = result["executions"][0]["diagnostics"]
    assert diag["emergency_hedge"] is True
    assert diag["hedge_fill_count"] == 1
    assert diag["failed_leg_count"] == 1
    assert any(x["state"] == "EMERGENCY_HEDGE" for x in diag["execution_actions"])
    assert any(x["state"] == "HEDGED" for x in diag["execution_actions"])


def test_execution_history_ui_shows_emergency_hedge_actions():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert "Execution actions" in html
    assert "EMERGENCY HEDGE" in html
    assert "HEDGE FILL" in html
    assert "executionActionSummary" in html
    assert "executionActionDetail" in html
