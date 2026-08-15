import json
from pathlib import Path

from arbscanner.api import API


def sample_execution(state="MONITOR_OPEN", captured_profit=6.4468, balanced=True):
    return {
        "state": state,
        "deployed": 100.122644,
        "captured_profit": captured_profit,
        "outcome": "No" if "SETTLED" in state else None,
        "details": {
            "execution_state": "HEDGED",
            "execution_result": {
                "state": "HEDGED",
                "captured_profit": 6.4468,
                "after_hedge": {
                    "worst_case_pnl": 6.4468,
                    "best_case_pnl": 7.7604,
                    "balanced": balanced,
                },
            },
        },
    }


def test_locked_profit_is_worst_case_from_actual_fills():
    metrics = API._execution_value_metrics(sample_execution())
    assert metrics["locked_profit"] == 6.4468
    assert metrics["best_case_profit"] == 7.7604
    assert metrics["locked_is_guaranteed"] is True
    assert abs(metrics["locked_return_pct"] - 6.438903) < 1e-6
    assert metrics["final_pnl"] is None


def test_locked_profit_survives_settlement_while_final_pnl_changes():
    metrics = API._execution_value_metrics(sample_execution("MONITOR_SETTLED", captured_profit=7.7604))
    assert metrics["locked_profit"] == 6.4468
    assert metrics["final_pnl"] == 7.7604
    assert metrics["locked_profit"] != metrics["final_pnl"]


def test_unbalanced_exposure_is_not_called_locked_profit():
    row = sample_execution("MONITOR_OPEN_EXPOSED", captured_profit=-2.0, balanced=False)
    metrics = API._execution_value_metrics(row)
    assert metrics["locked_profit"] is None
    assert metrics["locked_return_pct"] is None
    assert metrics["locked_is_guaranteed"] is False
    assert metrics["worst_case_pnl"] == 6.4468


def test_dashboard_open_position_exposes_locked_profit_and_return(tmp_path):
    api = API(tmp_path / "locked-dashboard.sqlite3")
    oid = api.db.add_opportunity(
        "alpha v beta", "Alpha v Beta", None, "Match Winner", 5.0, 5.0,
        [], [], 0.99, "locked-test", strategy="two-way", sport="Tennis", in_play=True, event_status="OPEN",
    )
    sim = {
        "stakes": [
            {"exchange": "Betfair delayed", "selection": "Alpha", "odds": 1.9, "stake": 60.0},
            {"exchange": "Matchbook", "selection": "Beta", "odds": 2.4, "stake": 40.0},
        ],
        "after_hedge": {"worst_case_pnl": 6.0, "best_case_pnl": 7.0, "balanced": True},
    }
    opened, reason = api.db.open_monitor_position(
        opportunity_id=oid,
        execution_run_id=None,
        event_key="alpha v beta",
        market_name="Match Winner",
        deployed=100.0,
        expected_profit=5.5,
        stakes_by_exchange={"betfair": 60.0, "matchbook": 40.0},
        normal_stakes_by_exchange={"betfair": 60.0, "matchbook": 40.0},
        outcome_exchange_pnls={"Alpha": {"betfair": 6.0, "matchbook": 0.0}, "Beta": {"betfair": 0.0, "matchbook": 7.0}},
        simulation=sim,
        hedge_reserve_pct=20.0,
        stream="in_play",
    )
    assert opened is True, reason
    overview = api.dashboard_overview({})
    row = overview["rows"][0]
    assert row["locked_profit"] == 6.0
    assert row["locked_return_pct"] == 6.0
    assert row["best_case_profit"] == 7.0
    assert overview["locked_open_profit"] == 6.0
    assert overview["locked_open_return_pct"] == 6.0
    assert overview["stream_summary"]["in_play"]["locked_open_profit"] == 6.0


def test_frontend_promotes_locked_values_across_views():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert 'id="dashOpenProfit"' in html and "locked" in html
    assert 'id="execViewLocked"' in html
    assert 'id="execViewLockedReturn"' in html
    assert "<th>Locked profit</th><th>Locked return</th>" in html
    assert 'option value="executed">Active / executed</option>' in html
    assert "Locked return on balanced capital" in html
    assert "Locked profit at placement" in html
    assert "Best-case profit" in html
