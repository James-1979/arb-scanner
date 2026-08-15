from pathlib import Path

import pytest

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
API_TEXT = (ROOT / "arbscanner" / "api.py").read_text()


def sports_pane():
    return HTML.split('<section id="sports"', 1)[1].split('</section>', 1)[0]


def performance_pane():
    return HTML.split('<div class="analytics-pane active" data-analytics-pane="performance">', 1)[1].split('<div class="analytics-pane" data-analytics-pane="results">', 1)[0]


def test_0935_release_identity_and_shared_financial_endpoint():
    assert __version__ == "0.9.39"
    assert '<title>ArbScanner PoC 0.9.39</title>' in HTML
    assert 'EXPECTED_VERSION="0.9.39"' in (ROOT / "BUILD_AND_INSTALL.command").read_text()
    assert "def portfolio_financial_state" in API_TEXT
    assert "def sports_overview" in API_TEXT


def test_0935_sports_overview_is_operational_not_market_analysis():
    pane = sports_pane()
    for label in ("Total Sports Capital", "Available", "Capital Deployed", "Active Positions", "Today P&amp;L"):
        assert label in pane
    for label in ("Sports Status", "Pre-match", "In-play", "Market Highlights", "Current Sports Positions"):
        assert label in pane
    for element_id in (
        "sportsStatusScanner0935", "sportsStatusPre0935", "sportsStatusInplay0935", "sportsStatusFresh0935",
        "sportsMarketHighlights0935", "sportsOpenPositions", "sportsException0935",
    ):
        assert f'id="{element_id}"' in pane
    # Global mode is authoritative; Sports has no local mode selector.
    assert '>Mode<select' not in pane
    assert 'id="sportsMode"' not in pane
    # Historical analytics remain outside this operational page.
    for banned in ("7-day profit", "Heatmap", "Historical ROI", "Opportunity funnel", "Conversion rate"):
        assert banned not in pane
    assert "high.slice(0,3)" in HTML


def test_0935_sim_sports_money_is_authoritative_stream_wallet_subset(tmp_path):
    api = API(tmp_path / "sports-scope.sqlite3")
    sports = api.portfolio_financial_state({"mode": "sim", "scope": "sports", "venue": "all"})["current"]
    racing = api.portfolio_financial_state({"mode": "sim", "scope": "racing", "venue": "all"})["current"]
    all_money = api.portfolio_financial_state({"mode": "sim", "scope": "all", "venue": "all"})["current"]

    assert sports["capital"] is not None
    assert racing["capital"] is not None
    assert all_money["capital"] == pytest.approx(sports["capital"] + racing["capital"])
    assert all_money["available"] == pytest.approx(sports["available"] + racing["available"])
    assert all_money["capital_deployed"] == pytest.approx(sports["capital_deployed"] + racing["capital_deployed"])
    assert set(sports["streams"]) == {"pre_match", "in_play"}
    assert sports["reporting_venues"] == 2


def test_0935_sports_overview_and_performance_share_current_money_definition(tmp_path):
    api = API(tmp_path / "shared-scope.sqlite3")
    overview = api.sports_overview({"mode": "sim", "timezone_offset_minutes": 0})
    performance = api.performance_analytics({
        "mode": "sim", "period": "today", "scope": "sports", "venue": "all",
        "stream": "all", "basis": "actual", "timezone_offset_minutes": 0,
    })
    assert overview["ok"] and performance["ok"]
    financial = overview["financial"]
    summary = performance["summary"]
    assert summary["current_capital"] == pytest.approx(financial["capital"])
    assert summary["current_available"] == pytest.approx(financial["available"])
    assert summary["current_exposure"] == pytest.approx(financial["capital_deployed"])
    # Guard the 0.9.39 reconciliation fix: current account totals must not be doubled.
    all_money = api.portfolio_financial_state({"mode": "sim", "scope": "all", "venue": "all"})["current"]
    all_perf = api.performance_analytics({"mode": "sim", "period": "today", "scope": "all", "venue": "all", "basis": "actual", "timezone_offset_minutes": 0})
    assert all_perf["summary"]["current_capital"] == pytest.approx(all_money["capital"])


def test_0935_live_sports_does_not_fall_back_to_sim_portfolio_money(tmp_path):
    api = API(tmp_path / "live-scope.sqlite3")
    sim = api.portfolio_financial_state({"mode": "sim", "scope": "sports"})["current"]
    live = api.portfolio_financial_state({"mode": "live", "scope": "sports"})["current"]
    assert sim["capital"] is not None
    # Fresh LIVE account balances may exist without Sports/Racing allocation provenance.
    # In that case the correct Sports value is unavailable, never the SIM value.
    if live["capital"] is None:
        assert live["available"] is None
        assert live["capital_deployed"] is None
    else:
        assert live["mode"] == "live"
    overview = api.sports_overview({"mode": "live"})
    assert overview["mode"] == "live"
    assert "SIM never fills" in overview["message"]


def test_0935_performance_filters_refresh_and_playback_contract():
    pane = performance_pane()
    header = pane.split('</div><div id="performanceCustomRange"', 1)[0]
    assert '>Period<select id="performancePeriod"' in header
    assert '>Portfolio<select id="performanceScope"' in header
    assert '>Venue<select id="performanceVenue"' in header
    assert '>Mode<select' not in header
    assert 'performancePlay0935' in pane and 'performancePause0935' in pane and 'performanceReset0935' in pane
    assert 'Capital Position' in pane or 'Capital over time' in pane
    assert 'In Open Positions' in pane or 'Capital Deployed' in pane
    assert 'performanceSharedPlayhead0935' in pane
    for text in ("◌ Refreshing…", "✓ Updated", "Refresh failed"):
        assert text in HTML
    assert "seq!==performanceRequestVersion0931" in HTML
    assert "performanceStopPlayback0935(true)" in HTML


def test_0935_performance_playback_is_visual_only_and_tooltip_complete():
    # Reveal uses an overlay/playhead and does not mutate financial row values.
    assert "performance-reveal-overlay0935" in HTML
    assert "performanceApplyReveal0935" in HTML
    assert "deployed-path0935" in HTML
    assert "let min=Math.min(...valid),max=Math.max(...valid),lo=min<0" in HTML
    for label in (
        "Capital", "Available", "Capital Deployed", "Utilisation",
        "Bucket P&amp;L", "Cumulative P&amp;L", "Settled Positions",
    ):
        assert label in HTML
    assert "performancePlaybackPaused0935" in HTML
    assert "Completed" in HTML


def test_0935_performance_filter_labels_are_above_controls():
    assert '.analytics-viewhead .performance-header-filters0931>label{display:flex;flex-direction:column;align-items:flex-start}' in HTML
