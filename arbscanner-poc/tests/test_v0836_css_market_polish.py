from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
STYLE = HTML.split('<style>', 1)[1].split('</style>', 1)[0]


def test_v0836_release_ui_and_css_contract():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert HTML.count('<style>') == 1
    assert 'data-display-profile' not in STYLE
    assert '.dash-fit' not in STYLE
    assert '.dash-full' not in STYLE
    assert 'v0.8.42 UI stabilization' in STYLE
    assert '.nav-count-badge[hidden]{display:none!important}' in STYLE.replace(' ', '')

    # Market Analysis is the completed layout requested by the operator.
    assert 'rows=rows.slice(0,10)' in HTML
    assert 'Conversion / drop-off' not in HTML
    assert 'id="marketSportsPreDiscovery"' in HTML
    assert 'id="marketSportsInplayDiscovery"' in HTML
    assert 'id="marketRacingDiscovery"' in HTML
    assert 'Sports · Pre-match' in HTML and 'Sports · In-play' in HTML
    assert 'Greyhound discovery' in HTML

    # Replay retains the chart but removes the redundant KPI tile.
    assert 'id="timelineReplayPnl"' not in HTML
    assert 'id="timelineReplayPnlChart"' not in HTML
    assert 'timeline-running-grid{grid-template-columns:repeat(6,minmax(0,1fr))}' in STYLE.replace(' ', '')

    # Semantic result colouring + helper positioning.
    assert 'result-bestworst' in HTML
    assert 'class="best"' in HTML and 'class="worst"' in HTML
    assert '.result-bestworst .best{color:var(--good)}' in STYLE
    assert '.result-bestworst .worst{color:var(--bad)}' in STYLE
    assert '.market-summary-card .helpq{position:absolute;top:8px;right:8px' in STYLE


def test_sports_discovery_summary_excludes_greyhounds_and_aggregates_period(tmp_path):
    api = API(tmp_path / 'sports-discovery.sqlite3')
    db = api.db
    sid = db.start_scan(scan_kind='discovery')
    statuses = [
        {'exchange': 'Betfair delayed', 'ok': True, 'sport_counts': {'Football': 10, 'Tennis': 5, 'Greyhounds': 3}},
        {'exchange': 'Matchbook', 'ok': True, 'sport_counts': {'Football': 8, 'Tennis': 4, 'Greyhounds': 2}},
    ]
    db.finish_scan(
        sid, markets_seen=32, matches_seen=11, statuses=statuses,
        stage_timings={'racing_discovery': {'total': 5, 'matched': 2, 'unmatched': 1, 'rejected': 0,
                                             'by_exchange': {'Betfair delayed': 3, 'Matchbook': 2}}},
    )
    now = datetime.now(timezone.utc)
    result = api.market_analysis({
        'from_utc': (now - timedelta(minutes=5)).isoformat(),
        'to_utc': (now + timedelta(minutes=5)).isoformat(),
        'scope': 'all', 'phase': 'all', 'sport': 'all',
        'timezone_offset_minutes': 0, 'timezone_name': 'UTC',
    })
    assert result['ok'] is True
    sports = result['sports_discovery']
    assert sports['scans'] == 1
    assert sports['by_exchange']['Betfair delayed'] == 15
    assert sports['by_exchange']['Matchbook'] == 12
    assert sports['total'] == 27
    # Total matches 11 minus two Racing pairs = nine Sports pairs; 27 listings - 18 paired listings = nine unmatched.
    assert sports['matched'] == 9
    assert sports['unmatched'] == 9
    assert sports['latest']['matched'] == 9
    assert sports['latest']['unmatched'] == 9


def test_dashboard_account_copy_and_delta_precision_are_operator_facing():
    assert 'SIM virtual venue accounts · configuration and funding live in Admin' in HTML
    assert 'function accountDeltaMoney' in HTML
    assert 'maximumFractionDigits:2' in HTML
    assert '#dashboard.dashboard-clean.active{display:flex!important' in STYLE.replace(' ', '')
