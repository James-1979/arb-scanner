from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
API_SRC = (ROOT / "arbscanner" / "api.py").read_text()
WORKER_SRC = (ROOT / "worker.py").read_text()


def _insert_position(api: API, *, opened: datetime, settled: datetime, deployed: float, realised: float, sig: str):
    oid = api.db.add_opportunity(
        event_key=f"event-{sig}",
        event_name=f"Event {sig}",
        event_start=opened.isoformat(),
        market_name="Match Odds",
        edge_pct=1.0,
        expected_roi_pct=1.0,
        legs=[],
        source_markets=[],
        match_score=1.0,
        signature=f"sig-{sig}",
        sport="Football",
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
    return oid


def test_0939_release_identity_and_installer_lock():
    assert __version__ == "0.9.39"
    assert '<title>ArbScanner PoC 0.9.39</title>' in HTML
    installer = (ROOT / "BUILD_AND_INSTALL.command").read_text()
    assert 'EXPECTED_VERSION="0.9.39"' in installer
    assert '## 0.9.39' in (ROOT / "RELEASE_NOTES.md").read_text()


def test_0939_dashboard_activity_status_is_selected_mode_and_feed_semantics_are_split(tmp_path):
    api = API(tmp_path / "ops.sqlite3")
    sim = api.live_activity_status({"mode": "sim"})
    live = api.live_activity_status({"mode": "live"})
    assert sim["ok"] and live["ok"]
    assert sim["operations"]["selected_mode"] == "sim"
    assert live["operations"]["selected_mode"] == "live"
    assert sim["operations"]["selected_mode_summary"] == sim["operations"]["mode_summary"]["sim"]
    assert live["operations"]["selected_mode_summary"] == live["operations"]["mode_summary"]["live"]
    for row in sim["operations"]["feeds"]:
        assert "transport_state" in row
        assert "freshness_state" in row
    assert "live_activity_status',[{mode}]" in HTML
    assert "Market CONNECTED" in HTML and "DATA STALE" in HTML
    assert '<strong>Price Scan</strong>' in HTML
    # Discovery can establish transport connectivity, but only price traffic or a
    # market snapshot is allowed to advance market-data freshness.
    assert "price_status_seen_at" in API_SRC
    assert "freshness=max((dt for dt in (snap_dt, price_seen_dt)" in API_SRC


def test_0939_discovery_is_decoupled_from_fast_price_scheduler():
    assert "def _run_discovery_background" in WORKER_SRC
    assert "threading.Thread(" in WORKER_SRC
    assert 'name="arbscanner-discovery"' in WORKER_SRC
    assert "discovery_thread is None" in WORKER_SRC
    # The blocking discovery call lives inside the background helper, while the
    # main loop still reaches the independent price scheduler immediately after.
    helper = WORKER_SRC.split("def _run_discovery_background", 1)[1].split("def _parse_archive_child_output", 1)[0]
    main = WORKER_SRC.split("def main():", 1)[1]
    assert "discover_once" in helper
    assert "scanner.price_scan_once" in main
    assert "discovery_thread.start()" in main


def test_0939_performance_capital_deployed_is_event_driven_and_releases(tmp_path):
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
    assert len(timeline) >= 5
    deployed = [round(float(x["capital_deployed"]), 4) for x in timeline]
    assert max(deployed) >= 250.0
    assert deployed[-1] == 0.0
    assert any(b > a for a, b in zip(deployed, deployed[1:]))
    assert any(b < a for a, b in zip(deployed, deployed[1:]))
    assert any(int(x.get("opened") or 0) > 0 for x in timeline)
    assert any(int(x.get("settled") or 0) > 0 for x in timeline)
    assert 'performanceResponse0844?.capital_timeline' in HTML
    assert 'deployed-step0939' in HTML
    assert 'performanceStepPath0939' in HTML


def test_0939_replay_console_engine_drawer_monitor_and_sports_config_contract():
    # Replay right-side console, with no reintroduction of a floating control row.
    assert 'replay-main-layout0939' in HTML
    assert 'replay-playback-console0939' in HTML
    assert 'RUNNING P&amp;L' in HTML and 'REPLAY TIME' in HTML
    assert 'grid-template-columns:minmax(0,4fr) minmax(220px,1fr)' in HTML

    # Sports engine drawer is solid and its top action is Export Engine. Duplicate
    # SIM/LIVE/routing/configure controls are removed from the rendered Sports drawer.
    assert '.engine-drawer0938,.engine-drawer-head0938,#sportsEngineDetail0914{background:var(--panel)!important}' in HTML
    assert '>Export Engine</button>' in HTML
    assert 'cleanSportsEngineDetail0939' in HTML
    assert "k==='routing'||k==='sim'||k==='live'" in HTML
    assert 'Export .arbengine' in HTML  # legacy/racing library remains compatible
    assert "Export \\.arbengine" in HTML  # cleanup removes it from Sports drawer

    # Seven Monitor status tiles remain one desktop row and Last Detected is explicit date+time.
    assert '#monitor .opsbar{grid-template-columns:repeat(7,minmax(0,1fr))!important' in HTML
    assert 'monitorDetectedStamp0939' in HTML
    assert "day:'2-digit',month:'short',year:'numeric'" in HTML
    assert "hour:'2-digit',minute:'2-digit',second:'2-digit'" in HTML

    # Every supported Sports coverage option has a recognisable icon.
    for icon in ('⚽','🎾','🏏','🏀','🎯','🎱','🏒','🏐','🏉','🏈','⚾','🤾','🏑'):
        assert icon in HTML


def test_0939_analytics_header_alignment_contract():
    assert '.analytics-context-controls{display:flex!important;justify-content:flex-end!important;align-items:flex-end!important' in HTML
    assert '#analyticsRefresh{align-self:end!important;justify-self:end!important;height:28px!important' in HTML
    assert '.analytics-context-controls .market-header-filters' in HTML
    assert '.analytics-context-controls .performance-filterbar0931' in HTML
