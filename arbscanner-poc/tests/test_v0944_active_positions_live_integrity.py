from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'frontend' / 'index.html').read_text()


def v0944_block():
    return HTML.split('<script id="v0944-active-positions-live-integrity-js">', 1)[1].split('</script>', 1)[0]


def test_0944_release_identity():
    assert __version__ == '0.9.44'
    assert '<title>ArbScanner PoC 0.9.44</title>' in HTML
    assert 'PoC 0.9.44' in HTML


def test_0944_live_execution_activity_remains_actual_only_empty(tmp_path):
    api = API(db_path=tmp_path / 'live.sqlite3')
    out = api.live_execution_activity({'domain': 'all'})
    assert out['ok'] is True
    assert out['mode'] == 'live'
    assert out['rows'] == []
    assert out['metrics']['positions'] == 0
    assert out['live_execution_allowed'] is False
    assert out['orders_write_capability'] is False


def test_0944_active_positions_has_one_payload_owner_for_rows_and_economics():
    block = v0944_block()
    assert 'function applyActivePositionsPayload0944(payload)' in block
    for field in (
        'activeMonitorPositions=Array.isArray(payload.rows)?payload.rows:[]',
        "$('activeBetsCount').textContent=positions.toLocaleString()",
        "$('activeBetsCommitted').textContent=gbp(committed)",
        "$('activeBetsLocked').textContent=lp==null?'—':signedGbp(Number(lp))",
        "$('activeBetsLockedReturn').textContent=lr==null?'—':pct(Number(lr),2)",
        'updateActivePositionsNavCount(positions);renderActivePositions()',
    ):
        assert field in block


def test_0944_live_route_primes_entire_shell_before_async_read_and_commits_with_token():
    block = v0944_block()
    live = block.split('loadLiveActivePositions=async function(){', 1)[1].split('// Clear the whole Active Positions shell', 1)[0]
    assert "let token=modeRequestToken('live','activebets');primeActivePositionsShell0944('live');" in live
    assert "live_execution_activity',[{domain:'all'}]" in live
    assert "modeRequestCurrent(token,true)" in live
    assert "activePageId()!=='activebets'" in live
    assert 'applyActivePositionsPayload0944(activePositionsPayloadFromLive0944(r))' in live


def test_0944_live_empty_shell_clears_all_stale_sim_headline_values_and_badge():
    block = v0944_block()
    prime = block.split("function primeActivePositionsShell0944(mode='live'){", 1)[1].split('function applyActivePositionsPayload0944', 1)[0]
    assert "$('activeBetsCount').textContent='0'" in prime
    assert "$('activeBetsCommitted').textContent=live?'£0.00':'£0.00'" in prime
    assert "$('activeBetsLocked').textContent=live?'—':'£0.00'" in prime
    assert "$('activeBetsLockedReturn').textContent=live?'—':'0.00%'" in prime
    assert "$('dashOpenProfit').hidden=live" in prime
    for field in ('activeFilterAllCount', 'activeFilterPrematchCount', 'activeFilterInplayCount', 'activeFilterRacingCount'):
        assert field in prime
    assert 'No LIVE positions are open.' in prime


def test_0944_live_copy_never_describes_current_live_rows_as_simulated_fills():
    block = v0944_block()
    assert 'Actual LIVE positions awaiting settlement. SIM positions and simulated fills are never used as fallback.' in block
    assert 'actual LIVE execution/fill evidence only; no SIM fills or hedging are used.' in block
    # The SIM copy remains available but the LIVE branch is explicit and separate.
    assert "dataContextMode==='live'?" in block


def test_0944_switch_to_live_clears_active_positions_synchronously_before_base_mode_queue():
    block = v0944_block()
    switch = block.split('setGlobalDataMode=function(mode){', 1)[1].split('</script>', 1)[0] if '</script>' in block else block.split('setGlobalDataMode=function(mode){', 1)[1]
    assert "if(next==='live'&&activePageId()==='activebets')primeActivePositionsShell0944('live')" in switch
    assert 'return __setGlobalDataMode0944.apply(this,arguments)' in switch


def test_0944_sim_dashboard_normalises_active_positions_from_same_response():
    block = v0944_block()
    sim = block.split('const __loadDashboardOverview0944=loadDashboardOverview;', 1)[1].split('// LIVE primes first', 1)[0]
    assert "requested==='sim'&&dataContextMode==='sim'&&r?.ok" in sim
    assert 'applyActivePositionsPayload0944(activePositionsPayloadFromSim0944(r))' in sim
