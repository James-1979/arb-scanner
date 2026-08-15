from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _body_after(marker: str, end_marker: str) -> str:
    return HTML.split(marker, 1)[1].split(end_marker, 1)[0]


def test_monitor_route_is_primed_before_any_route_loader_runs():
    assert "function primeSportsMonitorModeShellStage121" in HTML
    body = _body_after("function orchestrateRouteLoad", "function setGlobalDataMode")
    assert "if(id==='monitor')primeSportsMonitorModeShellStage121(dataContextMode)" in body
    assert body.index("primeSportsMonitorModeShellStage121") < body.index("let loader=routeLoadersStage03")


def test_monitor_prime_clears_mode_owned_rows_lifecycle_and_visible_funnel():
    body = _body_after("function primeSportsMonitorModeShellStage121", "function primeModeOwnedRouteShellStage03")
    assert "matchedAll=[]" in body
    assert "activeMonitorPositions=[]" in body
    assert "sportsEngineLifecycleRows0936=[]" in body
    for metric in ("monitorProcessed", "monitorPositive", "monitorQualified", "monitorExecuted"):
        assert metric in body
    assert "Loading ${mode.toUpperCase()} Sports Monitor" in body


def test_live_monitor_cannot_reuse_sim_engine_lifecycle_totals():
    body = _body_after("function renderMonitorFunnel0936", "function renderMonitor0936")
    assert "ownedLifecycle=mode==='live'?[]:sportsEngineLifecycleRows0936" in body
    assert "if(mode==='live'){qualified=0;executed=0}" in body
    assert "LIVE decision-evidence records" in body


def test_live_monitor_loader_clears_previous_mode_lifecycle_before_read():
    marker = "loadLiveMonitor=async function(domain='sports'){if(domain!=='sports')return __loadLiveMonitor0936(domain);"
    body = _body_after(marker, "function enginePeriodBounds0936")
    assert "matchedAll=[];activeMonitorPositions=[];sportsEngineLifecycleRows0936=[];let r=await liveDecisionRead" in body
    assert body.index("sportsEngineLifecycleRows0936=[]") < body.index("liveDecisionRead")


def test_sim_monitor_still_uses_engine_lifecycle_authority():
    body = _body_after("function renderMonitorFunnel0936", "function renderMonitor0936")
    assert "mode==='live'?[]:sportsEngineLifecycleRows0936" in body
    sim_loader = _body_after("const __loadMonitor0936=loadMonitor;", "const __loadLiveMonitor0936=loadLiveMonitor;")
    assert "await refreshMonitorLifecycle0936()" in sim_loader
