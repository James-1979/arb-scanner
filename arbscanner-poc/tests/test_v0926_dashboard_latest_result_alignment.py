from pathlib import Path

from arbscanner import __version__

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0926_ticker_aligns_to_third_total_economics_column_without_moving_layout():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert 'right:0;bottom:0;width:calc(50% - 5px);height:30px' in HTML
    assert '.dashboard-section-head0920{position:relative}' in HTML
    assert '.dashboard-total-economics0921{grid-template-columns:repeat(4,minmax(0,1fr))!important' in HTML
    venue_head = HTML.index('<div class="dashboard-section-head0920"><div><h2>Venue Accounts</h2>')
    ticker = HTML.index('id="dashboardLatestResult0925"')
    venue_grid = HTML.index('id="dashboardExchangeAccounts"')
    assert venue_head < ticker < venue_grid


def test_v0926_ticker_typography_is_stronger_but_height_is_unchanged():
    assert 'font-size:10px;font-weight:700;line-height:1' in HTML
    assert '.latest-event0925{min-width:0;overflow:hidden;text-overflow:ellipsis;font-weight:950}' in HTML
    assert '.latest-market0925{min-width:0;overflow:hidden;text-overflow:ellipsis;color:var(--text);font-weight:750;opacity:.72}' in HTML
    assert '.latest-outcome0925{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;font-weight:950' in HTML
    assert '.latest-time0925{flex:0 0 auto;color:var(--text);font-size:9px;font-weight:750;opacity:.68}' in HTML


def test_v0926_installer_guard_matches_package():
    installer = (ROOT / "BUILD_AND_INSTALL.command").read_text()
    assert 'EXPECTED_VERSION="0.9.36"' in installer
    assert 'Extract the 0.9.36 package and run its installer there.' in installer
