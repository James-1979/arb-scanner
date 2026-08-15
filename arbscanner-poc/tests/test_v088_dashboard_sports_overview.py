from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
API = (ROOT / "arbscanner" / "api.py").read_text()


def test_v088_release_and_larger_dashboard_clocks():
    assert "PoC 0.9.36" in HTML
    assert "aspect-ratio:1/1!important" in HTML
    assert "Local time" in HTML and "New York time" in HTML and "Sydney time" in HTML


def test_active_bets_exposes_emergency_hedge_marker():
    assert '"emergency_hedge": bool(emergency_hedge)' in API
    assert "EMERGENCY_HEDGE" in API
    assert 'class="hedge-alert"' in HTML
    assert "EMERGENCY HEDGE" in HTML


def test_sports_overview_is_portfolio_and_activity_first():
    assert "Sports Overview" in HTML
    assert 'id="sportsOverviewEquity"' in HTML
    assert 'id="sportsOverviewCommitted"' in HTML
    assert 'id="sportsOverviewToday"' in HTML
    assert 'id="sportsStatusScanner0935"' in HTML
    assert 'id="sportsPmQualified0935"' in HTML
    assert 'id="sportsIpQualified0935"' in HTML
    assert 'id="sportsMarketHighlights0935"' in HTML
    assert 'id="sportsOpenPositions"' in HTML
    assert "Market coverage and scan detail" not in HTML
    assert "Monitored market families" not in HTML
    assert "Open Monitor" in HTML
