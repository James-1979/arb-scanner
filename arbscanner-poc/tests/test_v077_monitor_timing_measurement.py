from dataclasses import asdict
from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.engine import simulate_equal_return
from arbscanner.models import Leg, Scenario
from arbscanner.monitor_timing import evaluate_observation


def _legs():
    return [
        Leg("Matchbook", "Home", 2.72, 420, 2.0, event_id="mb-e", market_id="mb-m", selection_id="4"),
        Leg("Betfair delayed", "Draw", 3.75, 265, 2.0, event_id="bf-e", market_id="bf-m", selection_id="2"),
        Leg("Betfair delayed", "Away", 3.05, 180, 2.0, event_id="bf-e", market_id="bf-m", selection_id="3"),
    ]


def _states(liquidity=500.0):
    return {
        ("Matchbook", "mb-m"): {
            "ok": True, "status": "OPEN", "in_play": False, "latency_ms": 12, "captured_at": "2026-08-09T10:00:00+00:00",
            "quotes": {"4": {"odds": 2.72, "liquidity": liquidity}},
        },
        ("Betfair delayed", "bf-m"): {
            "ok": True, "status": "OPEN", "in_play": False, "latency_ms": 18, "captured_at": "2026-08-09T10:00:00+00:00",
            "quotes": {"2": {"odds": 3.75, "liquidity": liquidity}, "3": {"odds": 3.05, "liquidity": liquidity}},
        },
    }


def test_monitor_timing_observation_requires_original_stake_liquidity():
    legs = _legs()
    sim = simulate_equal_return(legs, Scenario("monitor_timing", 500, 100, 100))
    assert sim["executable"] is True
    ok = evaluate_observation(
        legs, sim, _states(), bankroll=500, max_bankroll_pct=100, max_event_exposure_pct=100,
        min_roi=1.0, min_profit=0.0, pre_match_only=True,
    )
    assert ok["still_executable"] is True
    thin = evaluate_observation(
        legs, sim, _states(liquidity=1.0), bankroll=500, max_bankroll_pct=100, max_event_exposure_pct=100,
        min_roi=1.0, min_profit=0.0, pre_match_only=True,
    )
    assert thin["still_executable"] is False
    assert thin["failure_reason"] == "INSUFFICIENT_LIQUIDITY"


def test_monitor_timing_metrics_reports_checkpoint_survival(tmp_path: Path):
    db = DB(tmp_path / "monitor_timing.sqlite3")
    oid = db.add_opportunity("e", "A v B", "2026-08-10T12:00:00+00:00", "Match Odds", 2.0, 2.0,
                             [asdict(x) for x in _legs()], [], 0.99, "sig")
    rid = db.start_monitor_timing_run(oid, started_at="2026-08-09T10:00:00+00:00", initial_deployed=100,
                              initial_profit=3, initial_roi_pct=3, planned_stakes=[], reference_checkpoint_ms=250)
    db.add_monitor_timing_observation(rid, offset_ms=100, elapsed_ms=120, observed_at="2026-08-09T10:00:00.120+00:00",
                              fetch_latency_ms=20, deployed=100, expected_profit=2.8, expected_roi_pct=2.8,
                              executable_fraction=1, full_stake_available=True, still_profitable=True,
                              still_executable=True, failure_reason=None, quotes=[], venues=[])
    db.add_monitor_timing_observation(rid, offset_ms=250, elapsed_ms=280, observed_at="2026-08-09T10:00:00.280+00:00",
                              fetch_latency_ms=30, deployed=0, expected_profit=0, expected_roi_pct=0,
                              executable_fraction=.4, full_stake_available=False, still_profitable=False,
                              still_executable=False, failure_reason="INSUFFICIENT_LIQUIDITY", quotes=[], venues=[])
    db.finish_monitor_timing_run(rid, finished_at="2026-08-09T10:00:00.300+00:00", status="COMPLETE",
                         survived_through_ms=100, first_failure_reason="INSUFFICIENT_LIQUIDITY",
                         reference_profit=0, reference_roi_pct=0, reference_executable=False)
    m = db.monitor_timing_metrics(from_utc="2026-08-09T00:00:00+00:00", to_utc="2026-08-10T00:00:00+00:00")
    assert m["runs"] == 1
    assert m["survival"]["100"] == 100.0
    assert m["survival"]["250"] == 0.0
    assert m["failure_reasons"]["INSUFFICIENT_LIQUIDITY"] == 1


def test_replay_exact_datetime_and_monitor_timing_use_same_scenario_start(tmp_path: Path):
    api = API(tmp_path / "replay.sqlite3")
    legs = [asdict(x) for x in _legs()]
    ids = []
    for i, stamp in enumerate(("2026-08-08T10:00:00+00:00", "2026-08-09T10:00:00+00:00"), start=1):
        oid = api.db.add_opportunity(f"e{i}", f"Event {i}", "2026-08-09T12:00:00+00:00", "Match Odds", 2.0, 2.0,
                                     legs, [], 0.99, f"sig{i}")
        api.db.conn.execute("UPDATE opportunities SET detected_at=? WHERE id=?", (stamp, oid))
        api.db.settle(oid, "Home")
        api.db.conn.execute("UPDATE settlements SET settled_at=? WHERE opportunity_id=?", ("2026-08-09T15:00:00+00:00", oid))
        ids.append(oid)
    api.db.conn.commit()
    api.db.add_execution_run(ids[1], mode="monitor_timing", execution_type="timed_monitor_timing", state="MONITOR_TIMING_SURVIVED",
                             deployed=100, expected_profit=4, captured_profit=3, max_unhedged_exposure=0,
                             details={"timed_rechecks": True}, is_real=False,
                             started_at="2026-08-09T10:00:00.250+00:00", finished_at="2026-08-09T10:00:01+00:00")
    r = api.analytics_replay({
        "starting_capital": 100,
        "from_utc": "2026-08-09T09:00:00+00:00",
        "to_utc": "2026-08-09T11:00:00+00:00",
        "minimum_deployed_roi_pct": 0,
        "minimum_profit": 0,
    })
    assert r["ok"] is True
    assert r["result"]["counts"]["settled_available"] == 1
    assert r["result"]["filters"]["date_from"] == "2026-08-09T09:00:00+00:00"
    assert r["evidence_comparison"]["monitor_timing"]["ending_capital"] == 103.0
    assert r["evidence_comparison"]["actual"]["ending_capital"] is None


def test_frontend_has_professional_help_and_exact_replay_datetime():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert 'type="datetime-local"' in html
    assert 'id="replayFrom"' in html and 'id="replayTo"' in html
    assert 'class="helpq"' in html
    assert "MONITOR_TIMING timing" in html
    assert "The scanner is always watching" not in html
    assert "PoC 0.7.7" in html
