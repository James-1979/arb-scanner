from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _insert_position(api: API, *, opened: datetime, settled: datetime, deployed: float, realised: float, sig: str):
    oid = api.db.add_opportunity(
        event_key=f"event-{sig}", event_name=f"Event {sig}", event_start=opened.isoformat(),
        market_name="Match Odds", edge_pct=1.0, expected_roi_pct=1.0, legs=[], source_markets=[],
        match_score=1.0, signature=f"sig-{sig}", sport="Football",
    )
    with api.db.lock:
        api.db.conn.execute(
            """INSERT INTO monitor_positions(
                opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream,outcome,realized_pnl,realized_by_exchange_json
            ) VALUES(?,?,?,?,?,'SETTLED',?,?,?,?,?,?,?,?,?)""",
            (
                oid, f"event-{sig}", "Match Odds", opened.isoformat(), settled.isoformat(), float(deployed),
                max(0.01, realised), "{}", "{}", "{}", "pre_match", "Home", float(realised), "{}",
            ),
        )
        api.db.conn.commit()


def test_0940_release_identity_and_installer_lock():
    assert __version__ == "0.9.40"
    assert '<title>ArbScanner PoC 0.9.40</title>' in HTML
    installer = (ROOT / "BUILD_AND_INSTALL.command").read_text()
    assert 'EXPECTED_VERSION="0.9.40"' in installer
    assert '## 0.9.40' in (ROOT / "RELEASE_NOTES.md").read_text()


def test_0940_market_analysis_has_three_selected_mode_provider_slots(tmp_path):
    api = API(tmp_path / "market.sqlite3")
    sim = api.market_analysis({"mode": "sim", "period": "today", "scope": "all"})
    live = api.live_market_analysis({"mode": "live", "period": "today", "scope": "all"})
    assert sim["ok"] and live["ok"]
    for mode, result in (("sim", sim), ("live", live)):
        rows = {r["provider_id"]: r for r in result["venue_summary"]}
        assert set(rows) >= {"betfair", "matchbook", "smarkets"}
        assert all(rows[p]["selected_mode"] == mode for p in ("betfair", "matchbook", "smarkets"))
        assert rows["smarkets"]["authoritative_market_data"] is False
        assert rows["smarkets"]["analytics_status"] in {"awaiting_api_access", "not_expected", "disabled"}
    # Fixed three-wide visual contract and neutral missing/not-expected treatment.
    assert 'repeat(3,minmax(0,1fr))' in HTML
    assert "['betfair','matchbook','smarkets']" in HTML
    assert 'AWAITING API ACCESS' in HTML


def test_0940_performance_capital_over_time_is_event_driven_and_cash_reconciles(tmp_path):
    api = API(tmp_path / "performance.sqlite3")
    api.dashboard_overview({})
    now = datetime.now(timezone.utc)
    _insert_position(api, opened=now - timedelta(hours=5), settled=now - timedelta(hours=2), deployed=100, realised=5, sig="a")
    _insert_position(api, opened=now - timedelta(hours=4), settled=now - timedelta(hours=1), deployed=150, realised=7, sig="b")
    result = api.performance_analytics({
        "mode": "sim", "period": "24h", "scope": "all", "venue": "all", "basis": "actual",
        "timezone_offset_minutes": 0,
    })
    assert result["ok"] is True
    timeline = result["capital_timeline"]
    deployed = [float(x["capital_deployed"]) for x in timeline]
    assert max(deployed) >= 250.0
    assert deployed[-1] == 0.0
    assert any(b > a for a, b in zip(deployed, deployed[1:]))
    assert any(b < a for a, b in zip(deployed, deployed[1:]))
    event_points = [x for x in timeline if int(x.get("opened") or 0) or int(x.get("settled") or 0)]
    assert event_points
    for point in event_points:
        if point.get("capital") is not None and point.get("available") is not None:
            assert abs(float(point["available"]) - (float(point["capital"]) - float(point["capital_deployed"]))) < 0.02
    assert '>Capital over time<' in HTML
    assert '>Total Capital<' in HTML
    assert '>Available to Deploy<' in HTML
    assert '>In Open Positions<' in HTML
    assert 'performanceVenueQuick0940' in HTML
    assert 'All Venues' in HTML and 'Smarkets' in HTML
    assert 'performanceAxisMoney0940' in HTML


def test_0940_playback_speed_buttons_replace_operator_dropdown_experience():
    assert 'let vals=[.5,1,2,5,10]' in HTML
    assert 'performanceSpeed0940' in HTML
    assert 'setPerformanceSpeed0940' in HTML
    assert 'replaySpeed0940' in HTML
    assert 'setReplaySpeed0940' in HTML
    # Existing Replay select is retained only as hidden backing state for compatibility.
    assert "old.style.display='none'" in HTML


def test_0940_monitor_and_results_have_one_filter_surface_and_monitor_detected_column():
    # Monitor/Results were removed from the old global accordion wrapper.
    install = HTML.split('function installGlobalFilterAccordions()', 1)[1].split('function ', 1)[0]
    assert 'monitorPhase' not in install
    assert 'positionResultsPeriod' not in install
    assert 'flattenLifecycleFilters0940' in HTML
    assert 'analytics-filter-surface0940' in HTML
    assert 'monitorDetectedCell0940' in HTML
    assert '<th>Detected</th>' in HTML
    assert 'x?.detected_at||x?.first_seen||x?.observed_at' in HTML


def test_0940_engine_metadata_and_stream_guardrail_ownership_contract():
    assert 'engine-metadata0940' in HTML
    assert 'Effective configuration · read-only' in HTML
    assert 'Sports Config owns stream guardrails' in HTML
    assert 'Engines may be stricter, never looser' in HTML
    assert 'Global stream rule.' in HTML
    for term in ('Min Profit', 'Return', 'Quality', 'Max Stake'):
        assert term in HTML


def test_0940_sports_overview_density_and_sports_icon_contract():
    assert '#sports.page.active .viewhead' in HTML
    assert '.slice(0,4)' in HTML
    for icon in ('⚽','🎾','🏏','🏀','🎯','🎱','🏒','🏐','🏉','🏈','⚾','🤾','🏑'):
        assert icon in HTML
