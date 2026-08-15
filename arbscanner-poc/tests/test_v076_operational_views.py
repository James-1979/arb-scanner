from pathlib import Path

from arbscanner.api import API


def test_primary_navigation_matches_operational_views():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    nav = html[html.index('<div class="nav" id="nav"'): html.index('</div>', html.index('<div class="nav" id="nav"')) + 6]
    for label in ("Dashboard", "Monitor", "Results", "Executions", "Bankroll Replay", "Settings"):
        assert f">{label}</span>" in nav
    assert 'id="activeBetRows"' in html
    assert 'id="monitorRows"' in html
    assert 'id="resultsRows"' in html
    assert 'id="executionsRows"' in html
    assert 'id="cmpPotentialEnd"' in html
    assert 'id="cmpMonitorTimingEnd"' in html
    assert 'id="cmpActualEnd"' in html


def test_dashboard_overview_tracks_unsettled_execution_capital(tmp_path: Path):
    api = API(tmp_path / "dashboard.sqlite3")
    api.set_demo_visibility({"hide": False})
    demo = api.run_demo_scan()
    oid = demo["opportunity_id"]
    api.db.add_execution_run(
        oid,
        mode="monitor_timing",
        execution_type="captured_stress",
        state="STRESS_TESTED",
        deployed=100.0,
        expected_profit=5.0,
        captured_profit=4.0,
    )
    overview = api.dashboard_overview()
    assert overview["bets_in_play"] == 1
    assert overview["monitor_timing_in_play"] == 1
    assert overview["capital_in_play"] == 100.0
    assert overview["rows"][0]["bets"]

    api.db.settle(oid, "Northbridge")
    assert api.dashboard_overview()["bets_in_play"] == 0


def test_bankroll_replay_compares_potential_monitor_timing_and_actual(tmp_path: Path):
    api = API(tmp_path / "replay.sqlite3")
    api.set_demo_visibility({"hide": False})
    oid = api.run_demo_scan()["opportunity_id"]
    api.db.add_execution_run(
        oid, "monitor_timing", "captured_stress", "STRESS_TESTED",
        deployed=100.0, expected_profit=5.0, captured_profit=4.0,
    )
    api.db.add_execution_run(
        oid, "live", "real", "COMPLETE",
        deployed=100.0, expected_profit=5.0, captured_profit=3.0, is_real=True,
    )
    api.db.settle(oid, "Northbridge")
    replay = api.analytics_replay({
        "starting_capital": 500,
        "minimum_profit": 0,
        "minimum_deployed_roi_pct": 0,
        "max_event_exposure_pct": 100,
        "release_policy": "observed",
        "mode": "all",
    })
    evidence = replay["evidence_comparison"]
    assert evidence["potential"]["opportunities"] == 1
    assert evidence["monitor_timing"]["profit"] == 4.0
    assert evidence["actual"]["profit"] == 3.0
    assert evidence["monitor_timing"]["coverage_pct"] == 100.0
    assert evidence["actual"]["coverage_pct"] == 100.0
