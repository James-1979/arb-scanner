from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'frontend' / 'index.html').read_text(encoding='utf-8')
API_SOURCE = (ROOT / 'arbscanner' / 'api.py').read_text(encoding='utf-8')


def _market_payload(start: datetime, finish: datetime) -> dict:
    hour = start.isoformat()
    rows = [
        {'section':'sports','sport':'Football','market_name':'Match Odds','in_play':0,'observations':10,'net_positive':4,'qualified':2,'attempts':2,'executed':1,'settled':1},
        {'section':'sports','sport':'Tennis','market_name':'Match Odds','in_play':1,'observations':20,'net_positive':8,'qualified':3,'attempts':3,'executed':2,'settled':2},
        {'section':'racing','sport':'Greyhounds','market_name':'Win','in_play':0,'observations':30,'net_positive':12,'qualified':5,'attempts':4,'executed':3,'settled':3},
    ]
    activity = [{**x, 'hour_utc': hour} for x in rows]
    execution = [{**x, 'hour_utc': hour, 'pnl': 1.0} for x in rows]
    return {
        'history_from_utc': start.isoformat(), 'history_to_utc': finish.isoformat(),
        'rows': rows, 'reasons': rows, 'activity_hours': activity, 'execution_hours': execution,
        'exchange_discovery_rows': [], 'opportunity_venue_rows': [],
        'sports_discovery': {}, 'sports_scans': [], 'racing_discovery': {}, 'racing_scans': [],
        'summary_history_complete': True, 'detailed_history_complete': True,
    }


def test_0953_market_analysis_stream_filter_is_page_wide_and_all_is_unfiltered(tmp_path: Path):
    api = API(tmp_path / 'market.sqlite3')
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    finish = start + timedelta(days=1)
    payload = _market_payload(start, finish)
    api.analytics_store.market_summary = lambda *_a, **_k: payload
    api.db.liquidity_market_summary_between = lambda *_a, **_k: {'depth': [], 'opportunity': []}
    api.db.latest_liquidity_summary = lambda **_k: []
    api._operational_status = lambda *_a, **_k: {'feeds': []}
    base = {'mode':'sim','from_utc':start.isoformat(),'to_utc':finish.isoformat(),'scope':'all','phase':'all','sport':'all','timezone_name':'UTC','timezone_offset_minutes':0}

    selected = api.market_analysis({**base, 'streams':'pre_match,racing'})
    assert selected['ok'] is True
    assert {(x['section'], x['sport']) for x in selected['rows']} == {('sports','Football'), ('racing','Greyhounds')}
    assert selected['liquidity_funnel']['observed'] == 40

    complete = api.market_analysis({**base, 'streams':'pre_match,in_play,racing'})
    legacy = api.market_analysis(base)
    assert [(x['section'], x['sport']) for x in complete['rows']] == [(x['section'], x['sport']) for x in legacy['rows']]
    assert complete['liquidity_funnel']['observed'] == 60
    assert legacy['liquidity_funnel']['observed'] == 60


def test_0953_replay_does_not_manufacture_legacy_engine_identity():
    assert 'pseudo Engine such as "Legacy / Unverified"' in API_SOURCE
    replay_block = API_SOURCE[API_SOURCE.index('# Compact Replay period index'):API_SOURCE.index('return {', API_SOURCE.index('period_activity ='))]
    assert 'engine_id = str(row.get("engine_instance_id") or "")' in replay_block
    assert 'if authoritative:' in replay_block
    assert 'else "legacy_unverified"' not in replay_block

    assert "function replayEngineKey0942(p){return replayEngineId0953" in HTML
    assert "if(!id)continue" in HTML
    assert "No stored Engine provenance" in HTML
    assert "replayActivityEngine0942==='legacy_unverified'" not in HTML
    assert "id==='legacy_unverified'" not in HTML


def test_0953_replay_engine_filter_routes_by_instance_id_not_nickname():
    assert "replayEngineId0953(x)===eng" in HTML
    assert "setReplayEngineOptions0953()" in HTML
    assert "engine=row?replayEngineId0953(row):''" in HTML


def test_0953_market_all_route_omits_stream_filter_and_partial_uses_csv():
    assert "function marketStreamPayload0953()" in HTML
    assert "streams.length===marketHeatmapStreamOrder0952.length?{}" in HTML
    assert "streams:streams.join(',')" in HTML
    assert "...(p.streams?{streams:p.streams.join(',')}:{}),sport:'all'" in HTML
    assert "scope:$('marketAnalysisScope')?.value||'all'" in HTML
    assert "loadMarketAnalysis()}" in HTML
