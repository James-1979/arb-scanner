from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0844_identity_and_decision_performance_layout():
    assert __version__ == "0.9.36"
    # Decision/venue diagnostics remain available, but 0.9.36 makes the primary
    # Performance surface a financial control view.
    for text in (
        "Net P&amp;L",
        "Portfolio ROI",
        "Return on Deployed",
        "Captured Edge",
        "Financial Timeline",
        "Capital &amp; Exposure",
        "Venue Performance",
        "Market breakdown",
        "Venue pair performance",
        "Performance funnel",
        "Recovery cost",
    ):
        assert text in HTML
    assert 'id="performanceCapitalTimeline0931"' in HTML
    assert 'id="performancePnlTimeline0931"' in HTML
    assert 'id="performanceDomainGrid"' in HTML
    assert 'id="performanceVenueBody"' in HTML
    assert 'id="performancePairBody"' in HTML
    assert 'id="performanceDeployedChart"' not in HTML


def test_performance_returns_canonical_exchange_capital_series(tmp_path):
    api = API(tmp_path / "perf-exchange.sqlite3")
    api.dashboard_overview({})
    captured = api.account_overview({"mode": "sim", "capture": True, "context": "test_performance"})
    assert captured["ok"] is True

    result = api.performance_analytics({
        "period": "7d",
        "scope": "all",
        "stream": "all",
        "basis": "actual",
        "timezone_offset_minutes": 0,
    })
    assert result["ok"] is True
    exchange = result["exchange_capital"]
    assert exchange["basis"] == "account_equity"
    assert len(exchange["rows"]) == 7
    assert exchange["current"]["betfair"]["capital"] is not None
    assert exchange["current"]["matchbook"]["capital"] is not None
    assert exchange["rows"][-1]["betfair"] is not None
    assert exchange["rows"][-1]["matchbook"] is not None
    # The account series remains canonical supporting evidence even though the
    # default Performance UI is now decision/venue focused rather than a three-chart page.
    assert "performance" in result
    assert "venues" in result["performance"]
    assert "venue_pairs" in result["performance"]

    sports = api.performance_analytics({
        "period": "7d",
        "scope": "sports",
        "stream": "all",
        "basis": "actual",
        "timezone_offset_minutes": 0,
    })
    assert sports["exchange_capital"]["basis"] == "selected_portfolio_allocation"
    assert sports["exchange_capital"]["current"]["betfair"]["capital"] <= exchange["current"]["betfair"]["equity"]
