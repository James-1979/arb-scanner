from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API, DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'frontend' / 'index.html').read_text(encoding='utf-8')
API_SOURCE = (ROOT / 'arbscanner' / 'api.py').read_text(encoding='utf-8')
INSTALLER = (ROOT / 'BUILD_AND_INSTALL.command').read_text(encoding='utf-8')
NOTES = (ROOT / 'RELEASE_NOTES.md').read_text(encoding='utf-8')


def _js() -> str:
    return HTML.split('<script id="v0950-closure-js">', 1)[1].split('</script>', 1)[0]


def _css() -> str:
    return HTML.split('<style id="v0950-closure-css">', 1)[1].split('</style>', 1)[0]


def test_0950_release_identity_and_installer():
    assert __version__ == '0.9.50'
    assert '<title>ArbScanner PoC 0.9.50</title>' in HTML
    assert 'PoC 0.9.50</span>' in HTML
    assert 'EXPECTED_VERSION="0.9.50"' in INSTALLER
    assert '## 0.9.50 — Analytics & Admin Integrity Closure' in NOTES


def test_0950_performance_and_replay_filters_right_align_and_exposure_is_blue():
    css = _css()
    assert '#performanceButtons0948,#replayButtons0948' in css
    assert 'margin-left:auto!important' in css
    assert 'justify-content:flex-end!important' in css
    assert 'stroke:#175cd3!important' in css
    assert '.capital-use-label0947{fill:#175cd3!important}' in css
    assert 'fill:rgba(23,92,211,.12)!important' in css


def test_0950_market_heatmap_all_metric_routes_and_sport_filter(tmp_path: Path):
    api = API(tmp_path / 'route.sqlite3')
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    finish = start + timedelta(days=7)
    h = start.isoformat()
    football = {'hour_utc': h, 'section': 'sports', 'sport': 'Football', 'market_name': 'Match Odds', 'in_play': 0}
    tennis = {'hour_utc': h, 'section': 'sports', 'sport': 'Tennis', 'market_name': 'Match Winner', 'in_play': 0}
    api.db.market_heatmap_between = lambda *_a, **_k: {
        'source': 'test', 'financial_source': 'authoritative_sim_ledger',
        'rollups': [
            {**football, 'observations': 10, 'unique_markets': 2, 'net_positive': 3},
            {**tennis, 'observations': 999, 'unique_markets': 99, 'net_positive': 99},
        ],
        'financial': [
            {**football, 'qualified': 4, 'executed': 3, 'deployed': 100, 'settled': 2, 'settled_deployed': 80, 'pnl': 8},
            {**tennis, 'qualified': 99, 'executed': 99, 'deployed': 999, 'settled': 99, 'settled_deployed': 999, 'pnl': 999},
        ],
        'liquidity_depth': [
            {**football, 'depth_samples': 2, 'top_book_depth_sum': 40, 'top3_depth_sum': 100},
            {**tennis, 'depth_samples': 1, 'top_book_depth_sum': 999, 'top3_depth_sum': 999},
        ],
        'liquidity_opportunity': [
            {**football, 'liquidity_capable': 5, 'liquidity_rejected': 1, 'executable_stake_sum': 60, 'executable_stake_samples': 2},
            {**tennis, 'liquidity_capable': 99, 'liquidity_rejected': 99, 'executable_stake_sum': 999, 'executable_stake_samples': 1},
        ],
    }
    result = api.market_heatmap({
        'from_utc': start.isoformat(), 'to_utc': finish.isoformat(), 'scope': 'sports',
        'phase': 'pre_match', 'sport': 'Football', 'timezone_name': 'UTC', 'timezone_offset_minutes': 0,
    })
    assert result['ok'] is True
    route = result['route_integrity']
    assert route == {'rollup_rows': 1, 'financial_rows': 1, 'liquidity_depth_rows': 1, 'liquidity_opportunity_rows': 1, 'cell_count': 168, 'metric_count': 16}
    cell = next(x for x in result['cells'] if x['date'] == '2026-08-10' and x['hour'] == 0)
    assert cell['observations'] == 10
    assert cell['unique_markets'] == 2
    assert cell['net_positive'] == 3
    assert cell['qualified'] == 4
    assert cell['executed'] == 3
    assert cell['deployed'] == 100
    assert cell['settled'] == 2
    assert cell['settled_deployed'] == 80
    assert cell['pnl'] == 8
    assert cell['roi_pct'] == 10
    assert cell['available_depth'] == 50
    assert cell['top_book_depth'] == 20
    assert cell['avg_executable_stake'] == 30
    assert cell['liquidity_capable'] == 5
    assert cell['liquidity_rejected'] == 1
    assert round(cell['liquidity_rejection_rate_pct'], 4) == 16.6667
    assert set(result['sports']) == {'Football'}


def test_0950_market_analysis_reload_invalidates_week_cache():
    js = _js()
    assert 'const __loadMarketAnalysis0950=loadMarketAnalysis' in js
    assert 'marketHeatmapCache0835?.clear?.()' in js
    assert '__loadMarketAnalysis0950.apply(this,arguments)' in js
    assert "loadLiveMarketHeatmap099=async function(force=false){return loadMarketHeatmapDay0835(force)}" in js
    assert 'sport not in {"", "all"}' in API_SOURCE
    assert 'route_integrity' in API_SOURCE


def test_0950_admin_has_smarkets_account_readiness_slot_and_wider_layout():
    js, css = _js(), _css()
    assert 'function adminSmarketsCard0950' in js
    assert 'Smarkets account API not integrated' in js
    assert 'if(!accounts.smarkets)html+=adminSmarketsCard0950' in js
    assert '#adminAccounts0948 #adminExchangeAccountGrid{grid-template-columns:repeat(3' in css
    assert '#adminAccounts0948 .exchange-account-metrics{grid-template-columns:repeat(2' in css


def test_0950_admin_rehydrates_scanner_alert_account_and_technical_effective_values(tmp_path: Path):
    js = _js()
    for control, key in (
        ('accountRefreshSeconds', 'account_refresh_seconds'), ('scanInterval', 'discovery_interval_seconds'),
        ('priceTick', 'price_scan_tick_seconds'), ('alertDroi', 'alert_min_deployed_roi_pct'),
        ('adminEngineConcurrency0948', 'engine_max_concurrent_runtimes'),
        ('adminSnapshotKeep0948', 'snapshot_legacy_keep_rows'),
        ('adminLiveQuoteAge0948', 'live_decision_max_quote_age_seconds'),
    ):
        assert control in js and key in js
    assert 'function effectiveAdminConfig0950' in js
    assert 'function hydrateAdminValues0950' in js
    api = API(tmp_path / 'effective.sqlite3')
    api.db.set_setting('config', {'discovery_interval_seconds': 77, 'alert_min_profit': 2.5})
    cfg = api.get_state()['settings']['config']
    assert cfg['discovery_interval_seconds'] == 77
    assert cfg['alert_min_profit'] == 2.5
    assert cfg['price_scan_cache_limit'] == DEFAULT_CONFIG['price_scan_cache_limit']
    assert cfg['settlement_poll_seconds'] == DEFAULT_CONFIG['settlement_poll_seconds']


def test_0950_storage_and_admin_grids_use_workspace_cleanly():
    css = _css()
    assert '#adminStorage0948 .admin-section-body0948{grid-template-columns:repeat(2' in css
    assert '#adminScanner0948 .admin-section-body0948>.card' in css
    assert '#adminAlerts0948 .admin-section-body0948>.card' in css
    assert '#adminTechnical0948 .admin-technical-grid0948{grid-template-columns:repeat(2' in css


def test_0950_live_order_writes_remain_locked(tmp_path: Path):
    api = API(tmp_path / 'lock.sqlite3')
    state = api.get_state()
    assert state['settings']['live_execution_available'] is False
    assert all(feed['live_execution_effective'] is False for feed in state['operations']['feeds'])
    assert '"live_order_writes": False' in API_SOURCE
