from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'frontend' / 'index.html').read_text()
API_SRC = (ROOT / 'arbscanner' / 'api.py').read_text()


def test_0943_release_identity():
    assert __version__ == '0.9.43'
    assert '<title>ArbScanner PoC 0.9.43</title>' in HTML
    assert 'PoC 0.9.43' in HTML


def test_0943_actual_live_execution_contract_owns_qualified(tmp_path):
    api = API(db_path=tmp_path / 'live.sqlite3')
    out = api.live_execution_activity({'domain': 'all'})
    assert out['mode'] == 'live'
    assert out['metrics']['qualified'] == 0
    assert out['metrics']['positions'] == 0
    assert out['live_execution_allowed'] is False


def test_0943_live_engine_lifecycle_keeps_decision_qualification_diagnostic_only(tmp_path):
    api = API(db_path=tmp_path / 'engines.sqlite3')
    api.db.engine_lifecycle_rows = lambda **kwargs: [{
        'engine_instance_id': 'E1', 'nickname': 'Engine One', 'processed': 12,
        'opportunities': 4, 'qualified': 3, 'executed': 0, 'settled': 0,
        'realised_pnl': 0.0, 'errors': 0, 'streams': ['pre_match'],
    }]
    out = api.engine_lifecycle({'section': 'sports', 'mode': 'live'})
    assert out['rows'][0]['qualified'] == 0
    assert out['rows'][0]['decision_qualified_evidence'] == 3
    assert out['totals']['qualified'] == 0
    assert out['totals']['decision_qualified_evidence'] == 3


def test_0943_live_racing_uses_future_shared_schedule_and_never_promotes_decision_qualified(tmp_path, monkeypatch):
    api = API(db_path=tmp_path / 'racing.sqlite3')
    now = datetime.now(timezone.utc)
    past = (now - timedelta(minutes=12)).isoformat()
    future = (now + timedelta(minutes=18)).isoformat()
    api.db.set_setting('racing_discovery_latest', {
        'observed_at': now.isoformat(),
        'summary': {'matched': 6, 'candidates': 8},
        'rows': [
            {'event_name': 'Past Track', 'market_name': 'Win', 'event_start': past, 'market_status': 'OPEN', 'match_status': 'matched'},
            {'event_name': 'Future Track', 'market_name': 'Win', 'event_start': future, 'market_status': 'OPEN', 'match_status': 'matched'},
        ],
    })

    async def money(*args, **kwargs):
        return {'mode': 'live', 'scope': 'racing', 'capital': None, 'available': None, 'capital_deployed': None, 'rows': []}

    monkeypatch.setattr(api, '_live_portfolio_financial_state_async', money)
    monkeypatch.setattr(api, '_operational_status', lambda mode=None: {'selected_mode': mode, 'feeds': []})
    monkeypatch.setattr(api, 'live_decision_evidence', lambda data=None: {
        'ok': True,
        'latest': [
            {'event_name': 'Past Track', 'market_name': 'Win', 'net_roi_pct': 2.0, 'provider_pair': 'betfair|matchbook'},
            {'event_name': 'Future Track', 'market_name': 'Win', 'net_roi_pct': 1.5, 'provider_pair': 'betfair|matchbook', 'last_seen': now.isoformat()},
            {'event_name': 'Unknown Track', 'market_name': 'Win', 'net_roi_pct': 3.0, 'provider_pair': 'betfair|matchbook'},
        ],
        'summary': {'positive': 3, 'qualified': 7},
    })
    out = api.racing_overview({'mode': 'live'})
    # Only the future matched race is current; the past discovery row is ignored.
    assert out['summary']['matched_races'] == 1
    assert out['summary']['candidate_races'] == 1
    assert out['summary']['net_positive'] == 1
    assert out['summary']['qualified_monitor'] == 0
    assert out['summary']['decision_qualified_evidence'] == 7
    assert out['summary']['next_off'] == future
    assert all(datetime.fromisoformat(x['event_start']) >= now for x in out['upcoming'])
    # Simulated LIVE decisions never become operator Race Highlights.
    assert out['highlights'] == []
    assert out['rows'] == []


def test_0943_frontend_live_activity_uses_actual_qualified_not_decision_qualified():
    block = HTML.split('renderLiveDashboardActivity0922=function(shared,decisions,actual,operations){', 1)[1].split('// When the generic operational renderer refreshes', 1)[0]
    assert 'qualified:Number(m.qualified||0)' in block
    assert 'decisionQualifiedEvidence:Number(s.qualified||0)' in block
    assert 'decision-qualified evidence' in block


def test_0943_dashboard_racing_is_mode_aware_and_cleared_on_live_entry():
    block = HTML.split('loadDashboardRacingSummary=async function(){', 1)[1].split('// LIVE dashboard refresh hydrates Racing', 1)[0]
    assert "let mode=normalizedMode(dataContextMode" in block
    assert "racing_overview',[{mode,limit:500" in block
    assert "modeRequestToken(mode,'dashboard')" in block
    assert "modeRequestToken('sim')" not in block
    assert "sm.qualified_monitor||0" in block
    clear = HTML.split('// Clear Racing summary data as part of LIVE shell priming', 1)[1].split('// Dashboard Racing summary is selected-mode aware', 1)[0]
    for field in ('dashRaceMatchedSummary', 'dashRacePositiveSummary', 'dashRaceQualifiedSummary', 'dashRaceNextOffSummary'):
        assert field in clear
    live = HTML.split('// LIVE dashboard refresh hydrates Racing', 1)[1].split('// Dashboard provider label distinguishes', 1)[0]
    assert 'loadDashboardRacingSummary()' in live
    assert "if($('scanOppCount'))$('scanOppCount').textContent='0'" in live


def test_0943_feed_card_separates_account_ready_from_market_feed_expectation():
    block = HTML.split('// Dashboard provider label distinguishes account connectivity', 1)[1].split('// LIVE Sports Monitor rows', 1)[0]
    assert "st.textContent='ACCOUNT READY'" in block
    assert "bits.push('market feed not expected')" in block
    assert 'feedExpected' in block and 'accountExpected' in block


def test_0943_live_monitor_decision_rows_do_not_map_to_recommended_qualified():
    block = HTML.split('// LIVE Sports Monitor rows are decision evidence', 1)[1].split('function init0943', 1)[0]
    assert "status:positive?'positive':'rejected'" in block
    assert "status:qualified?'recommended'" not in block
    assert 'decision_evidence_only:true' in block


def test_0943_operational_status_live_suffix_fails_closed_and_preserves_decision_diagnostic():
    block = API_SRC.split('        pipeline={"fetched":', 1)[1].split('        pipeline["failure_reasons"]', 1)[0]
    assert 'if selected_mode == "live"' in block
    assert 'pipeline["decision_qualified_evidence"]' in block
    assert 'pipeline["qualified"] = 0' in block
    assert 'pipeline["executed"] = 0' in block


def test_0943_sports_live_decision_evidence_is_not_promoted_to_highlights(tmp_path, monkeypatch):
    api = API(db_path=tmp_path / 'sports-live.sqlite3')
    async def money(*args, **kwargs):
        return {'mode': 'live', 'scope': 'sports', 'capital': None, 'available': None, 'capital_deployed': None, 'rows': []}
    monkeypatch.setattr(api, '_live_portfolio_financial_state_async', money)
    monkeypatch.setattr(api, '_operational_status', lambda mode=None: {'selected_mode': mode})
    monkeypatch.setattr(api, 'live_decision_evidence', lambda data=None: {
        'ok': True, 'summary': {'qualified': 9, 'positive': 12},
        'latest': [{'event_name': 'A v B', 'market_name': 'Match Odds', 'net_roi_pct': 4.0}],
    })
    out = api.sports_overview({'mode': 'live'})
    assert out['highlights'] == []
    assert out['streams']['pre_match']['qualified'] == 0
    assert out['streams']['in_play']['qualified'] == 0
    assert out['decision_qualified_evidence'] == 9


def test_0943_runtime_state_current_release_remains_lightweight(tmp_path):
    api = API(db_path=tmp_path / 'runtime.sqlite3')
    out = api.runtime_state({})
    assert out['ok'] is True
    assert out['version'] == '0.9.43'
    assert set(out) == {'ok', 'version', 'settings', 'background', 'operations'}
    assert 'dashboard' not in out and 'jobs' not in out
    assert set(out['settings']) == {'mode', 'data_context_mode', 'betfair_feed'}


def test_0943_live_pipeline_analytics_never_returns_sim_lifecycle_counts(tmp_path, monkeypatch):
    api = API(db_path=tmp_path / 'pipeline.sqlite3')
    monkeypatch.setattr(api.db, 'scan_pipeline_between', lambda *a, **k: {
        'fetched': 100, 'matched': 40, 'processed': 30, 'opportunities': 8,
        'qualified_observations': 6, 'executed_observations': 4,
        'qualified': 5, 'executed': 3,
    })
    monkeypatch.setattr(api.db, 'execution_failure_reasons_between', lambda *a, **k: [])
    monkeypatch.setattr(api.db, 'discovery_pipeline_between', lambda *a, **k: {'fetched': 120, 'matched': 50})
    monkeypatch.setattr(api.db, 'monitor_timing_metrics', lambda **k: {})
    out = api.pipeline_analytics({'mode': 'live'})
    assert out['mode'] == 'live'
    assert out['pipeline']['qualified'] == 0
    assert out['pipeline']['executed'] == 0
    assert out['pipeline']['decision_qualified_evidence'] == 6
    assert out['pipeline']['decision_executed_evidence'] == 4
    assert out['pipeline']['processed'] == 30


def test_0943_live_market_heatmap_keeps_decision_qualification_diagnostic_only(tmp_path, monkeypatch):
    api = API(db_path=tmp_path / 'heatmap.sqlite3')
    monkeypatch.setattr(api, 'market_heatmap', lambda data=None: {
        'ok': True,
        'cells': [{'date': '2026-08-14', 'hour': 13, 'observations': 10, 'qualified': 99, 'executed': 8}],
        'by_sport': {'Football': [{'date': '2026-08-14', 'hour': 13, 'observations': 10, 'qualified': 99}]},
        'sports': ['Football'],
    })
    monkeypatch.setattr(api.db, 'live_decision_analytics', lambda *a, **k: {
        'hourly': [{'hour_utc': '2026-08-14T13:00:00+00:00', 'qualified': 4}],
        'markets': [],
    })
    # Force UTC so the test hour is deterministic.
    monkeypatch.setattr(api, '_viewer_timezone', lambda data: (timezone.utc, 'UTC'))
    out = api.live_market_heatmap({'from_utc': '2026-08-14T00:00:00+00:00', 'to_utc': '2026-08-15T00:00:00+00:00'})
    assert out['cells'][0]['qualified'] == 0
    assert out['cells'][0]['executed'] == 0
    assert out['cells'][0]['decision_qualified_evidence'] == 4
    assert out['hours'][13]['qualified'] == 0
    assert out['hours'][13]['decision_qualified_evidence'] == 4
    assert out['by_sport']['Football'][0]['qualified'] == 0


def test_0943_live_racing_frontend_uses_actual_only_empty_highlight_copy():
    assert "No authoritative LIVE Racing highlights. Decision evidence is not promoted to Qualified." in HTML
    assert "pipeline_analytics',[{...bounds,mode:'live'}]" in HTML


def test_0943_live_racing_monitor_decision_rows_cannot_become_qualified_filter_rows():
    assert "status:positive?'positive':'rejected'" in HTML
    assert "decision_evidence_only:true" in HTML
    assert "String(st)==='matched'" not in HTML.split('function racingMonitorFiltered0941()',1)[1].split('function renderRacingMonitor0941()',1)[0]


def test_0943_runtime_state_scopes_operations_to_selected_data_mode(tmp_path, monkeypatch):
    api = API(db_path=tmp_path / 'runtime-mode.sqlite3')
    api.db.set_setting('data_context_mode', 'live')
    seen = []
    monkeypatch.setattr(api, '_operational_status', lambda mode=None: seen.append(mode) or {'selected_mode': mode})
    out = api.runtime_state({})
    assert seen == ['live']
    assert out['operations']['selected_mode'] == 'live'


def test_0943_live_dashboard_periodically_refreshes_racing_next_off():
    assert "void loadLiveDashboardActivity0922();void loadDashboardRacingSummary()" in HTML


def test_0943_live_overview_frontend_fail_closed_for_sports_and_racing_highlights():
    sports = HTML.split('function renderSportsOverview0935(r)', 1)[1].split('let sportsRefreshSeq0935', 1)[0]
    assert "high=mode==='live'?[]:(r.highlights||[])" in sports
    assert 'No authoritative LIVE Sports highlights. Decision evidence is not promoted to Qualified.' in sports
    racing = HTML.split('async function loadRacing0941()', 1)[1].split('loadRacing=loadRacing0941', 1)[0]
    assert "(mode==='live'?[]:(r.highlights||[])).map(racingHighlightCard0941)" in racing
