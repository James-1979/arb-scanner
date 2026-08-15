from datetime import datetime, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'frontend' / 'index.html').read_text()


def performance_pane():
    return HTML.split('<div class="analytics-pane active" data-analytics-pane="performance">', 1)[1].split('<div class="analytics-pane" data-analytics-pane="results">', 1)[0]


def test_v0931_performance_finance_first_contract():
    pane = performance_pane()
    assert __version__ == '0.9.39'
    assert '<title>ArbScanner PoC 0.9.39</title>' in HTML
    header = pane.split('</div><div id="performanceCustomRange"', 1)[0]
    assert '>Period<select id="performancePeriod"' in header
    assert '>Portfolio<select id="performanceScope"' in header
    assert '>Venue<select id="performanceVenue"' in header
    assert '>Mode<select' not in header
    assert '>Type<select' not in header
    assert 'performance-basis' not in header
    assert pane.count('<div class="card performance-kpi">') == 5
    for text in ('Net P&amp;L', 'Capital', 'Exposure', 'Available', 'Portfolio ROI'):
        assert text in pane
    for element_id in ('performanceCapitalTimeline0931', 'performancePnlTimeline0931', 'performanceVenueEconomicsBody'):
        assert f'id="{element_id}"' in pane
    assert 'Settled Turnover' in pane
    assert 'Return on Deployed' in pane
    assert 'Peak Exposure' in pane
    assert 'Average Utilisation' in pane
    assert 'Deeper performance breakdown' in pane


def test_v0931_mode_is_global_and_endpoints_enforce_isolation(tmp_path):
    api = API(tmp_path / 'mode-guard.sqlite3')
    sim_wrong = api.performance_analytics({'mode': 'live'})
    live_wrong = api.live_performance({'mode': 'sim'})
    assert sim_wrong['ok'] is False
    assert live_wrong['ok'] is False
    assert 'SIM-only' in sim_wrong['message']
    assert 'LIVE-only' in live_wrong['message']
    assert "mode,scope" in HTML or "mode,scope:" in HTML
    assert "performancePayload0931('sim')" in HTML
    assert "performancePayload0931('live')" in HTML


def test_v0931_today_is_intraday_and_timeline_reconciles_to_headline(tmp_path):
    api = API(tmp_path / 'intraday.sqlite3')
    api.dashboard_overview({})
    api.account_overview({'mode': 'sim', 'capture': True, 'context': 'v0931_test'})
    result = api.performance_analytics({
        'mode': 'sim', 'period': 'today', 'scope': 'all', 'stream': 'all', 'basis': 'actual',
        'timezone_offset_minutes': 0,
    })
    assert result['ok'] is True
    assert result['timeline_granularity'] == 'hour'
    # A normal day after midnight contains multiple intraday buckets; at minimum
    # the API must no longer collapse Today to one daily point.
    now_hour = datetime.now(timezone.utc).hour
    if now_hour >= 1:
        assert len(result['rows']) >= 2
    assert result['rows']
    last = result['rows'][-1]
    summary = result['summary']
    assert last['cumulative_period_profit'] == summary['net_pnl']
    if summary['current_capital'] is not None:
        assert last['capital'] == summary['current_capital']
    if summary['current_exposure'] is not None:
        assert last['exposure'] == summary['current_exposure']
    if summary['current_available'] is not None:
        assert last['available'] == summary['current_available']


def test_v0931_current_capital_available_exposure_reconcile(tmp_path):
    api = API(tmp_path / 'reconcile.sqlite3')
    api.dashboard_overview({})
    result = api.performance_analytics({'mode': 'sim', 'period': '7d', 'scope': 'all', 'venue': 'all', 'basis': 'actual', 'timezone_offset_minutes': 0})
    assert result['ok'] is True
    s = result['summary']
    assert s['current_capital'] is not None
    assert s['current_available'] is not None
    assert s['current_exposure'] is not None
    assert round(s['current_available'] + s['current_exposure'], 4) == round(s['current_capital'], 4)


def test_v0931_filter_refresh_is_one_versioned_page_state():
    assert 'performanceRequestVersion0931' in HTML
    assert 'seq!==performanceRequestVersion0931' in HTML
    assert 'performanceSetLoading0931(true)' in HTML
    assert "dataContextMode!=='sim'" in HTML
    assert "dataContextMode!=='live'" in HTML
