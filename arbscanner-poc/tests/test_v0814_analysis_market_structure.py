from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API, DEFAULT_CONFIG, OPERATING_MODES

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
SCANNER = (ROOT / "arbscanner" / "scanner.py").read_text()


def test_v0814_version_and_live_lock():
    assert __version__ == "0.9.36"
    assert OPERATING_MODES["live"]["available"] is False
    assert DEFAULT_CONFIG["display_profile"] == "auto"
    assert "PoC 0.9.36" in HTML


def test_position_language_and_market_navigation_are_explicit():
    assert ">Active Positions<" in HTML
    assert 'data-tab="sports-config"' in HTML
    assert 'data-tab="racing-monitor"' in HTML
    assert 'data-tab="racing-config"' in HTML
    assert "Position #${Number(x.opportunity_id" in HTML
    assert "planned leg" in HTML
    assert "balancing/recovery" in HTML


def test_market_analysis_racing_monitor_and_automatic_layout_exist():
    for token in (
        'id="marketAnalysisRows"',
        'id="marketActivityHours"',
        'id="marketRacingDiscovery"',
        'id="racingMonitorRows"',
        'id="racingMonStatus"',
        'id="racingMonQuality"',
        'id="racingMonType"',
        'Dashboard layout is fully automatic',
        'function fitDashboardToViewport',
        "function loadMarketAnalysis()",
        "function loadRacingMonitor()",
    ):
        assert token in HTML


def test_replay_supports_today_24h_7d_and_custom_period():
    assert '<option value="7d">7 days</option>' in HTML
    assert '<option value="24h">24 hours</option>' in HTML
    assert '<option value="today" selected>Today</option>' in HTML
    assert '<option value="custom">Custom period</option>' in HTML
    assert '<option value="previous_day">' not in HTML
    assert 'id="timelineReplayFrom"' in HTML
    assert 'id="timelineReplayTo"' in HTML
    assert "function timelineReplayPeriodChanged()" in HTML
    assert ".timeline-return-marker .return-value,.timeline-position-marker .position-value{display:none}" in HTML


def test_dashboard_24h_results_and_execution_market_filter_exist():
    for token in (
        'id="dashBestWin"',
        'id="dashWins24h"',
        'id="dashLosses24h"',
        'id="dashWinRate24h"',
        "function loadDashboardResults24h()",
        'id="executionsMarket"',
    ):
        assert token in HTML


def test_api_read_only_analysis_endpoints_smoke(tmp_path):
    api = API(tmp_path / "v0814.sqlite3")
    market = api.market_analysis({"scope": "all", "phase": "all", "timezone_offset_minutes": -60})
    assert market["ok"] is True
    assert len(market["activity_hours"]) == 24
    results = api.dashboard_results_24h({})
    assert results["ok"] is True
    assert {"wins", "losses", "settled", "win_rate_pct", "best_win"} <= results.keys()
    racing = api.racing_monitor({})
    assert racing["ok"] is True
    assert racing["monitor_execution_allowed"] is True
    assert racing["live_execution_allowed"] is False


def test_racing_raw_discovery_and_monitor_qualification_path_are_persisted():
    assert 'set_setting("racing_discovery_latest", racing_diagnostics)' in SCANNER
    assert '"match_status": status' in SCANNER
    assert '"matched_sources"' in SCANNER
    assert 'raw_event.get("countryCode")' in SCANNER
    assert "Qualified for pre-race Greyhound MONITOR execution; LIVE orders remain hard-locked" in SCANNER
    assert "racing_qualified" in SCANNER
