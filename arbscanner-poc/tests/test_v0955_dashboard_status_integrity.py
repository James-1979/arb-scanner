from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API, DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
API_SOURCE = (ROOT / "arbscanner" / "api.py").read_text()
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text()
NOTES = (ROOT / "RELEASE_NOTES.md").read_text()


def _seed_healthy_price_scan(api: API) -> None:
    now = datetime.now(timezone.utc)
    statuses = [
        {"exchange": "Betfair", "ok": True, "latency_ms": 120, "markets": 44, "message": "ok"},
        {"exchange": "Matchbook", "ok": True, "latency_ms": 260, "markets": 31, "message": "ok"},
    ]
    api.db.conn.execute(
        """INSERT INTO scan_runs(started_at,finished_at,markets_seen,matches_seen,status_json,error,
           processed_candidates,positive_opportunities,qualified_count,executed_count,duration_ms,scan_kind,stage_timings_json,cache_entries,stale_rejections)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            (now - timedelta(milliseconds=400)).isoformat(), now.isoformat(), 75, 20,
            json.dumps(statuses), None, 20, 4, 2, 1, 400, "price", "{}", 20, 0,
        ),
    )
    api.db.conn.commit()
    api.service.status = lambda: {"loaded": True}


def _feed(ops: dict, provider: str) -> dict:
    return next(x for x in ops["feeds"] if x["key"] == provider)


def test_v0955_release_identity():
    assert __version__ == "0.9.55"
    assert '<title>ArbScanner PoC 0.9.55</title>' in HTML
    assert 'EXPECTED_VERSION="0.9.55"' in INSTALLER
    assert "## 0.9.55 — Dashboard Status Integrity Closure" in NOTES


def test_dashboard_status_strip_has_explicit_rag_and_latency_outputs():
    for provider in ("Betfair", "Matchbook", "Smarkets"):
        assert f'id="dash{provider}Dot"' in HTML
        assert f'id="dash{provider}Latency"' in HTML
    for control in ("dashScannerLatency", "dashDiscoveryLatency", "dashMonitorDot", "dashMonitorLatency", "dashMonitorMeta"):
        assert f'id="{control}"' in HTML
    for rag in ("rag-green", "rag-amber", "rag-red", "rag-grey"):
        assert f".statusdot.{rag}" in HTML
    assert "selected_mode_latency_ms" in API_SOURCE
    assert "selected_mode_rag" in API_SOURCE


def test_selected_mode_feed_rag_and_latency_follow_admin_enablement(tmp_path):
    api = API(tmp_path / "dashboard-status.sqlite3")
    _seed_healthy_price_scan(api)

    sim = api.live_activity_status({"mode": "sim"})["operations"]
    bf = _feed(sim, "betfair")
    mb = _feed(sim, "matchbook")
    assert bf["mode_states"]["sim"]["state"] == "ready"
    assert bf["selected_mode_rag"] == "green"
    assert bf["selected_mode_latency_ms"] == 120
    assert mb["selected_mode_latency_ms"] == 260
    assert sim["monitor"]["state"] == "active"
    assert sim["monitor"]["rag"] == "green"
    assert sim["monitor"]["latency_ms"] == 260  # worst enabled selected-feed latency

    # LIVE has no feed enabled by default, so its Monitor must not borrow SIM readiness.
    live = api.live_activity_status({"mode": "live"})["operations"]
    assert live["monitor"]["state"] == "disabled"
    assert live["monitor"]["rag"] == "grey"
    assert live["monitor"]["feeds_expected"] == 0

    # The Admin venue-control mutation returns selected-mode status immediately.
    changed = api.update_venue_control({
        "provider_id": "betfair", "live_feed_enabled": True, "live_account_enabled": False, "mode": "live"
    })
    assert changed["ok"] is True
    assert changed["operations"]["selected_mode"] == "live"
    live_bf = _feed(changed["operations"], "betfair")
    assert live_bf["mode_states"]["live"]["feed_expected"] is True
    assert live_bf["mode_states"]["live"]["state"] == "ready"
    assert live_bf["selected_mode_rag"] == "green"
    assert live_bf["selected_mode_latency_ms"] == 120
    assert changed["operations"]["monitor"]["state"] == "active"

    # Explicitly disabling both LIVE feed and account makes the provider neutral/disabled,
    # not falsely red and not green because SIM remains healthy.
    disabled = api.update_venue_control({
        "provider_id": "betfair", "live_feed_enabled": False, "live_account_enabled": False, "mode": "live"
    })
    live_bf = _feed(disabled["operations"], "betfair")
    assert live_bf["mode_states"]["live"]["state"] == "disabled"
    assert live_bf["selected_mode_rag"] == "grey"
    assert live_bf["selected_mode_latency_ms"] is None


def test_sports_monitor_status_follows_stream_controls_without_affecting_live_lock(tmp_path):
    api = API(tmp_path / "monitor-status.sqlite3")
    _seed_healthy_price_scan(api)
    cfg = {**DEFAULT_CONFIG, **(api.db.get_setting("config", DEFAULT_CONFIG) or {})}

    cfg["inplay_monitor_enabled"] = False
    api.db.set_setting("config", cfg)
    partial = api.live_activity_status({"mode": "sim"})["operations"]["monitor"]
    assert partial["state"] == "partial"
    assert partial["rag"] == "amber"

    cfg["pre_match_monitor_enabled"] = False
    api.db.set_setting("config", cfg)
    off = api.live_activity_status({"mode": "sim"})["operations"]["monitor"]
    assert off["state"] == "disabled"
    assert off["rag"] == "grey"
    assert off["live_execution_allowed"] is False


def test_live_dashboard_commits_live_operational_status_and_ui_does_not_hardcode_monitor_active():
    assert "if(status?.operations)renderOperationalStatus(status.operations);" in HTML
    assert "if(r.operations)renderOperationalStatus(r.operations);renderAccountGrid('dashboardExchangeAccounts',r)" in HTML
    assert "provider_id:provider,mode:normalizedMode(dataContextMode" in HTML
    assert "call('venue_controls',{mode})" in HTML
    assert 'selected_mode = canonical_mode_value(data.get("mode")' in API_SOURCE
    final_render_mode = HTML[HTML.rfind("renderMode=function()") : HTML.rfind("renderMode=function()") + 1500]
    assert "title.textContent=operating==='live'?'LIVE ACTIVE':'ACTIVE'" not in final_render_mode
    assert "renderDashboardMonitorStatus0955(operationalStatus)" in final_render_mode
