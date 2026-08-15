from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0924_performance_is_finance_and_capital_first():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    for text in (
        "Financial control view",
        "Net P&amp;L",
        "Capital",
        "Exposure",
        "Available",
        "Portfolio ROI",
        "Financial Timeline",
        "Venue Performance",
        "Deeper performance breakdown",
        "Market breakdown <span class=\"faint\">(secondary)</span>",
    ):
        assert text in HTML
    for element_id in (
        "perfCurrentCapitalTop",
        "perfCurrentExposureTop",
        "perfAvailableTop0931",
        "performanceCapitalTimeline0931",
        "performancePnlTimeline0931",
        "performanceVenueEconomicsBody",
    ):
        assert f'id="{element_id}"' in HTML
    pane = HTML.split('<div class="analytics-pane active" data-analytics-pane="performance">',1)[1].split('<div class="analytics-pane" data-analytics-pane="results">',1)[0]
    header = pane.split('</div><div id="performanceCustomRange"',1)[0]
    assert '>Type<select' not in header
    assert '>Mode<select' not in header
    assert 'performance-basis' not in header


def test_v0924_scoped_venue_capital_available_and_exposure_follow_selected_portfolio(tmp_path):
    api = API(tmp_path / "performance-scope.sqlite3")
    api.dashboard_overview({})
    accounts = api.account_overview({"mode": "sim", "capture": False})["accounts"]

    sports = api.performance_analytics(
        {
            "period": "7d",
            "scope": "sports",
            "stream": "all",
            "basis": "actual",
            "timezone_offset_minutes": 0,
        }
    )
    assert sports["ok"] is True
    assert sports["venue_capital"]["basis"] == "selected_portfolio_allocation"

    for venue_id, current in sports["venue_capital"]["current"].items():
        account = accounts[venue_id]
        selected = [x for x in account["allocations"] if x["stream"] in {"pre_match", "in_play"}]
        expected_capital = round(sum(float(x["equity"]) for x in selected), 4)
        expected_available = round(sum(float(x["available"]) for x in selected), 4)
        expected_reserved = round(sum(float(x["reserved"]) for x in selected), 4)
        assert current["capital"] == expected_capital
        assert current["available"] == expected_available
        assert current["reserved"] == expected_reserved


def test_v0924_installer_guard_matches_package():
    installer = (ROOT / "BUILD_AND_INSTALL.command").read_text()
    assert 'EXPECTED_VERSION="0.9.36"' in installer
    assert "Extract the 0.9.36 package and run its installer there." in installer
