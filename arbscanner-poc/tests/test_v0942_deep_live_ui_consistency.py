from __future__ import annotations

from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'frontend' / 'index.html').read_text()
API_SRC = (ROOT / 'arbscanner' / 'api.py').read_text()
DB_SRC = (ROOT / 'arbscanner' / 'db.py').read_text()




def test_0942_release_identity_and_installer_lock():
    assert __version__ == '0.9.42'
    assert '<title>ArbScanner PoC 0.9.42</title>' in HTML
    installer = (ROOT / 'BUILD_AND_INSTALL.command').read_text()
    assert 'EXPECTED_VERSION="0.9.42"' in installer
    assert 'Extract the 0.9.42 package' in installer
    assert '## 0.9.42 — Deep LIVE Isolation & UI Consistency' in (ROOT / 'RELEASE_NOTES.md').read_text()

def test_0942_active_positions_refresh_is_mode_aware():
    # The Active Positions button must never call the SIM dashboard loader directly.
    assert "standardRefresh0938(this,()=>orchestrateRouteLoad('activebets'))" in HTML
    active_section = HTML.split('<section id="activebets"', 1)[1].split('</section>', 1)[0]
    assert 'loadDashboardOverview()' not in active_section


def test_0942_live_market_analysis_operator_qualified_is_zero_but_decision_evidence_is_retained(tmp_path, monkeypatch):
    api = API(tmp_path / 'live-market.sqlite3')
    monkeypatch.setattr(api, 'market_analysis', lambda data: {
        'ok': True,
        'rows': [{'section':'sports','sport':'Football','market_name':'Match Odds','qualified':7,'attempts':3,'executed':2,'settled':1,'pnl':5}],
        'liquidity_funnel': {'observed': 20, 'positive': 4, 'liquidity_capable': 2, 'qualified': 7, 'attempted':3,'executed':2,'settled':1},
        'venue_summary': [], 'reasons': [],
    })
    monkeypatch.setattr(api.db, 'live_decision_analytics', lambda *a, **k: {
        'summary': {'qualified': 5},
        'markets': [{'domain':'sports','sport':'Football','market_type':'Match Odds','qualified':5,'simulated_attempts':4,'execution_grade':2}],
        'quality': [], 'reasons': [],
    })
    out = api.live_market_analysis({'scope':'sports','mode':'live'})
    assert out['rows'][0]['qualified'] == 0
    assert out['rows'][0]['live_decision_qualified'] == 5
    assert out['liquidity_funnel']['qualified'] == 0
    assert out['live_decision_qualified'] == 5
    assert out['liquidity_funnel']['executed'] == 0
    assert out['financial_time_basis'] == 'no_live_execution'


def test_0942_live_racing_highlights_require_meaningful_provider_evidence(tmp_path, monkeypatch):
    api = API(tmp_path / 'live-racing.sqlite3')

    async def money(*args, **kwargs):
        return {'mode':'live','scope':'racing','capital':None,'available':None,'capital_deployed':None,'rows':[]}

    monkeypatch.setattr(api, '_live_portfolio_financial_state_async', money)
    monkeypatch.setattr(api, '_operational_status', lambda mode=None: {'mode':mode,'feeds':[]})
    # Rows exist, but none is display-worthy: no positive edge/provider pair/market.
    monkeypatch.setattr(api, 'live_decision_evidence', lambda data=None: {
        'ok': True,
        'latest': [
            {'event_name':'Race A','market_name':'Win','net_edge_pct':None,'provider_pair':'betfair|matchbook'},
            {'event_name':'Race B','market_name':'Win','net_edge_pct':0,'provider_pair':'betfair|matchbook'},
            {'event_name':'Race C','market_name':'','net_edge_pct':1.2,'provider_pair':'betfair|matchbook'},
            {'event_name':'Race D','market_name':'Win','net_edge_pct':1.1,'provider_pair':''},
        ],
        'summary': {'qualified': 4},
    })
    out = api.racing_overview({'mode':'live'})
    assert out['mode'] == 'live'
    assert out['highlights'] == []
    assert out['summary']['qualified_monitor'] == 0
    assert out['positions'] == [] and out['active_positions'] == 0


def test_0942_live_performance_empty_state_clears_fixed_inspector():
    assert 'function performanceClearInspector0942()' in HTML
    block = HTML.split('performanceEmptyLive0931=function', 1)[1].split('// Engines:', 1)[0]
    assert 'performanceClearInspector0942()' in block
    for field in ('performanceInspectorTime0937','performanceInspectorCapital0937','performanceInspectorAvailable0937',
                  'performanceInspectorDeployed0937','performanceInspectorPnl0937','performanceInspectorSettled0937'):
        assert field in HTML


def test_0942_engine_list_period_buttons_and_stream_column_replace_top_dropdowns():
    sports = HTML.split('<section id="sports-engines"', 1)[1].split('</section>', 1)[0]
    racing = HTML.split('<section id="racing-engines"', 1)[1].split('</section>', 1)[0]
    for section in (sports, racing):
        assert 'engine-period-buttons0942' in section
        assert '>Search<' in section or 'Search<input' in section
        assert 'type="hidden" value="today"' in section
    # Removed operator-facing period/status/stream selects from engine headers.
    assert 'id="sportsEngineStatus0936"' not in sports
    assert 'id="sportsEngineStream0936"' not in sports
    assert 'id="racingEngineStatus0941"' not in racing
    assert '<th>Stream</th>' in HTML
    for label in ('Today','Yesterday','7 Days','30 Days'):
        assert label in HTML
    assert '"streams": sorted(streams_seen)' in DB_SRC


def test_0942_monitor_results_have_one_visible_filter_surface_and_positive_toggle_style():
    assert "secondary.hidden=true;secondary.setAttribute('aria-hidden','true')" in HTML
    assert 'function upgradeMonitorFilters0942()' in HTML
    assert 'function upgradeResultsFilters0942()' in HTML
    assert 'sportsSeen0942' in HTML and 'resultsObservedSports0942' in HTML
    assert 'overflow-x:auto' in HTML
    # Selected controls use a light positive selection, not white-on-dark inversion.
    css = HTML.split('<style id="v0942-deep-fix-css">', 1)[1].split('</style>', 1)[0]
    assert 'background:#eaf2ff' in css
    assert 'color:#175cd3' in css


def test_0942_racing_monitor_filters_are_one_horizontal_line():
    assert 'function upgradeRacingMonitorFilters0942()' in HTML
    css = HTML.split('<style id="v0942-deep-fix-css">', 1)[1].split('</style>', 1)[0]
    assert '.racing-monitor-filters0941{display:flex!important' in css
    assert 'flex-wrap:nowrap!important' in css
    assert "fast.insertBefore(venue,search)" in HTML


def test_0942_replay_period_activity_is_engine_first_and_legacy_not_inferred(tmp_path):
    api = API(tmp_path / 'replay.sqlite3')
    out = api.activity_analytics({'include_results':False,'include_executions':True,'include_metrics':False,'include_all_time':False,'timeline_range':True,'limit':10})
    assert out['ok'] is True
    assert set(out['period_activity']) == {'sports','engines','markets'}
    assert out['period_activity']['engines'] == []
    assert 'Engines in this period' in HTML
    assert 'Most active engine' in HTML
    assert 'Legacy / Unverified' in API_SRC
    assert 'engine_provenance_source' in API_SRC
    assert 'function replayActivitySelectEngine0942' in HTML
    assert 'data-replay-engine' in HTML


def test_0942_engine_drawers_are_solid_and_racing_mirrors_full_width_engine_grammar():
    css = HTML.split('<style id="v0942-deep-fix-css">', 1)[1].split('</style>', 1)[0]
    assert '.engine-drawer0938,.racing-engine-drawer0941' in css
    assert 'background:var(--surface)!important' in css
    assert 'backdrop-filter:none!important' in css
    assert 'renderSportsEngines0936=function' in HTML
    assert 'renderRacingEngines0941=function' in HTML
    assert 'Engine</th><th>State</th><th>Stream</th>' in HTML


def test_0942_replay_engine_period_index_uses_authoritative_position_origin(tmp_path):
    api = API(tmp_path / 'replay-engine.sqlite3')
    db = api.db
    db.ensure_default_engines()
    db.reset_monitor_wallets({'betfair': 100.0, 'matchbook': 100.0})
    legs = [
        {'exchange':'Betfair delayed','provider_id':'betfair','venue_id':'betfair','selection':'A','odds':2.1,'liquidity':100},
        {'exchange':'Matchbook','provider_id':'matchbook','venue_id':'matchbook','selection':'B','odds':2.1,'liquidity':100},
    ]
    oid = db.add_opportunity(
        'evt-942', 'A v B', None, 'Match Winner', 2.0, 1.0, legs, [], 1.0, 'v0942',
        sport='Tennis', section='sports', engine_instance_id='SPORTS_BASELINE_ARB_PRIMARY',
        engine_type='SPORTS_BASELINE_ARB', engine_version='1.0.0', engine_config_version=1,
    )
    db.set_opportunity_qualification(oid, 'qualified', 'test')
    run_id = db.add_execution_run(oid, 'sim', 'modeled_monitor', 'MONITOR_OPEN', deployed=20.0, expected_profit=1.0, captured_profit=1.0)
    ok, reason = db.open_monitor_position(
        opportunity_id=oid, execution_run_id=run_id, event_key='evt-942', market_name='Match Winner', deployed=20.0,
        expected_profit=1.0, stakes_by_exchange={'betfair':10.0,'matchbook':10.0},
        outcome_exchange_pnls={'A':{'betfair':2.0,'matchbook':-0.5},'B':{'betfair':-0.5,'matchbook':2.0}},
        simulation={'after_hedge':{'balanced':True}}, stream='pre_match',
    )
    assert ok, reason
    settled = db.settle_monitor_position(oid, 'A')
    assert settled['ok'] is True
    out = api.activity_analytics({'include_results':False,'include_executions':True,'include_metrics':False,'include_all_time':False,'timeline_range':True,'limit':100})
    engines = out['period_activity']['engines']
    baseline = next(x for x in engines if x['engine_id'] == 'SPORTS_BASELINE_ARB_PRIMARY')
    assert baseline['authoritative'] is True
    assert baseline['engine'] == 'Baseline'
    assert baseline['positions'] == 1
    assert baseline['position_ids'] == [oid]
    assert baseline['pnl'] == round(float(settled['realized_pnl']), 4)
