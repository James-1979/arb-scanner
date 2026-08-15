from pathlib import Path

ROOT = Path(__file__).parents[1]
HTML = ROOT.joinpath("frontend", "index.html")


def _html():
    return HTML.read_text()


def test_0921_total_economics_row_is_between_activity_and_venue_accounts():
    html = _html()
    labels = ("Total Capital", "Total Capital In Play", "Total Profit Today", "Total Locked Profit")
    assert 'id="dashboardTotalEconomics"' in html
    for label in labels:
        assert label in html
    order = [
        html.index('id="dashboardLiveActivity"'),
        html.index('id="dashboardTotalEconomics"'),
        html.index('id="dashboardAccountContext"'),
        html.index('id="dashboard24hResults"'),
    ]
    assert order == sorted(order)


def test_0921_total_economics_reuses_same_venue_value_function():
    html = _html()
    assert "function dashboardVenueEconomicValues0921" in html
    assert "function renderDashboardTotalEconomics0921" in html
    assert "ids=['betfair','matchbook','smarkets']" in html
    assert "renderDashboardTotalEconomics0921(accounts,metrics,mode)" in html
    assert "2 of 3 venues reporting" not in html  # coverage is calculated, never hard-coded


def test_0921_total_economics_row_is_responsive_four_across():
    html = _html()
    assert ".dashboard-total-economics0921{grid-template-columns:repeat(4,minmax(0,1fr))!important" in html
    assert ".dashboard-daily-performance0920,.dashboard-total-economics0921{grid-template-columns:repeat(2,minmax(0,1fr))!important}" in html
