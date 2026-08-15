from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0925_latest_result_uses_existing_dashboard_header_whitespace_without_layout_flow():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert 'id="dashboardLatestResult0925"' in HTML
    assert 'class="dashboard-latest-result0925 result-empty0925"' in HTML
    assert '.dashboard-latest-result0925{position:absolute;' in HTML
    assert 'right:0;bottom:0;width:calc(50% - 5px);height:30px' in HTML
    assert '.dashboard-section-head0920{position:relative}' in HTML
    # It must live in the Venue Accounts header, not as a new Dashboard row.
    venue_head = HTML.index('<div class="dashboard-section-head0920"><div><h2>Venue Accounts</h2>')
    ticker = HTML.index('id="dashboardLatestResult0925"')
    venue_grid = HTML.index('id="dashboardExchangeAccounts"')
    assert venue_head < ticker < venue_grid


def test_v0925_sim_and_live_ticker_reads_are_explicitly_separate(tmp_path):
    api = API(tmp_path / "latest-result.sqlite3")

    sim_row = {
        "opportunity_id": 11,
        "execution_run_id": 22,
        "event_name": "Everton vs Liverpool",
        "market_name": "Match Winner",
        "sport": "Football",
        "monitor_stream": "pre_match",
        "outcome": "Liverpool",
        "final_pnl": 12.34,
        "settled_at": "2026-08-14T00:01:00+00:00",
        "details": {},
    }

    def no_live_results(_data=None):
        raise AssertionError("SIM latest-result endpoint must not consult LIVE results")

    api.live_results = no_live_results
    api.db.settled_monitor_positions = lambda **_kwargs: [sim_row]
    sim = api.dashboard_latest_sim_result({"domain": "sports"})
    assert sim["ok"] is True
    assert sim["mode"] == "sim"
    assert sim["source"] == "sim_monitor_settlement"
    assert sim["sim_fallback_used"] is False
    assert sim["result"]["event_name"] == "Everton vs Liverpool"
    assert sim["result"]["outcome"] == "Liverpool"
    assert sim["result"]["pnl"] == 12.34

    def no_sim_rows(**_kwargs):
        raise AssertionError("LIVE latest-result endpoint must not consult SIM settlements")

    api.db.settled_monitor_positions = no_sim_rows
    api.live_results = lambda _data=None: {
        "ok": True,
        "mode": "live",
        "rows": [{
            "opportunity_id": 90,
            "execution_id": 91,
            "event_name": "Everton vs Liverpool",
            "market_name": "Match Winner",
            "outcome": "Liverpool",
            "pnl": -7.5,
            "settled_at": "2026-08-14T00:02:00+00:00",
        }],
    }
    live = api.dashboard_latest_live_result({"domain": "sports"})
    assert live["ok"] is True
    assert live["mode"] == "live"
    assert live["source"] == "actual_live_results"
    assert live["sim_fallback_used"] is False
    assert live["result"]["pnl"] == -7.5


def test_v0925_live_empty_state_stays_actual_live_only(tmp_path):
    api = API(tmp_path / "latest-live-empty.sqlite3")
    api.db.settled_monitor_positions = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("LIVE empty state must not fall back to SIM")
    )
    result = api.dashboard_latest_live_result({"domain": "all"})
    assert result == {
        "ok": True,
        "mode": "live",
        "source": "actual_live_results",
        "result": None,
        "sim_fallback_used": False,
    }


def test_v0925_frontend_clears_on_mode_switch_and_branches_endpoint_by_mode():
    assert "primeDashboardLatestResult0925(mode);let cached=" in HTML
    assert "mode==='live'?'dashboard_latest_live_result':'dashboard_latest_sim_result'" in HTML
    assert "mode==='live'?'No LIVE settled results yet':'No settled results yet'" in HTML
    assert "mode==='live'?'actual LIVE only':'SIM Monitor'" in HTML
    assert "loadDashboardLatestResult0925()" in HTML
    assert "dashboardResultEventHtml0925" in HTML
    assert "result-winner0925" in HTML
    assert "result-loser0925" in HTML


def test_v0925_installer_guard_matches_package():
    installer = (ROOT / "BUILD_AND_INSTALL.command").read_text()
    assert 'EXPECTED_VERSION="0.9.36"' in installer
    assert "Extract the 0.9.36 package and run its installer there." in installer
