from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'frontend' / 'index.html').read_text(encoding='utf-8')
API_SOURCE = (ROOT / 'arbscanner' / 'api.py').read_text(encoding='utf-8')
INSTALLER = (ROOT / 'BUILD_AND_INSTALL.command').read_text(encoding='utf-8')
NOTES = (ROOT / 'RELEASE_NOTES.md').read_text(encoding='utf-8')


def _row(hour: str, section: str, sport: str, in_play: int, n: int) -> dict:
    return {
        'hour_utc': hour, 'section': section, 'sport': sport,
        'market_name': 'Match Odds' if section == 'sports' else 'Win', 'in_play': in_play,
        'observations': n, 'unique_markets': n, 'net_positive': n,
    }


def test_0952_release_identity():
    assert __version__ == '0.9.52'
    assert '<title>ArbScanner PoC 0.9.52</title>' in HTML
    assert 'PoC 0.9.52</span>' in HTML
    assert 'EXPECTED_VERSION="0.9.52"' in INSTALLER
    assert '## 0.9.52 — Market Analysis Multi-Stream Closure' in NOTES


def test_0952_heatmap_backend_accepts_true_multi_stream_sets(tmp_path: Path):
    api = API(tmp_path / 'multi.sqlite3')
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    finish = start + timedelta(days=7)
    h = start.isoformat()
    pre = _row(h, 'sports', 'Football', 0, 10)
    ip = _row(h, 'sports', 'Tennis', 1, 20)
    race = _row(h, 'racing', 'Greyhounds', 0, 30)
    api.db.market_heatmap_between = lambda *_a, **_k: {
        'source': 'test', 'financial_source': 'authoritative_sim_ledger',
        'rollups': [pre, ip, race], 'financial': [], 'liquidity_depth': [], 'liquidity_opportunity': [],
    }
    base = {'from_utc': start.isoformat(), 'to_utc': finish.isoformat(), 'scope': 'all', 'phase': 'all', 'sport': 'all', 'timezone_name': 'UTC', 'timezone_offset_minutes': 0}

    selected = api.market_heatmap({**base, 'streams': ['pre_match', 'racing']})
    assert selected['ok'] is True
    cell = next(x for x in selected['cells'] if x['date'] == '2026-08-10' and x['hour'] == 0)
    assert cell['observations'] == 40
    assert set(selected['sports']) == {'Football', 'Greyhounds'}

    all_streams = api.market_heatmap({**base, 'streams': ['pre_match', 'in_play', 'racing']})
    all_cell = next(x for x in all_streams['cells'] if x['date'] == '2026-08-10' and x['hour'] == 0)
    assert all_cell['observations'] == 60
    assert set(all_streams['sports']) == {'Football', 'Tennis', 'Greyhounds'}


def test_0952_stream_ui_has_all_and_multi_select_semantics():
    assert "const marketHeatmapStreamOrder0952=['pre_match','in_play','racing']" in HTML
    assert "marketHeatmapStreams0952=new Set(marketHeatmapStreamOrder0952)" in HTML
    assert 'id="heatmapStreams0952"' in HTML
    assert "setHeatmapStream0948('all')\">All</button>" in HTML
    assert "if(marketHeatmapStreams0952.size===1)return" in HTML
    assert "marketHeatmapStreams0952.delete(s)" in HTML
    assert "marketHeatmapStreams0952.add(s)" in HTML
    assert "marketHeatmapStreams0952=new Set(marketHeatmapStreamOrder0952)" in HTML
    assert 'streams:p.streams' in HTML
    assert "streamKey=p.streams.join(',')" in HTML


def test_0952_backend_stream_classifier_keeps_racing_independent_of_phase():
    assert 'if str(row.get("section") or "").lower() == "racing":' in API_SOURCE
    assert 'return "racing"' in API_SOURCE
    assert 'return "in_play" if int(row.get("in_play") or 0) == 1 else "pre_match"' in API_SOURCE
    assert 'if selected_streams and row_stream(row) not in selected_streams: return False' in API_SOURCE


def test_0952_live_partial_sports_selection_stays_fail_closed(tmp_path: Path):
    api = API(tmp_path / 'live.sqlite3')
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    finish = start + timedelta(days=7)
    h = start.isoformat()
    pre = _row(h, 'sports', 'Football', 0, 10)
    race = _row(h, 'racing', 'Greyhounds', 0, 30)
    api.db.market_heatmap_between = lambda *_a, **_k: {
        'source': 'test', 'financial_source': 'none',
        'rollups': [pre, race], 'financial': [], 'liquidity_depth': [], 'liquidity_opportunity': [],
    }
    api.db.live_decision_analytics = lambda *_a, **_k: {
        'hourly': [{'hour_utc': h, 'qualified': 999}],
        'hourly_by_sport': [{'hour_utc': h, 'sport': 'Football', 'qualified': 999}],
    }
    result = api.live_market_heatmap({
        'from_utc': start.isoformat(), 'to_utc': finish.isoformat(), 'scope': 'all', 'phase': 'all',
        'streams': ['pre_match', 'racing'], 'sport': 'all', 'timezone_name': 'UTC', 'timezone_offset_minutes': 0,
    })
    assert result['ok'] is True
    cell = next(x for x in result['cells'] if x['date'] == '2026-08-10' and x['hour'] == 0)
    assert cell['observations'] == 40
    assert cell['decision_qualified_evidence'] == 0
    assert cell['qualified'] == 0
    assert cell['executed'] == 0
    assert cell['pnl'] == 0
    assert result['live_execution_allowed'] is False
